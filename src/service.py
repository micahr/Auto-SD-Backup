"""Main service orchestrator for SnapSync"""
import asyncio
import logging
import signal
from typing import Optional
import uvicorn

from .config import Config
from .database import BackupDatabase
from .sd_detector_cross_platform import create_detector, SDCard
from .immich_client import ImmichClient
from .unraid_client import UnraidClient
from .backup_engine import BackupEngine
from .mqtt_client import MQTTClient
from .web_ui import create_app
from .eject import eject_device

logger = logging.getLogger(__name__)


class ServiceManager:
    """Main service manager that orchestrates all components"""

    def __init__(self, config: Config):
        self.config = config
        self.database = BackupDatabase(self.config.service.database_path)
        self.sd_detector: Optional[SDCardDetector] = None
        self.immich_client: Optional[ImmichClient] = None
        self.unraid_client: Optional[UnraidClient] = None
        self.backup_engine: Optional[BackupEngine] = None
        self.mqtt_client: Optional[MQTTClient] = None
        self._running = False
        self._current_status = "idle"
        self._web_server: Optional[uvicorn.Server] = None
        self._pending_backups: dict[str, SDCard] = {}  # Pending approval backups
        self._auto_backup_enabled = True  # Runtime toggle
        self._current_progress: dict = {} # Store current progress metrics
        self._detector_task = None
        self._web_server_task = None

    async def start(self):
        """Start all service components"""
        logger.info("Starting SnapSync service...")

        try:
            self._running = True
            logger.info("SnapSync service started successfully")

            # Setup logging level
            logging.getLogger().setLevel(getattr(logging, self.config.service.log_level))

            # Reduce log level for smbprotocol to avoid excessive logging
            logging.getLogger('smbprotocol').setLevel(logging.WARNING)
            logging.getLogger('aiosqlite').setLevel(logging.WARNING)

            # Configure separate log for HTTP traffic if specified
            if self.config.service.http_log_path:
                http_loggers = ['httpx', 'uvicorn', 'uvicorn.access', 'uvicorn.error']
                file_handler = logging.FileHandler(self.config.service.http_log_path)
                file_handler.setFormatter(
                    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                )

                for logger_name in http_loggers:
                    http_logger = logging.getLogger(logger_name)
                    http_logger.setLevel(logging.INFO)  # Capture INFO and above for HTTP traffic
                    http_logger.addHandler(file_handler)
                    http_logger.propagate = False
                
                logger.info(f"Redirecting HTTP logs to {self.config.service.http_log_path}")

            # Initialize database
            await self.database.initialize()

            # Clean up interrupted sessions from previous runs
            try:
                await self.database.db.execute(
                    "UPDATE backup_sessions SET status = 'failed', end_time = CURRENT_TIMESTAMP "
                    "WHERE status IN ('scanning', 'backing_up')"
                )
                await self.database.db.execute(
                    "UPDATE files SET status = 'failed', error_message = 'Interrupted by service restart' "
                    "WHERE status = 'backing_up'"
                )
                await self.database.db.commit()
            except Exception as e:
                logger.warning(f"Failed to clean up stale sessions: {e}")

            # Initialize Immich client if enabled
            if self.config.immich.enabled:
                self.immich_client = ImmichClient(
                    self.config.immich.url,
                    self.config.immich.api_key,
                    self.config.immich.timeout
                )
                await self.immich_client.initialize()
                if not await self.immich_client.check_connection():
                    logger.error("Failed to connect to Immich server")
                    if not self.config.unraid.enabled:
                        raise Exception("No backup destinations available")

            # Initialize Unraid client if enabled
            if self.config.unraid.enabled:
                self.unraid_client = UnraidClient(
                    self.config.unraid.host,
                    self.config.unraid.share,
                    self.config.unraid.path,
                    self.config.unraid.username,
                    self.config.unraid.password,
                    self.config.unraid.protocol
                )
                await self.unraid_client.initialize()
                if not await self.unraid_client.check_connection():
                    logger.error("Failed to connect to Unraid server")
                    if not self.config.immich.enabled:
                        raise Exception("No backup destinations available")

            # Initialize MQTT client if enabled
            if self.config.mqtt.enabled:
                self.mqtt_client = MQTTClient(self.config.mqtt, service=self)
                await self.mqtt_client.initialize()
                await self.mqtt_client.publish_status("idle")

            # Initialize backup engine
            self.backup_engine = BackupEngine(
                self.config,
                self.database,
                self.immich_client,
                self.unraid_client,
                progress_callback=self._on_backup_progress,
                scanning_callback=self._on_scanning_progress
            )

            # Initialize SD card detector (auto-detects platform)
            detection_mode = getattr(self.config.sd_card, 'detection_mode', 'auto')
            self.sd_detector = create_detector(
                on_insert=self._on_sd_card_inserted,
                on_remove=self._on_sd_card_removed,
                mode=detection_mode
            )

            # Start web server in background
            self._web_server_task = asyncio.create_task(self._start_web_server())

            # Start SD card detection in the background
            self._detector_task = asyncio.create_task(self.sd_detector.start())

            # Wait indefinitely until the service is stopped
            while self._running:
                await asyncio.sleep(1)

        except (asyncio.CancelledError, KeyboardInterrupt):
            logger.info("Service interruption detected.")
        except Exception as e:
            logger.error(f"Unexpected error in service loop: {e}", exc_info=True)
        finally:
            logger.info("Service shutting down.")
            await self.stop()

    async def stop(self):
        """Stop all service components"""
        if not self._running:
            return  # Avoid multiple stop calls

        logger.info("Stopping SnapSync service...")
        self._running = False

        # Stop SD card detector task
        if self.sd_detector:
            await self.sd_detector.stop()  # Signal the loop to stop

        if self._detector_task:
            self._detector_task.cancel()
            try:
                await self._detector_task
            except asyncio.CancelledError:
                pass  # This is expected

        # Close MQTT client
        if self.mqtt_client:
            self.mqtt_client.close()

        # Close Immich client
        if self.immich_client:
            await self.immich_client.close()

        # Close Unraid client
        if self.unraid_client:
            await self.unraid_client.close()

        # Close database
        if self.database:
            await self.database.close()

        # Stop web server task
        if self._web_server and self._web_server.started:
            self._web_server.should_exit = True
        
        if self._web_server_task:
            self._web_server_task.cancel()
            try:
                await self._web_server_task
            except asyncio.CancelledError:
                pass

        logger.info("SnapSync service stopped")

    async def _start_web_server(self):
        """Start the web UI server"""
        try:
            app = create_app(self)

            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=self.config.service.web_ui_port,
                log_level="info",
                lifespan="off",  # Disable lifespan to prevent CancelledError on force exit
            )
            # Disable uvicorn's signal handlers to allow the main application to manage shutdown
            # We set this directly on the config object to bypass version compatibility issues
            config.install_signal_handlers = False

            self._web_server = uvicorn.Server(config)
            
            # Forcefully disable signal handlers by monkey-patching the method
            # This ensures uvicorn cannot hijack the main application's loop
            self._web_server.install_signal_handlers = lambda: None

            # If redirecting logs, remove the default console handler uvicorn adds to the access logger
            if self.config.service.http_log_path:
                access_logger = logging.getLogger('uvicorn.access')
                # Remove all handlers that are StreamHandlers to prevent console output
                access_logger.handlers = [
                    h for h in access_logger.handlers
                    if not isinstance(h, logging.StreamHandler)
                ]

            logger.info(f"Starting web UI on port {self.config.service.web_ui_port}")
            try:
                await self._web_server.serve()
            except asyncio.CancelledError:
                # This is expected during forceful shutdown
                pass

        except Exception as e:
            logger.error(f"Error starting web server: {e}", exc_info=True)

    async def _on_sd_card_inserted(self, sd_card: SDCard):
        """Handle SD card insertion event"""
        logger.info(f"SD card inserted: {sd_card.device_name} at {sd_card.mount_point}")

        try:
            # Check if auto-backup is enabled
            if not self._auto_backup_enabled:
                logger.info("Auto-backup is disabled, ignoring SD card")
                return

            # Check if approval is required
            if self.config.backup.require_approval:
                # Add to pending queue
                backup_id = f"pending_{sd_card.device_name}_{id(sd_card)}"
                self._pending_backups[backup_id] = sd_card
                self._current_status = "pending_approval"

                logger.warning(f"⏸️  Backup pending approval for: {sd_card.device_name}")
                logger.warning(f"  Approve via web UI (http://localhost:{self.config.service.web_ui_port})")
                logger.warning(f"  or MQTT command: snapsync/command approve {backup_id}")

                if self.mqtt_client:
                    await self.mqtt_client.publish_status("pending_approval")
                    await self.mqtt_client.publish_pending_backup(backup_id, sd_card)

                return

            # Auto-start backup
            await self._start_backup(sd_card)

        except Exception as e:
            logger.error(f"Error handling SD card insertion: {e}", exc_info=True)

            if self.mqtt_client:
                await self.mqtt_client.publish_error(str(e))
                await self.mqtt_client.publish_status("failed")

            self._current_status = "failed"

    async def _start_backup(self, sd_card: SDCard):
        """Start backup for an SD card"""
        self._current_status = "backing_up"

        if self.mqtt_client:
            await self.mqtt_client.publish_status("backing_up")

        # Start backup
        session_id = await self.backup_engine.start_backup(sd_card)

        logger.info(f"Backup started with session ID: {session_id}")

    async def _on_sd_card_removed(self, sd_card: SDCard):
        """Handle SD card removal event"""
        logger.info(f"SD card removed: {sd_card.device_name}")

        # Note: We don't stop the backup if it's in progress
        # The backup engine will handle files that are no longer accessible

    async def _on_scanning_progress(self, session_id: str, count: int, total: int, filename: str):
        """Handle scanning progress updates"""
        # Log less frequently to keep logs clean
        if count % 50 == 0:
            logger.info(f"Scanning progress: {count}/{total} files (hashing {filename})")
        
        # Update status and MQTT every 5 files to reduce overhead
        if count % 5 == 0 or count == total:
            if total > 0:
                status_msg = f"scanning ({count}/{total} files)"
            else:
                status_msg = f"scanning ({count} files)"
            
            self._current_status = status_msg
            
            if self.mqtt_client:
                await self.mqtt_client.publish_status(status_msg)

    async def _on_backup_progress(
        self,
        session_id: str,
        completed: int,
        failed: int,
        total: int,
        elapsed_seconds: float = 0,
        remaining_seconds: float = 0,
        current_speed: float = 0
    ):
        """Handle backup progress updates"""
        logger.info(f"Backup progress: {completed}/{total} files ({failed} failed)")

        # Get session info
        session = await self.database.get_session(session_id)

        # Update local progress state
        self._current_progress = {
            "elapsed_seconds": elapsed_seconds,
            "remaining_seconds": remaining_seconds,
            "current_speed": current_speed
        }

        if self.mqtt_client:
            await self.mqtt_client.publish_progress(
                completed=completed,
                total=total,
                bytes_transferred=session.get('transferred_bytes', 0) if session else 0,
                total_bytes=session.get('total_bytes', 0) if session else 0,
                elapsed_seconds=elapsed_seconds,
                remaining_seconds=remaining_seconds,
                current_speed=current_speed
            )

        # Check if backup is complete
        if completed + failed >= total:
            logger.info(f"Backup session {session_id} completed")

            self._current_status = "completed" if failed == 0 else "completed_with_errors"

            if self.mqtt_client:
                await self.mqtt_client.publish_session_complete(session or {})
                await self.mqtt_client.publish_status("completed")

            # Auto-eject if enabled (regardless of success/failure)
            if self.config.backup.auto_eject and session and session.get('mount_point'):
                logger.info(f"Auto-ejecting {session['mount_point']}...")
                await eject_device(session['mount_point'])

            # Return to idle after a short delay
            await asyncio.sleep(5)
            self._current_status = "idle"
            self._current_progress = {}

            if self.mqtt_client:
                await self.mqtt_client.publish_status("idle")

    async def get_status(self) -> dict:
        """Get current service status"""
        active_session = await self.database.get_active_session()

        return {
            "status": self._current_status,
            "current_session": active_session,
            "progress": self._current_progress,
            "immich_enabled": self.config.immich.enabled,
            "unraid_enabled": self.config.unraid.enabled,
            "mqtt_enabled": self.config.mqtt.enabled,
            "auto_backup_enabled": self._auto_backup_enabled,
            "require_approval": self.config.backup.require_approval,
            "pending_backups": list(self._pending_backups.keys())
        }

    async def approve_backup(self, backup_id: str) -> bool:
        """Approve a pending backup"""
        if backup_id not in self._pending_backups:
            logger.error(f"Backup {backup_id} not found in pending queue")
            return False

        sd_card = self._pending_backups.pop(backup_id)
        logger.info(f"✓ Backup approved for: {sd_card.device_name}")

        await self._start_backup(sd_card)
        return True

    async def reject_backup(self, backup_id: str) -> bool:
        """Reject a pending backup"""
        if backup_id not in self._pending_backups:
            logger.error(f"Backup {backup_id} not found in pending queue")
            return False

        sd_card = self._pending_backups.pop(backup_id)
        logger.info(f"✗ Backup rejected for: {sd_card.device_name}")

        if self.mqtt_client:
            await self.mqtt_client.publish_status("idle")

        self._current_status = "idle"
        return True

    async def set_auto_backup(self, enabled: bool):
        """Enable or disable auto-backup"""
        self._auto_backup_enabled = enabled
        logger.info(f"Auto-backup {'enabled' if enabled else 'disabled'}")

        if self.mqtt_client:
            await self.mqtt_client.publish_auto_backup_status(enabled)

    async def get_pending_backups(self) -> dict:
        """Get list of pending backups"""
        return {
            backup_id: {
                "device_name": card.device_name,
                "mount_point": card.mount_point,
                "size": card.size,
                "label": card.label
            }
            for backup_id, card in self._pending_backups.items()
        }

    async def trigger_backup(self, path: str) -> str:
        """
        Manually trigger a backup for a specific directory

        Args:
            path: Path to directory to backup

        Returns:
            Session ID
        """
        from pathlib import Path
        path_obj = Path(path)

        if not path_obj.exists():
            raise ValueError(f"Path does not exist: {path}")

        if not path_obj.is_dir():
            raise ValueError(f"Path is not a directory: {path}")

        # Create a simulated SD card
        sd_card = SDCard(
            device_name=path_obj.name,
            mount_point=str(path_obj),
            device_path=str(path_obj),
            size=0,
            label=path_obj.name
        )

        logger.info(f"Manually triggered backup for: {path}")
        return await self.backup_engine.start_backup(sd_card)
