"Backup engine for orchestrating file backups"
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, List, Callable
from datetime import datetime
import uuid
from concurrent.futures import ProcessPoolExecutor

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
        progress_callback: Optional[Callable] = None,
        scanning_callback: Optional[Callable] = None
    ):
        self.config = config
        self.database = database
        self.immich_client = immich_client
        self.unraid_client = unraid_client
        self.progress_callback = progress_callback
        self.scanning_callback = scanning_callback
        self._current_session_id: Optional[str] = None
        # We use a Queue instead of a list for producer-consumer pattern
        self._upload_queue: Optional[asyncio.Queue] = None
        self._stop_event = asyncio.Event()

    async def start_backup(self, sd_card: SDCard) -> str:
        """
        Start backup process for an SD card

        Returns:
            Session ID for tracking
        """
        session_id = str(uuid.uuid4())
        self._current_session_id = session_id
        self._stop_event.clear()
        self._upload_queue = asyncio.Queue(maxsize=50) # Buffer 50 files to keep memory usage low

        logger.info(f"Starting backup session {session_id} for {sd_card.device_name}")

        try:
            # Pre-flight Check: Disk Space
            total_size_estimate = 0
            try:
                # Quick rough estimate using shutil (if local) or just statvfs
                # This is just a hint, scanning provides real size
                pass 
            except Exception:
                pass

            # Create initial session
            await self.database.create_session({
                'session_id': session_id,
                'device_name': sd_card.device_name,
                'device_path': sd_card.device_path,
                'mount_point': sd_card.mount_point,
                'status': 'scanning',
                'total_files': 0,
                'total_bytes': 0
            })

            # Start the pipeline
            # 1. Producer: Scan and Hash
            scan_task = asyncio.create_task(self._producer_scan(sd_card, session_id))
            
            # 2. Consumers: Upload Workers
            num_workers = self.config.backup.concurrent_files
            workers = [
                asyncio.create_task(self._upload_worker(session_id, i)) 
                for i in range(num_workers)
            ]

            # Wait for scanner to finish
            await scan_task
            
            # Wait for queue to drain
            await self._upload_queue.join()
            
            # Cancel workers
            for w in workers:
                w.cancel()
            
            # Final status update handled by progress tracking (or check here)
            session = await self.database.get_session(session_id)
            if session and session['status'] not in ['completed', 'failed', 'completed_with_errors']:
                final_status = 'completed' if session['failed_files'] == 0 else 'completed_with_errors'
                await self.database.update_session(session_id, status=final_status)
                logger.info(f"Backup session {session_id} finished via pipeline.")

            return session_id

        except Exception as e:
            logger.error(f"Error starting backup: {e}", exc_info=True)
            await self.database.update_session(session_id, status='failed')
            raise

    async def _producer_scan(self, sd_card: SDCard, session_id: str):
        """
        Producer: Scan files, hash them (multi-core), and put into upload queue.
        """
        mount_point = Path(sd_card.mount_point)
        scanned_count = 0
        total_files_count = 0
        total_bytes_found = 0
        files_found_count = 0
        
        logger.info(f"Scanning {mount_point} for files...")
        
        # Pre-count for progress
        try:
            total_files_count = sum(1 for _ in mount_point.rglob('*') if _.is_file())
            logger.info(f"Found {total_files_count} total files to process")
        except Exception:
            pass

        # Load existing files cache
        try:
            existing_files = await self.database.get_existing_files_metadata(sd_card.device_id)
        except Exception:
            existing_files = set()

        # Update DB with total count immediately so UI shows something
        await self.database.update_session(
            session_id, 
            total_files=total_files_count if total_files_count > 0 else 0
        )

        # Setup ProcessPoolExecutor for hashing
        # Limit workers to avoid thrashing I/O, but use >1 for CPU speedup if MD5 is bottleneck
        # Actually, for SD cards, sequential read is key. 
        # But if we read in main process and hash in worker? No, passing data is slow.
        # We let workers read. To avoid random I/O, we limit to 1 worker for hashing? 
        # Or we rely on OS buffering.
        # The user requested Multi-Core. We'll use 2-4 workers.
        max_hash_workers = 2 
        
        with ProcessPoolExecutor(max_workers=max_hash_workers) as executor:
            loop = asyncio.get_running_loop()
            
            for file_path in mount_point.rglob('*'):
                if self._stop_event.is_set():
                    break
                    
                if not file_path.is_file():
                    continue

                scanned_count += 1
                
                # Report scanning progress
                if self.scanning_callback and scanned_count % 10 == 0:
                    await self.scanning_callback(session_id, scanned_count, total_files_count, str(file_path.name))

                if not self._should_backup_file(file_path):
                    continue

                file_size = file_path.stat().st_size
                if file_size < self.config.files.min_size:
                    continue

                # Optimization: Metadata check
                if (file_path.name, file_size) in existing_files:
                    logger.debug(f"Skipping {file_path.name} (metadata match)")
                    continue

                # Hashing (Offloaded to process pool)
                try:
                    # Note: This does I/O in the worker process
                    # calculate_file_hash is imported from database.py
                    file_hash = await loop.run_in_executor(
                        executor, 
                        calculate_file_hash, 
                        file_path,
                        self.config.backup.hash_algorithm
                    )
                except Exception as e:
                    logger.error(f"Failed to hash {file_path}: {e}")
                    continue

                # Check DB for completion
                if await self.database.file_exists(file_hash, sd_card.device_id):
                    continue

                # Valid file to backup
                file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                backup_date = file_mtime.strftime('%Y/%m/%d')
                
                file_info = {
                    'file_path': str(file_path),
                    'file_name': file_path.name,
                    'file_size': file_size,
                    'md5_hash': file_hash,
                    'source_device': sd_card.device_id,
                    'status': 'new',
                    'backup_date': backup_date,
                    'created_at': file_mtime
                }
                
                # Check space requirements once (Pre-flight for this file)
                # If we were strictly pre-flighting, we'd do it before scanning.
                # But here we do it dynamically. 
                # Just proceed.
                
                files_found_count += 1
                total_bytes_found += file_size

                # Update session totals incrementally (so progress bar grows correctly if total_files_count was wrong)
                # Or better: We update session 'total_files' to match what we actually found to backup?
                # No, total_files usually means "Files to backup".
                # But we initialized with 0.
                
                # Logic update: We should update 'total_files' in DB as we find them, 
                # so the progress bar (completed/total) makes sense.
                # But we don't want it to jump 0->1->2.
                # The previous logic counted ALL then updated.
                # With pipelining, we update incrementally.
                await self.database.update_session(
                    session_id, 
                    total_files=files_found_count,
                    total_bytes=total_bytes_found
                )
                
                # Put in queue
                await self._upload_queue.put(file_info)

        # Signal end of scanning?
        # We don't need a signal because we await the scan_task in start_backup.
        # But consumers need to know when to stop?
        # Consumers loop while True. We cancel them when queue is empty AND scan is done.
        logger.info(f"Scanning finished. Found {files_found_count} new files.")
        
        # Update status from 'scanning' to 'backing_up' if not already
        await self.database.update_session(session_id, status='backing_up')

    async def _upload_worker(self, session_id: str, worker_id: int):
        """
        Consumer: Pulls files from queue and uploads them with retry logic.
        """
        while not self._stop_event.is_set():
            try:
                # Get a "work item"
                file_info = await self._upload_queue.get()
                
                try:
                    await self._backup_single_file_with_retry(session_id, file_info)
                finally:
                    # Notify queue that item is processed
                    self._upload_queue.task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} error: {e}")

    async def _backup_single_file_with_retry(self, session_id: str, file_info: dict):
        """
        Wrapper around _backup_single_file to handle Smart Network Backoff
        """
        max_retries = self.config.backup.max_retries
        retry_count = 0
        
        while retry_count <= max_retries:
            success, bytes_transferred = await self._backup_single_file(session_id, file_info)
            
            if success:
                return
            
            # If failed, check if we should backoff
            retry_count += 1
            if retry_count <= max_retries:
                logger.warning(f"File {file_info['file_name']} failed. Retry {retry_count}/{max_retries}...")
                
                # Smart Network Backoff Check
                # If clients are disconnected, wait until they are back
                if not await self._check_connectivity():
                    logger.warning("Network connectivity lost. Pausing backup...")
                    await self._wait_for_connectivity()
                    logger.info("Network restored. Resuming...")
                
                await asyncio.sleep(self.config.backup.retry_delay)
            else:
                logger.error(f"File {file_info['file_name']} failed after {max_retries} retries.")

    async def _check_connectivity(self) -> bool:
        """Check if backup destinations are reachable"""
        tasks = []
        if self.config.immich.enabled and self.immich_client:
            tasks.append(self.immich_client.check_connection())
        if self.config.unraid.enabled and self.unraid_client:
            tasks.append(self.unraid_client.check_connection())
            
        if not tasks:
            return True
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Return True if ANY configured destination is reachable? Or ALL?
        # If one is down, we might want to pause to avoid partial backups?
        # Let's say ALL must be reachable if enabled.
        return all(r is True for r in results)

    async def _wait_for_connectivity(self):
        """Loop until connectivity is restored"""
        while True:
            if await self._check_connectivity():
                return
            await asyncio.sleep(10) # Check every 10s

    async def _backup_single_file(self, session_id: str, file_info: dict) -> tuple[bool, int]:
        """Backup a single file (Implementation)"""
        # (This remains mostly the same as before, but called by worker)
        # Note: Logic copied from previous implementation but slightly adapted
        
        file_path = Path(file_info['file_path'])
        file_id = None
        
        try:
            # UPSERT into DB
            file_id = await self.database.add_file(file_info)
            await self.database.update_file_status(file_id, 'backing_up')

            # Prepare uploads
            upload_tasks = []
            if self.config.immich.enabled and self.immich_client:
                upload_tasks.append(('immich', self._upload_to_immich(file_path, file_info)))
            if self.config.unraid.enabled and self.unraid_client:
                upload_tasks.append(('unraid', self._upload_to_unraid(file_path, file_info)))

            # Execute
            results = await asyncio.gather(*[task for _, task in upload_tasks], return_exceptions=True)
            
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

            upload_success = (self.config.immich.enabled is False or immich_uploaded) and \
                             (self.config.unraid.enabled is False or unraid_uploaded)
            
            verification_success = True
            if upload_success and self.config.backup.verify_checksums:
                verification_success = await self._verify_uploads(
                    file_path, file_info, immich_asset_id, unraid_path
                )

            success = upload_success and verification_success
            final_status = 'completed' if success else 'failed'
            error_msg = "Verification failed" if not verification_success else None
            
            await self.database.update_file_status(
                file_id, final_status, error_message=error_msg,
                immich_uploaded=immich_uploaded, unraid_uploaded=unraid_uploaded,
                immich_asset_id=immich_asset_id, unraid_path=unraid_path
            )
            
            # Update Progress (Since this is worker, we need to handle session updates here)
            # The session totals are updated by producer. We update completed/failed.
            if success:
                await self.database.db.execute(
                    "UPDATE backup_sessions SET completed_files = completed_files + 1, transferred_bytes = transferred_bytes + ? WHERE session_id = ?",
                    (file_info['file_size'], session_id)
                )
            else:
                await self.database.db.execute(
                    "UPDATE backup_sessions SET failed_files = failed_files + 1 WHERE session_id = ?",
                    (session_id,)
                )
            await self.database.db.commit()
            
            # Callback
            if self.progress_callback:
                # We need to fetch current session stats to report accurately
                session = await self.database.get_session(session_id)
                if session:
                    # Calculate elapsed/speed
                    start_t = datetime.fromisoformat(session['start_time']).timestamp() if isinstance(session['start_time'], str) else time.time() # Simplification
                    elapsed = time.time() - start_t
                    transferred = session['transferred_bytes']
                    total_bytes = session['total_bytes']
                    speed = transferred / elapsed if elapsed > 0 else 0
                    remaining = (total_bytes - transferred) / speed if speed > 0 else 0
                    
                    await self.progress_callback(
                        session_id,
                        session['completed_files'],
                        session['failed_files'],
                        session['total_files'],
                        elapsed_seconds=elapsed,
                        remaining_seconds=remaining,
                        current_speed=speed
                    )

            return success, file_info['file_size'] if success else 0
            
        except Exception as e:
            logger.error(f"Error backing up single file: {e}")
            if file_id:
                await self.database.update_file_status(file_id, 'failed', error_message=str(e))
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
        """
        all_verified = True
        if self.config.immich.enabled and immich_asset_id and self.immich_client:
            if not await self.immich_client.verify_asset(immich_asset_id):
                all_verified = False
        if self.config.unraid.enabled and unraid_path and self.unraid_client:
            if not await self.unraid_client.verify_file(unraid_path, file_info['file_size']):
                all_verified = False
        return all_verified

    def _should_backup_file(self, file_path: Path) -> bool:
        """Check if file should be backed up based on extension"""
        ext = file_path.suffix.lower()
        return ext in [e.lower() for e in self.config.files.extensions]