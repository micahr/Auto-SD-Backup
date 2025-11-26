"""Cross-platform device ejection utility."""
import platform
import subprocess
import logging
import asyncio

logger = logging.getLogger(__name__)


async def eject_device(mount_point: str) -> bool:
    """
    Eject a device by its mount point.

    Args:
        mount_point: The path to the device's mount point.

    Returns:
        True if the eject command was successful, False otherwise.
    """
    system = platform.system()
    logger.info(f"Attempting to eject '{mount_point}' on {system}...")

    if system == "Darwin":
        return await _eject_macos(mount_point)
    elif system == "Linux":
        return await _eject_linux(mount_point)
    else:
        logger.warning(f"Auto-eject is not supported on unsupported platform: {system}")
        return False


async def _eject_macos(mount_point: str) -> bool:
    """Eject a device on macOS using 'diskutil'."""
    try:
        # First attempt: Standard eject
        proc = await asyncio.create_subprocess_exec(
            'diskutil', 'eject', mount_point,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info(f"Successfully ejected '{mount_point}'")
            return True
        
        error_message = stderr.decode().strip()
        logger.warning(f"Standard eject failed for '{mount_point}': {error_message}")
        logger.info("Attempting force unmount...")

        # Second attempt: Force unmount
        # unmountDisk is preferred over unmount to handle partitions correctly
        proc_force = await asyncio.create_subprocess_exec(
            'diskutil', 'unmountDisk', 'force', mount_point,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout_force, stderr_force = await proc_force.communicate()

        if proc_force.returncode == 0:
            logger.info(f"Successfully force unmounted '{mount_point}'")
            return True
        else:
            error_message_force = stderr_force.decode().strip()
            logger.error(f"Failed to force eject '{mount_point}': {error_message_force}")
            return False

    except FileNotFoundError:
        logger.error("diskutil command not found. Please ensure it's in your PATH.")
        return False
    except Exception as e:
        logger.error(f"An error occurred while ejecting on macOS: {e}", exc_info=True)
        return False


async def _eject_linux(mount_point: str) -> bool:
    """Eject a device on Linux using 'umount'."""
    try:
        # Use umount, as it's safer and more common than 'eject'
        proc = await asyncio.create_subprocess_exec(
            'umount', mount_point,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info(f"Successfully unmounted '{mount_point}'")
            return True
        else:
            error_message = stderr.decode().strip()
            logger.error(f"Failed to unmount '{mount_point}': {error_message}")
            return False
    except FileNotFoundError:
        logger.error("umount command not found. Please ensure it's in your PATH.")
        return False
    except Exception as e:
        logger.error(f"An error occurred while unmounting on Linux: {e}", exc_info=True)
        return False

# Need to import asyncio for the subprocess calls
import asyncio
