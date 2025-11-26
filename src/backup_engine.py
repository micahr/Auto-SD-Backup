"""Backup engine for orchestrating file backups"""
import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Callable
from datetime import datetime
import uuid

from .database import BackupDatabase, calculate_file_hash
from .immich_client import ImmichClient
from .unraid_client import UnraidClient
from .sd_detector_cross_platform import SDCard
from .config import Config

logger = logging.getLogger(__name__)


class BackupEngine:
    """Main backup orchestration engine"""

    def __init__(
        self,
        config: Config,
        database: BackupDatabase,
        immich_client: Optional[ImmichClient] = None,
        unraid_client: Optional[UnraidClient] = None,
        progress_callback: Optional[Callable] = None
    ):
        self.config = config
        self.database = database
        self.immich_client = immich_client
        self.unraid_client = unraid_client
        self.progress_callback = progress_callback
        self._current_session_id: Optional[str] = None
        self._semaphore = asyncio.Semaphore(config.backup.concurrent_files)

    async def start_backup(self, sd_card: SDCard) -> str:
        """
        Start backup process for an SD card

        Returns:
            Session ID for tracking
        """
        session_id = str(uuid.uuid4())
        self._current_session_id = session_id

        logger.info(f"Starting backup session {session_id} for {sd_card.device_name}")

        try:
            # Scan SD card for files
            files_to_backup = await self._scan_sd_card(sd_card)

            if not files_to_backup:
                logger.info("No new files to backup")
                await self.database.create_session({
                    'session_id': session_id,
                    'device_name': sd_card.device_name,
                    'device_path': sd_card.device_path,
                    'status': 'completed',
                    'total_files': 0,
                    'total_bytes': 0
                })
                return session_id

            # Calculate totals
            total_files = len(files_to_backup)
            total_bytes = sum(f['file_size'] for f in files_to_backup)

            # Create backup session
            await self.database.create_session({
                'session_id': session_id,
                'device_name': sd_card.device_name,
                'device_path': sd_card.device_path,
                'mount_point': sd_card.mount_point,
                'status': 'backing_up',
                'total_files': total_files,
                'total_bytes': total_bytes
            })

            logger.info(f"Found {total_files} files to backup ({total_bytes} bytes)")

            # Start backup process
            asyncio.create_task(self._backup_files(session_id, files_to_backup))

            return session_id

        except Exception as e:
            logger.error(f"Error starting backup: {e}", exc_info=True)
            await self.database.update_session(session_id, status='failed')
            raise

    async def _scan_sd_card(self, sd_card: SDCard) -> List[dict]:
        """
        Scan SD card for files to backup

        Returns:
            List of file information dictionaries
        """
        files_to_backup = []
        mount_point = Path(sd_card.mount_point)

        logger.info(f"Scanning {mount_point} for files...")

        try:
            # Walk through all files on SD card
            for file_path in mount_point.rglob('*'):
                if not file_path.is_file():
                    continue

                # Check file extension
                if not self._should_backup_file(file_path):
                    continue

                # Check file size
                file_size = file_path.stat().st_size
                if file_size < self.config.files.min_size:
                    continue

                # Calculate hash
                logger.debug(f"Hashing {file_path.name}...")
                file_hash = await asyncio.to_thread(calculate_file_hash, file_path)

                # Check if file already backed up
                if await self.database.file_exists(file_hash, sd_card.device_name):
                    logger.debug(f"File {file_path.name} already backed up, skipping")
                    continue

                # Get file creation date for organization
                file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                backup_date = file_mtime.strftime('%Y/%m/%d')

                files_to_backup.append({
                    'file_path': str(file_path),
                    'file_name': file_path.name,
                    'file_size': file_size,
                    'md5_hash': file_hash,
                    'source_device': sd_card.device_name,
                    'status': 'new',
                    'backup_date': backup_date,
                    'created_at': file_mtime
                })

        except Exception as e:
            logger.error(f"Error scanning SD card: {e}", exc_info=True)

        return files_to_backup

    def _should_backup_file(self, file_path: Path) -> bool:
        """Check if file should be backed up based on extension"""
        ext = file_path.suffix.lower()
        return ext in [e.lower() for e in self.config.files.extensions]

    async def _backup_files(self, session_id: str, files: List[dict]):
        """Backup files with parallel processing"""
        completed = 0
        failed = 0
        transferred_bytes = 0

        try:
            # Process files with concurrency limit
            tasks = []
            for file_info in files:
                task = self._backup_single_file(session_id, file_info)
                tasks.append(task)

            # Process with progress tracking
            for coro in asyncio.as_completed(tasks):
                success, bytes_transferred = await coro

                if success:
                    completed += 1
                    transferred_bytes += bytes_transferred
                else:
                    failed += 1

                # Update session progress
                await self.database.update_session(
                    session_id,
                    completed_files=completed,
                    failed_files=failed,
                    transferred_bytes=transferred_bytes
                )

                # Call progress callback
                if self.progress_callback:
                    await self.progress_callback(session_id, completed, failed, len(files))

            # Mark session as completed
            final_status = 'completed' if failed == 0 else 'completed_with_errors'
            await self.database.update_session(session_id, status=final_status)

            logger.info(
                f"Backup session {session_id} finished: "
                f"{completed} completed, {failed} failed"
            )

        except Exception as e:
            logger.error(f"Error during backup: {e}", exc_info=True)
            await self.database.update_session(session_id, status='failed')

    async def _backup_single_file(self, session_id: str, file_info: dict) -> tuple[bool, int]:
        """
        Backup a single file to configured destinations

        Returns:
            Tuple of (success, bytes_transferred)
        """
        async with self._semaphore:
            file_path = Path(file_info['file_path'])
            file_id = None

            try:
                # Add file to database
                file_id = await self.database.add_file(file_info)

                # Update status to backing_up
                await self.database.update_file_status(file_id, 'backing_up')

                logger.info(f"Backing up {file_info['file_name']}...")

                # Prepare upload tasks
                upload_tasks = []

                if self.config.immich.enabled and self.immich_client:
                    upload_tasks.append(('immich', self._upload_to_immich(file_path, file_info)))

                if self.config.unraid.enabled and self.unraid_client:
                    upload_tasks.append(('unraid', self._upload_to_unraid(file_path, file_info)))

                # Execute uploads (parallel if configured)
                if self.config.backup.parallel:
                    results = await asyncio.gather(*[task for _, task in upload_tasks], return_exceptions=True)
                else:
                    results = []
                    for _, task in upload_tasks:
                        results.append(await task)

                # Process results
                immich_uploaded = False
                unraid_uploaded = False
                immich_asset_id = None
                unraid_path = None

                for i, (dest, _) in enumerate(upload_tasks):
                    result = results[i]

                    if isinstance(result, Exception):
                        logger.error(f"Upload to {dest} failed: {result}")
                        continue

                    if dest == 'immich' and result:
                        immich_uploaded = True
                        immich_asset_id = result.get('id')
                    elif dest == 'unraid' and result:
                        unraid_uploaded = True
                        unraid_path = result

                # Check for overall success based on upload status and verification
                upload_success = (self.config.immich.enabled is False or immich_uploaded) and \
                                 (self.config.unraid.enabled is False or unraid_uploaded)

                verification_success = True
                if upload_success and self.config.backup.verify_checksums:
                    verification_success = await self._verify_uploads(
                        file_path, file_info,
                        immich_asset_id,
                        unraid_path
                    )
                
                success = upload_success and verification_success
                final_status = 'completed' if success else 'failed'
                error_message = "Verification failed" if not verification_success else None

                # Update database with the final status
                await self.database.update_file_status(
                    file_id,
                    final_status,
                    error_message=error_message,
                    immich_uploaded=immich_uploaded,
                    unraid_uploaded=unraid_uploaded,
                    immich_asset_id=immich_asset_id,
                    unraid_path=unraid_path
                )

                return success, file_info['file_size'] if success else 0

            except Exception as e:
                logger.error(f"Error backing up {file_info['file_name']}: {e}", exc_info=True)

                if file_id:
                    await self.database.update_file_status(
                        file_id,
                        'failed',
                        error_message=str(e)
                    )

                # Retry logic - not fully implemented in original but we keep placeholder
                if file_id and file_info.get('retry_count', 0) < self.config.backup.max_retries:
                    await self.database.increment_retry_count(file_id)
                    await asyncio.sleep(self.config.backup.retry_delay)
                    logger.info(f"Retrying {file_info['file_name']}...")
                    return await self._backup_single_file(session_id, file_info)

                return False, 0

    async def _upload_to_immich(self, file_path: Path, file_info: dict) -> Optional[dict]:
        """Upload file to Immich"""
        try:
            result = await self.immich_client.upload_asset(
                file_path,
                created_at=file_info.get('created_at'),
                device_id=file_info['source_device']
            )
            return result
        except Exception as e:
            logger.error(f"Immich upload failed: {e}")
            return None

    async def _upload_to_unraid(self, file_path: Path, file_info: dict) -> Optional[str]:
        """Upload file to Unraid"""
        try:
            remote_path = await self.unraid_client.upload_file(
                file_path,
                file_info['backup_date'],
                organize_by_date=self.config.unraid.organize_by_date
            )
            return remote_path
        except Exception as e:
            logger.error(f"Unraid upload failed: {e}")
            return None

    async def _verify_uploads(
        self,
        file_path: Path,
        file_info: dict,
        immich_asset_id: Optional[str],
        unraid_path: Optional[str]
    ) -> bool:
        """
        Verify uploaded files match source.

        Returns:
            True if all enabled verifications pass, False otherwise.
        """
        logger.debug(f"Verifying uploads for {file_info['file_name']}...")
        all_verified = True

        # Verify Immich
        if self.config.immich.enabled and immich_asset_id and self.immich_client:
            if not await self.immich_client.verify_asset(immich_asset_id):
                logger.warning(f"Immich verification failed for {file_info['file_name']}")
                all_verified = False

        # Verify Unraid
        if self.config.unraid.enabled and unraid_path and self.unraid_client:
            if not await self.unraid_client.verify_file(unraid_path, file_info['file_size']):
                logger.warning(f"Unraid verification failed for {file_info['file_name']}")
                all_verified = False
        
        return all_verified

    async def get_session_status(self, session_id: str) -> Optional[dict]:
        """Get current status of a backup session"""
        return await self.database.get_session(session_id)

    async def get_active_session(self) -> Optional[dict]:
        """Get currently active backup session"""
        return await self.database.get_active_session()
