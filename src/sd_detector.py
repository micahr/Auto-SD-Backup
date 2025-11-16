"""SD Card detection using pyudev"""
import pyudev
import logging
import asyncio
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


class SDCardDetector:
    """Monitors for SD card insertion and removal"""

    def __init__(self, on_insert: Optional[Callable] = None, on_remove: Optional[Callable] = None):
        self.context = pyudev.Context()
        self.monitor = pyudev.Monitor.from_netlink(self.context)
        self.monitor.filter_by(subsystem='block')
        self.on_insert = on_insert
        self.on_remove = on_remove
        self._running = False
        self._mounted_cards: dict[str, SDCard] = {}

    def _is_removable_device(self, device: pyudev.Device) -> bool:
        """Check if device is removable storage"""
        try:
            # Check if device is removable
            removable_path = Path(f"/sys/block/{device.sys_name}/removable")
            if removable_path.exists():
                with open(removable_path, 'r') as f:
                    if f.read().strip() == '1':
                        return True
            return False
        except Exception as e:
            logger.debug(f"Error checking if device {device.sys_name} is removable: {e}")
            return False

    def _get_mount_point(self, device: pyudev.Device) -> Optional[str]:
        """Get mount point for a device"""
        try:
            # Try to read from /proc/mounts
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == device.device_node:
                        return parts[1]
            return None
        except Exception as e:
            logger.debug(f"Error getting mount point for {device.device_node}: {e}")
            return None

    def _get_device_label(self, device: pyudev.Device) -> Optional[str]:
        """Get device label if available"""
        try:
            # Try to get label from udev properties
            return device.get('ID_FS_LABEL') or device.get('ID_FS_UUID')
        except:
            return None

    def _get_device_size(self, device: pyudev.Device) -> int:
        """Get device size in bytes"""
        try:
            size_path = Path(f"/sys/block/{device.sys_name}/size")
            if size_path.exists():
                with open(size_path, 'r') as f:
                    # Size is in 512-byte blocks
                    return int(f.read().strip()) * 512
            return 0
        except:
            return 0

    async def _handle_device_event(self, device: pyudev.Device, action: str):
        """Handle device add/remove events"""
        try:
            if action == 'add' and self._is_removable_device(device):
                # Wait a moment for the device to be mounted
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
        logger.info("SD card detector started")

        # Check for already mounted removable devices
        await self._scan_existing_devices()

        # Monitor for new events
        observer = pyudev.MonitorObserver(self.monitor, self._device_event_callback)
        observer.start()

        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            observer.stop()
            logger.info("SD card detector stopped")

    def _device_event_callback(self, device):
        """Callback for pyudev monitor (runs in separate thread)"""
        action = device.action
        # Schedule the async handler in the event loop
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
