"""Unraid/SMB client for file backups"""
import logging
import shutil
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
                # Register SMB session
                await asyncio.to_thread(
                    register_session,
                    self.host,
                    username=self.username,
                    password=self.password
                )
                self._session_registered = True
                logger.info(f"Unraid SMB client initialized for {self.host}/{self.share}")
            else:
                logger.info(f"Unraid client using protocol: {self.protocol}")
        except Exception as e:
            logger.error(f"Failed to initialize Unraid client: {e}", exc_info=True)
            raise

    async def close(self):
        """Close connection"""
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
                # Build remote path
                smb_path = f"\\\\{self.host}\\{self.share}\\{self.path}"

                if organize_by_date and relative_path:
                    smb_path = str(Path(smb_path) / relative_path)

                # Create directories
                await asyncio.to_thread(makedirs, smb_path, exist_ok=True)

                # Full file path
                remote_file = str(Path(smb_path) / local_path.name)

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
