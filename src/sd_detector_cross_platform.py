"""Cross-platform SD Card detection"""
import logging
import asyncio
import platform
import subprocess
from pathlib import Path
from typing import Optional, Callable, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SDCard:
    """Represents a detected SD card"""
    device_name: str
    mount_point: str
    device_path: str
    size: int = 0
    label: Optional[str] = None
    device_id: Optional[str] = None


# Try to import platform-specific modules
try:
    import pyudev
    PYUDEV_AVAILABLE = True
except ImportError:
    PYUDEV_AVAILABLE = False
    logger.warning("pyudev not available, falling back to directory monitoring")

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False


class LinuxSDCardDetector:
    """Linux SD card detector using pyudev"""

    def __init__(self, on_insert: Optional[Callable] = None, on_remove: Optional[Callable] = None):
        if not PYUDEV_AVAILABLE:
            raise RuntimeError("pyudev is required for Linux SD card detection")

        self.context = pyudev.Context()
        self.monitor = pyudev.Monitor.from_netlink(self.context)
        self.monitor.filter_by(subsystem='block')
        self.on_insert = on_insert
        self.on_remove = on_remove
        self._running = False
        self._mounted_cards: dict[str, SDCard] = {}
        # Capture the running loop for thread-safe scheduling
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            # Fallback if initialized outside of a running loop (unlikely in this app structure)
            self.loop = asyncio.new_event_loop()

    def _check_removable(self, device) -> bool:
        """
        Check if device is removable storage using multiple heuristics.
        Returns True if device appears to be a removable SD card/USB drive.
        """
        try:
            # 1. Check sysfs 'removable' flag
            removable_path = Path(f"/sys/block/{device.sys_name}/removable")
            if removable_path.exists():
                with open(removable_path, 'r') as f:
                    if f.read().strip() == '1':
                        return True

            # 2. Check udev properties commonly associated with SD cards/USB
            if device.get('ID_DRIVE_FLASH_SD') == '1':
                return True
            if device.get('ID_BUS') == 'mmc':
                return True
            
            # 3. Check parent device if this is a partition (e.g., sdb1 -> sdb)
            if device.parent:
                parent = device.parent
                # Check parent's sysfs flag
                parent_removable = Path(f"/sys/block/{parent.sys_name}/removable")
                if parent_removable.exists():
                    with open(parent_removable, 'r') as f:
                        if f.read().strip() == '1':
                            return True
                
                # Check parent's udev properties
                if parent.get('ID_DRIVE_FLASH_SD') == '1':
                    return True
                if parent.get('ID_BUS') == 'mmc':
                    return True

            return False
        except Exception as e:
            logger.debug(f"Error checking if device {device.sys_name} is removable: {e}")
            return False

    def _get_mount_point(self, device) -> Optional[str]:
        """Get mount point for a device"""
        try:
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == device.device_node:
                        return parts[1]
            return None
        except Exception as e:
            logger.debug(f"Error getting mount point for {device.device_node}: {e}")
            return None

    def _get_device_label(self, device) -> Optional[str]:
        """Get device label if available"""
        try:
            return device.get('ID_FS_LABEL') or device.get('ID_FS_UUID')
        except:
            return None

    def _get_device_size(self, device) -> int:
        """Get device size in bytes"""
        try:
            size_path = Path(f"/sys/block/{device.sys_name}/size")
            if size_path.exists():
                with open(size_path, 'r') as f:
                    return int(f.read().strip()) * 512
            return 0
        except:
            return 0

    def _get_device_uuid(self, device) -> Optional[str]:
        """
        Get the filesystem UUID for a partition, trying three methods in order:

        1. udev ID_FS_UUID property — fastest, no subprocess needed.
        2. blkid subprocess — reads from the kernel/udev blkid cache.
        3. Direct ExFAT boot sector read — works when udev/blkid don't support
           ExFAT UUID extraction (common on older OrangePi kernels). Requires
           the service user to be in the 'disk' group:
               sudo usermod -aG disk <username>
        """
        # 1. udev property
        uuid = device.get('ID_FS_UUID')
        if uuid:
            return uuid

        # 2. blkid subprocess
        try:
            result = subprocess.run(
                ['blkid', '-o', 'value', '-s', 'UUID', device.device_node],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                uuid = result.stdout.strip()
                if uuid:
                    logger.debug(f"Got UUID via blkid for {device.device_node}: {uuid}")
                    return uuid
        except Exception as e:
            logger.debug(f"blkid unavailable for {device.device_node}: {e}")

        # 3. Direct ExFAT boot sector read.
        # ExFAT VolumeSerialNumber sits at byte offset 100 (0x64) in the boot
        # sector and is the source of the XXXX-XXXX UUID that blkid normally
        # reports.  We verify the "EXFAT   " signature at offset 3 first.
        try:
            with open(device.device_node, 'rb') as f:
                f.seek(3)
                if f.read(8) == b'EXFAT   ':
                    f.seek(100)
                    data = f.read(4)
                    if len(data) == 4:
                        serial = int.from_bytes(data, 'little')
                        if serial == 0:
                            logger.debug(f"ExFAT VolumeSerialNumber is zero for {device.device_node}, skipping")
                        else:
                            uuid = f'{serial >> 16:04X}-{serial & 0xFFFF:04X}'
                            logger.debug(f"Got ExFAT UUID via boot sector for {device.device_node}: {uuid}")
                            return uuid
        except PermissionError:
            logger.warning(
                f"Cannot read {device.device_node} to extract ExFAT UUID — "
                f"two cards with the same volume label will be treated as the "
                f"same device until the service user is added to the disk group: "
                f"  sudo usermod -aG disk <service-username>"
            )
        except Exception as e:
            logger.debug(f"Boot sector UUID read failed for {device.device_node}: {e}")

        return None

    async def _handle_device_event(self, device, action):
        """Handle device add/remove events"""
        try:
            logger.debug(f"Handling device event: action={action}, device={getattr(device, 'sys_name', 'unknown')}")

            if action in ('add', 'change'):
                is_removable = self._check_removable(device)
                logger.debug(f"Device {getattr(device, 'sys_name', 'unknown')} removable: {is_removable}")

                if is_removable:
                    # A 'change' event on a whole-disk device (e.g. sdb) means media was
                    # inserted into a permanently-connected card reader.  The mount point
                    # lives on a partition (e.g. sdb1), so recurse into child partitions.
                    if device.get('DEVTYPE') == 'disk':
                        logger.debug(f"Disk device {device.sys_name} changed, scanning partitions...")
                        # Small delay so the kernel has time to create partition entries
                        await asyncio.sleep(1)
                        for child in device.children:
                            if child.get('DEVTYPE') == 'partition':
                                await self._handle_device_event(child, 'add')
                        return

                    # If already tracked, verify it's still the same card by comparing
                    # mount points. When a card is ejected via umount (not physically
                    # removed), there is no udev 'remove' event, so _mounted_cards retains
                    # the stale entry. A different (or absent) mount point means the card
                    # was swapped — clear the stale entry and handle the new card.
                    if device.sys_name in self._mounted_cards:
                        stored_mount = self._mounted_cards[device.sys_name].mount_point
                        current_mount = self._get_mount_point(device)
                        if current_mount == stored_mount:
                            logger.debug(f"Device {device.sys_name} already tracked, skipping")
                            return
                        logger.debug(f"Device {device.sys_name} stale entry cleared ({stored_mount} → {current_mount!r})")
                        old_card = self._mounted_cards.pop(device.sys_name)
                        if self.on_remove:
                            await self.on_remove(old_card)
                        # Fall through to handle the new card below

                    logger.debug("Waiting for mount...")

                    # Retry loop for slow auto-mounters
                    mount_point = None
                    for i in range(5):
                        mount_point = self._get_mount_point(device)
                        if mount_point:
                            break
                        await asyncio.sleep(1)
                        logger.debug(f"Retry {i+1}/5: Waiting for mount point...")

                    logger.debug(f"Mount point for {getattr(device, 'device_node', 'unknown')}: {mount_point}")

                    if mount_point:
                        device_id = self._get_device_uuid(device)
                        if not device_id:
                            # UUID unavailable — compose from reader serial + volume label.
                            # The mount point basename is the ExFAT volume label as reported
                            # by systemd-mount, even when udev doesn't expose ID_FS_LABEL.
                            reader_serial = device.get('ID_SERIAL') or device.sys_name
                            fs_label = device.get('ID_FS_LABEL') or Path(mount_point).name
                            if fs_label:
                                device_id = f"{reader_serial}:{fs_label}"
                                logger.warning(
                                    f"No unique filesystem UUID for {device.device_node}. "
                                    f"Using serial+label as device ID ({device_id!r}). "
                                    f"Cards with identical labels will be treated as the same device."
                                )
                            else:
                                device_id = reader_serial
                                logger.warning(
                                    f"No unique filesystem UUID or label for {device.device_node}. "
                                    f"All cards in this reader share device ID ({device_id!r})."
                                )
                        logger.debug(f"Device {device.sys_name} device_id={device_id!r}")
                        sd_card = SDCard(
                            device_name=device.sys_name,
                            mount_point=mount_point,
                            device_path=device.device_node,
                            size=self._get_device_size(device),
                            label=self._get_device_label(device),
                            device_id=device_id
                        )
                        self._mounted_cards[device.sys_name] = sd_card
                        logger.info(f"SD card detected: {sd_card.device_name} at {sd_card.mount_point}")
                        if self.on_insert:
                            await self.on_insert(sd_card)
                    else:
                        logger.warning(f"Device {getattr(device, 'sys_name', 'unknown')} ({getattr(device, 'device_node', 'unknown')}) detected but not mounted after 5 seconds.")
                        logger.warning("Please ensure your OS has auto-mounting enabled (e.g., usbmount, udisks2).")
            elif action == 'remove':
                if device.sys_name in self._mounted_cards:
                    sd_card = self._mounted_cards.pop(device.sys_name)
                    logger.info(f"SD card removed: {sd_card.device_name}")
                    if self.on_remove:
                        await self.on_remove(sd_card)
        except Exception as e:
            logger.error(f"Error handling device event: {e}", exc_info=True)

    async def start(self):
        """Start monitoring for SD card events"""
        self._running = True
        logger.info("Linux SD card detector started")
        await self._scan_existing_devices()
        observer = pyudev.MonitorObserver(self.monitor, self._device_event_callback)
        observer.start()
        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            observer.stop()
            logger.info("SD card detector stopped")

    def _device_event_callback(self, action, device):
        """Callback for pyudev monitor"""
        try:
            if self._running:
                asyncio.run_coroutine_threadsafe(
                    self._handle_device_event(device, action),
                    self.loop
                )
        except Exception as e:
            logger.error(f"Error in pyudev callback: {e}")

    async def _scan_existing_devices(self):
        """Scan for already mounted removable devices"""
        logger.info("Scanning for existing removable devices...")
        try:
            for device in self.context.list_devices(subsystem='block', DEVTYPE='partition'):
                if self._check_removable(device):
                    await self._handle_device_event(device, 'add')
        except Exception as e:
            logger.error(f"Error scanning existing devices: {e}", exc_info=True)

    async def stop(self):
        """Stop monitoring"""
        self._running = False

    def get_mounted_cards(self) -> List[SDCard]:
        """Get list of currently mounted SD cards"""
        return list(self._mounted_cards.values())


class MacOSSDCardDetector:
    """macOS SD card detector using /Volumes directory monitoring and diskutil"""

    def __init__(self, on_insert: Optional[Callable] = None, on_remove: Optional[Callable] = None):
        self.on_insert = on_insert
        self.on_remove = on_remove
        self._running = False
        self._mounted_cards: dict[str, SDCard] = {}
        self._volumes_path = Path("/Volumes")
        self._observer = None
        self._known_volumes = set()

    async def start(self):
        """Start monitoring /Volumes for new mounts"""
        self._running = True
        logger.info("macOS SD card detector started")
        logger.info("Monitoring: /Volumes directory and diskutil for removable media")
        logger.info("This includes built-in SD card readers and external USB drives")

        # Get initial volumes
        self._known_volumes = set(await self._get_removable_volumes())
        logger.info(f"Initial removable volumes: {self._known_volumes}")

        # Start monitoring
        while self._running:
            try:
                await self._check_volumes()
                await asyncio.sleep(2)  # Check every 2 seconds
            except asyncio.CancelledError:
                break # Exit loop cleanly on cancellation
            except Exception as e:
                logger.error(f"Error in macOS detector loop: {e}", exc_info=True)
                await asyncio.sleep(5) # Wait a bit longer after an error

    async def _check_volumes(self):
        """Check for new or removed volumes"""
        current_volumes = set(await self._get_removable_volumes())

        # Check for new volumes
        new_volumes = current_volumes - self._known_volumes
        for volume_name in new_volumes:
            volume_path = self._volumes_path / volume_name
            if volume_path.exists() and volume_path.is_dir():
                await self._handle_volume_added(volume_name, volume_path)

        # Check for removed volumes
        removed_volumes = self._known_volumes - current_volumes
        for volume_name in removed_volumes:
            await self._handle_volume_removed(volume_name)

        self._known_volumes = current_volumes

    async def _get_removable_volumes(self) -> List[str]:
        """Get list of removable volumes using diskutil in a non-blocking way."""
        removable_volumes = []
        try:
            proc = await asyncio.create_subprocess_exec(
                'diskutil', 'list', '-plist', 'external',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode == 0 and stdout:
                import plistlib
                plist_data = plistlib.loads(stdout)

                for disk in plist_data.get('AllDisksAndPartitions', []):
                    for partition in disk.get('Partitions', []):
                        mount_point = partition.get('MountPoint', '')
                        if mount_point and mount_point.startswith('/Volumes/'):
                            volume_name = mount_point.replace('/Volumes/', '')
                            if volume_name:
                                removable_volumes.append(volume_name)
                                logger.debug(f"Found removable volume via diskutil: {volume_name}")
            elif stderr:
                logger.debug(f"diskutil command failed: {stderr.decode().strip()}")

        except asyncio.TimeoutError:
            logger.debug("diskutil command timed out, will rely on directory listing fallback.")
        except FileNotFoundError:
            logger.debug("diskutil command not found, will rely on directory listing fallback.")
        except Exception as e:
            logger.debug(f"diskutil detection failed, falling back to directory listing: {e}")

        # Always combine with fallback for robustness (e.g., for non-standard removable devices)
        fallback_volumes = self._get_volumes_fallback()
        
        # Combine and remove duplicates
        combined_volumes = set(removable_volumes) | set(fallback_volumes)
        return list(combined_volumes)

    def _get_volumes_fallback(self) -> List[str]:
        """Fallback method: Get volumes by listing /Volumes directory"""
        if not self._volumes_path.exists():
            return []

        volumes = []
        for item in self._volumes_path.iterdir():
            if item.is_dir() and item.name not in ['Macintosh HD', 'Preboot', 'Recovery', 'VM', 'Data']:
                volumes.append(item.name)
                logger.debug(f"Found volume via directory listing: {item.name}")
        return volumes

    async def _handle_volume_added(self, volume_name: str, volume_path: Path):
        """Handle new volume detected"""
        try:
            # Get volume size and info using non-blocking async calls
            size, disk_info, uuid = await asyncio.gather(
                self._get_volume_size(volume_path),
                self._get_disk_info(volume_name),
                self._get_volume_uuid(volume_name)
            )

            sd_card = SDCard(
                device_name=volume_name,
                mount_point=str(volume_path),
                device_path=str(volume_path),
                size=size,
                label=volume_name,
                device_id=uuid or volume_name  # Fallback to volume name if UUID not found
            )

            self._mounted_cards[volume_name] = sd_card

            logger.info(f"✓ Removable volume detected: '{volume_name}'")
            logger.info(f"  Mount point: {volume_path}")
            if disk_info:
                logger.info(f"  Type: {disk_info}")
            if uuid:
                logger.info(f"  UUID: {uuid}")
            if size > 0:
                logger.info(f"  Size: {self._format_size(size)}")

            if self.on_insert:
                await self.on_insert(sd_card)

        except Exception as e:
            logger.error(f"Error handling volume added: {e}", exc_info=True)

    async def _handle_volume_removed(self, volume_name: str):
        """Handle volume removed"""
        if volume_name in self._mounted_cards:
            sd_card = self._mounted_cards.pop(volume_name)
            logger.info(f"✗ Volume removed: '{volume_name}'")

            if self.on_remove:
                await self.on_remove(sd_card)

    async def _get_volume_size(self, volume_path: Path) -> int:
        """Get volume size using du command asynchronously"""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                'du', '-sk', str(volume_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            
            if proc.returncode == 0 and stdout:
                # du returns size in KB
                size_kb = int(stdout.decode().split()[0])
                return size_kb * 1024
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if proc:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            if isinstance(Exception, asyncio.CancelledError):
                raise
        except Exception:
            pass
        return 0

    async def _get_volume_uuid(self, volume_name: str) -> Optional[str]:
        """Get volume UUID using diskutil asynchronously"""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                'diskutil', 'info', f'/Volumes/{volume_name}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode == 0 and stdout:
                output = stdout.decode()
                for line in output.split('\n'):
                    if 'Volume UUID:' in line:
                        return line.split(':', 1)[1].strip()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if proc:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            if isinstance(Exception, asyncio.CancelledError):
                raise
        except Exception:
            pass
        return None

    async def _get_disk_info(self, volume_name: str) -> Optional[str]:
        """Get disk type info using diskutil asynchronously"""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                'diskutil', 'info', f'/Volumes/{volume_name}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

            if proc.returncode == 0 and stdout:
                output = stdout.decode()
                for line in output.split('\n'):
                    if 'Protocol:' in line:
                        return line.split(':', 1)[1].strip()
                    elif 'Device / Media Name:' in line:
                        return line.split(':', 1)[1].strip()
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if proc:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            if isinstance(Exception, asyncio.CancelledError):
                raise
        except Exception:
            pass
        return None

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human readable size"""
        if size_bytes == 0: return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

    async def stop(self):
        """Stop monitoring"""
        self._running = False

    def get_mounted_cards(self) -> List[SDCard]:
        """Get list of currently mounted volumes"""
        return list(self._mounted_cards.values())


class DevSimulator:
    """Development simulator for manual testing"""

    def __init__(self, on_insert: Optional[Callable] = None, on_remove: Optional[Callable] = None):
        self.on_insert = on_insert
        self.on_remove = on_remove
        self._running = False
        self._mounted_cards: dict[str, SDCard] = {}

    async def start(self):
        """Start simulator (does nothing, use trigger_insert manually)"""
        self._running = True
        logger.info("Development simulator started - use CLI to trigger backups")
        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        """Stop simulator"""
        self._running = False

    async def trigger_insert(self, path: str):
        """Manually trigger an SD card insertion event"""
        path_obj = Path(path)
        if not path_obj.exists():
            logger.error(f"Path does not exist: {path}")
            return

        sd_card = SDCard(
            device_name=path_obj.name,
            mount_point=str(path_obj),
            device_path=str(path_obj),
            size=self._get_dir_size(path_obj),
            label=path_obj.name,
            device_id=path_obj.name
        )

        self._mounted_cards[path_obj.name] = sd_card
        logger.info(f"Simulated SD card insert: {path}")

        if self.on_insert:
            await self.on_insert(sd_card)

    def _get_dir_size(self, path: Path) -> int:
        """Get total size of directory"""
        try:
            total = 0
            for item in path.rglob('*'):
                if item.is_file():
                    total += item.stat().st_size
            return total
        except:
            return 0

    def get_mounted_cards(self) -> List[SDCard]:
        """Get list of simulated cards"""
        return list(self._mounted_cards.values())


def create_detector(on_insert: Optional[Callable] = None, on_remove: Optional[Callable] = None, mode: str = "auto"):
    """
    Create appropriate SD card detector based on platform

    Args:
        on_insert: Callback for SD card insertion
        on_remove: Callback for SD card removal
        mode: Detection mode - "auto", "linux", "macos", "dev"

    Returns:
        SD card detector instance
    """
    if mode == "dev":
        logger.info("Using development simulator")
        return DevSimulator(on_insert, on_remove)

    if mode == "auto":
        system = platform.system()
        if system == "Linux":
            mode = "linux"
        elif system == "Darwin":
            mode = "macos"
        else:
            logger.warning(f"Unsupported platform {system}, using dev simulator")
            mode = "dev"

    if mode == "linux":
        if not PYUDEV_AVAILABLE:
            logger.error("pyudev not available, falling back to dev simulator")
            return DevSimulator(on_insert, on_remove)
        return LinuxSDCardDetector(on_insert, on_remove)

    elif mode == "macos":
        return MacOSSDCardDetector(on_insert, on_remove)

    else:
        return DevSimulator(on_insert, on_remove)
