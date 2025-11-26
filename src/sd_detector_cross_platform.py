"""Cross-platform SD Card detection"""
import logging
import asyncio
import platform
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

    def _is_removable_device(self, device) -> bool:
        """Check if device is removable storage"""
        try:
            removable_path = Path(f"/sys/block/{device.sys_name}/removable")
            if removable_path.exists():
                with open(removable_path, 'r') as f:
                    if f.read().strip() == '1':
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

    async def _handle_device_event(self, device, action: str):
        """Handle device add/remove events"""
        try:
            if action == 'add' and self._is_removable_device(device):
                await asyncio.sleep(1)
                mount_point = self._get_mount_point(device)
                if mount_point:
                    sd_card = SDCard(
                        device_name=device.sys_name,
                        mount_point=mount_point,
                        device_path=device.device_node,
                        size=self._get_device_size(device),
                        label=self._get_device_label(device)
                    )
                    self._mounted_cards[device.sys_name] = sd_card
                    logger.info(f"SD card detected: {sd_card.device_name} at {sd_card.mount_point}")
                    if self.on_insert:
                        await self.on_insert(sd_card)
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

    def _device_event_callback(self, device):
        """Callback for pyudev monitor"""
        action = device.action
        if self._running:
            asyncio.create_task(self._handle_device_event(device, action))

    async def _scan_existing_devices(self):
        """Scan for already mounted removable devices"""
        logger.info("Scanning for existing removable devices...")
        try:
            for device in self.context.list_devices(subsystem='block', DEVTYPE='partition'):
                if self._is_removable_device(device):
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
            size, disk_info = await asyncio.gather(
                self._get_volume_size(volume_path),
                self._get_disk_info(volume_name)
            )

            sd_card = SDCard(
                device_name=volume_name,
                mount_point=str(volume_path),
                device_path=str(volume_path),
                size=size,
                label=volume_name
            )

            self._mounted_cards[volume_name] = sd_card

            logger.info(f"✓ Removable volume detected: '{volume_name}'")
            logger.info(f"  Mount point: {volume_path}")
            if disk_info:
                logger.info(f"  Type: {disk_info}")
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
            label=path_obj.name
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
