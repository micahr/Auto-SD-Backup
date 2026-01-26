"""Unraid/SMB client for file backups"""
import logging
import shutil
import threading
from pathlib import Path
from typing import Optional
from smbprotocol.connection import Connection
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect
from smbclient import register_session, open_file, makedirs
import asyncio

logger = logging.getLogger(__name__)


class UnraidClient:
    """Client for uploading files to Unraid via SMB"""

    def __init__(
        self,
        host: str,
        share: str,
        path: str,
        username: str,
        password: str,
        protocol: str = "smb"
    ):
        self.host = host
        self.share = share
        self.path = path
        self.username = username
        self.password = password
        self.protocol = protocol
        self._session_registered = False

    async def initialize(self):
        """Initialize SMB connection"""
        try:
            if self.protocol == "smb":
                # Validate credentials
                if not self.username or not self.password:
                    raise ValueError("SMB username and password are required")
                
                if self.username == "" or self.password == "":
                    raise ValueError("SMB username and password cannot be empty")
                
                # Debug: Log what credentials we're using (but mask password)
                logger.info(f"Connecting to SMB share {self.host}/{self.share}")
                logger.debug(f"Username: '{self.username}', Password length: {len(self.password) if self.password else 0}")
                
                # Register SMB session with proper authentication
                def _register():
                    # register_session doesn't support domain parameter
                    # Just use username and password
                    register_session(
                        self.host,
                        username=self.username,
                        password=self.password,
                        port=445,
                    )
                
                await asyncio.to_thread(_register)
                self._session_registered = True
                logger.info(f"Unraid SMB client initialized for {self.host}/{self.share}")
            else:
                logger.info(f"Unraid client using protocol: {self.protocol}")
        except Exception as e:
            logger.error(f"Failed to initialize Unraid client: {e}", exc_info=True)
            logger.error(f"Host: {self.host}, Share: {self.share}, Username: {self.username}")
            logger.error("Please verify:")
            logger.error("  1. Username and password are correct")
            logger.error("  2. User has access to the share")
            logger.error("  3. SMB service is running on the server")
            raise

    async def close(self):
        """Close connection and attempt to clear SMB cache in a background thread."""
        if self.protocol == "smb" and self._session_registered:

            def _clear_cache():
                """Target for the cleanup thread."""
                # Suppress smbclient noise during disconnect
                smb_pool_logger = logging.getLogger("smbclient._pool")
                original_level = smb_pool_logger.level

                try:
                    from smbclient import reset_connection_cache

                    # Temporarily set to ERROR to hide WARNING logs + tracebacks
                    smb_pool_logger.setLevel(logging.ERROR)

                    logger.info("Attempting to clear SMB connection cache in background...")
                    # This is a blocking call
                    reset_connection_cache()
                    logger.info("Background SMB connection cache clearing completed.")
                except Exception as e:
                    # This runs in a daemon thread, so we log any errors
                    logger.warning(f"Failed to reset SMB connection cache in background: {e}")
                finally:
                    smb_pool_logger.setLevel(original_level)

            # Run cache clearing in a separate daemon thread.
            # This is "best effort" and will not block shutdown if it hangs.
            cleanup_thread = threading.Thread(target=_clear_cache, daemon=True)
            cleanup_thread.start()

        logger.info("Unraid client closed")

    async def check_connection(self) -> bool:
        """Verify connection to Unraid share"""
        try:
            if self.protocol == "smb":
                # Try to list the root of the share
                smb_path = f"\\\\{self.host}\\{self.share}"
                test_path = Path(smb_path)

                # Use smbclient to test connection
                await asyncio.to_thread(makedirs, str(test_path / self.path), exist_ok=True)
                logger.info("Unraid share connection verified")
                return True
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Unraid share: {e}")
            return False

    async def upload_file(
        self,
        local_path: Path,
        relative_path: str,
        organize_by_date: bool = True
    ) -> Optional[str]:
        """
        Upload a file to Unraid

        Args:
            local_path: Local file path
            relative_path: Relative path for organization (e.g., "2025/11/16")
            organize_by_date: Whether to organize by date

        Returns:
            Remote path where file was uploaded, or None on failure
        """
        try:
            if not local_path.exists():
                logger.error(f"File not found: {local_path}")
                return None

            if self.protocol == "smb":
                # Build remote path using string joining to ensure consistent backslashes for UNC
                remote_parts = [f"\\\\{self.host}\\{self.share}", self.path]

                if organize_by_date and relative_path:
                    # Ensure the date-based path also uses backslashes
                    remote_parts.append(relative_path.replace('/', '\\'))
                
                remote_dir = "\\".join(remote_parts)

                # Create directories
                await asyncio.to_thread(makedirs, remote_dir, exist_ok=True)

                # Full file path
                remote_file = f"{remote_dir}\\{local_path.name}"

                # Copy file
                await self._copy_file_smb(local_path, remote_file)

                logger.info(f"Successfully uploaded {local_path.name} to {remote_file}")
                return remote_file

            elif self.protocol in ["nfs", "local"]:
                # For local/NFS, use direct file operations
                remote_path = Path(self.path)

                if organize_by_date and relative_path:
                    remote_path = remote_path / relative_path

                remote_path.mkdir(parents=True, exist_ok=True)
                remote_file = remote_path / local_path.name

                await asyncio.to_thread(shutil.copy2, local_path, remote_file)

                logger.info(f"Successfully uploaded {local_path.name} to {remote_file}")
                return str(remote_file)

            return None

        except Exception as e:
            logger.error(f"Error uploading {local_path} to Unraid: {e}", exc_info=True)
            return None

    async def _copy_file_smb(self, local_path: Path, remote_path: str):
        """Copy file using SMB protocol"""
        def _copy():
            with open(local_path, 'rb') as src:
                with open_file(remote_path, mode='wb') as dst:
                    shutil.copyfileobj(src, dst)

        await asyncio.to_thread(_copy)

    async def verify_file(self, remote_path: str, expected_size: int) -> bool:
        """Verify file exists and has correct size"""
        try:
            if self.protocol == "smb":
                def _check():
                    from smbclient import stat
                    file_stat = stat(remote_path)
                    return file_stat.st_size == expected_size

                result = await asyncio.to_thread(_check)
                return result

            elif self.protocol in ["nfs", "local"]:
                path = Path(remote_path)
                return path.exists() and path.stat().st_size == expected_size

            return False

        except Exception as e:
            logger.error(f"Error verifying file {remote_path}: {e}")
            return False

    async def check_space(self, required_bytes: int) -> bool:
        """Check if destination has enough space"""
        try:
            check_path = self.path
            
            # If we have a mount point configured (for NFS/Local/Mounted SMB), check that
            if self.protocol in ["nfs", "local"] or (self.protocol == "smb" and self.mount_point):
                 if self.mount_point:
                     check_path = self.mount_point
                 
                 if not Path(check_path).exists():
                     return True # Cannot check if path doesn't exist locally

                 total, used, free = shutil.disk_usage(check_path)
                 if free < required_bytes:
                     logger.error(f"Not enough space on {check_path}. Required: {required_bytes}, Free: {free}")
                     return False
                 return True
                 
            # For pure SMB without mount point, assume True (checking quota is complex)
            return True
        except Exception as e:
            logger.warning(f"Failed to check disk space: {e}")
            return True
