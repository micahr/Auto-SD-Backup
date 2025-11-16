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

logger = logging.getLogger(__name__)


class ServiceManager:
    """Main service manager that orchestrates all components"""

    def __init__(self, config: Config):
        self.config = config
        self.database: Optional[BackupDatabase] = None
        self.sd_detector: Optional[SDCardDetector] = None
        self.immich_client: Optional[ImmichClient] = None
        self.unraid_client: Optional[UnraidClient] = None
        self.backup_engine: Optional[BackupEngine] = None
        self.mqtt_client: Optional[MQTTClient] = None
        self._running = False
        self._current_status = "idle"
        self._web_server: Optional[uvicorn.Server] = None

    async def start(self):
        """Start all service components"""
        logger.info("Starting SnapSync service...")

        try:
            # Setup logging level
            logging.getLogger().setLevel(getattr(logging, self.config.service.log_level))

            # Initialize database
            self.database = BackupDatabase(self.config.service.database_path)
            await self.database.initialize()

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
                self.mqtt_client = MQTTClient(self.config.mqtt)
                await self.mqtt_client.initialize()
                await self.mqtt_client.publish_status("idle")

            # Initialize backup engine
            self.backup_engine = BackupEngine(
                self.config,
                self.database,
                self.immich_client,
                self.unraid_client,
                progress_callback=self._on_backup_progress
            )

            # Initialize SD card detector (auto-detects platform)
            detection_mode = getattr(self.config.sd_card, 'detection_mode', 'auto')
            self.sd_detector = create_detector(
                on_insert=self._on_sd_card_inserted,
                on_remove=self._on_sd_card_removed,
                mode=detection_mode
            )

            # Start web server in background
            asyncio.create_task(self._start_web_server())

            # Setup signal handlers
            self._setup_signal_handlers()

            # Start SD card detection
            self._running = True
            logger.info("SnapSync service started successfully")

            await self.sd_detector.start()

        except Exception as e:
            logger.error(f"Failed to start service: {e}", exc_info=True)
            await self.stop()
            raise

    async def stop(self):
        """Stop all service components"""
        logger.info("Stopping SnapSync service...")
        self._running = False

        # Stop SD card detector
        if self.sd_detector:
            await self.sd_detector.stop()

        # Close MQTT client
        if self.mqtt_client:
            await self.mqtt_client.close()

        # Close Immich client
        if self.immich_client:
            await self.immich_client.close()

        # Close Unraid client
        if self.unraid_client:
            await self.unraid_client.close()

        # Close database
        if self.database:
            await self.database.close()

        # Stop web server
        if self._web_server:
            self._web_server.should_exit = True

        logger.info("SnapSync service stopped")

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self.stop())
            )

    async def _start_web_server(self):
        """Start the web UI server"""
        try:
            app = create_app(self)

            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=self.config.service.web_ui_port,
                log_level="info"
            )

            self._web_server = uvicorn.Server(config)

            logger.info(f"Starting web UI on port {self.config.service.web_ui_port}")
            await self._web_server.serve()

        except Exception as e:
            logger.error(f"Error starting web server: {e}", exc_info=True)

    async def _on_sd_card_inserted(self, sd_card: SDCard):
        """Handle SD card insertion event"""
        logger.info(f"SD card inserted: {sd_card.device_name} at {sd_card.mount_point}")

        try:
            # Update status
            self._current_status = "backing_up"

            if self.mqtt_client:
                await self.mqtt_client.publish_status("backing_up")

            # Start backup
            session_id = await self.backup_engine.start_backup(sd_card)

            logger.info(f"Backup started with session ID: {session_id}")

        except Exception as e:
            logger.error(f"Error handling SD card insertion: {e}", exc_info=True)

            if self.mqtt_client:
                await self.mqtt_client.publish_error(str(e))
                await self.mqtt_client.publish_status("failed")

            self._current_status = "failed"

    async def _on_sd_card_removed(self, sd_card: SDCard):
        """Handle SD card removal event"""
        logger.info(f"SD card removed: {sd_card.device_name}")

        # Note: We don't stop the backup if it's in progress
        # The backup engine will handle files that are no longer accessible

    async def _on_backup_progress(
        self,
        session_id: str,
        completed: int,
        failed: int,
        total: int
    ):
        """Handle backup progress updates"""
        logger.info(f"Backup progress: {completed}/{total} files ({failed} failed)")

        # Get session info
        session = await self.database.get_session(session_id)

        if self.mqtt_client:
            await self.mqtt_client.publish_progress(
                completed=completed,
                total=total,
                bytes_transferred=session.get('transferred_bytes', 0) if session else 0,
                total_bytes=session.get('total_bytes', 0) if session else 0
            )

        # Check if backup is complete
        if completed + failed >= total:
            logger.info(f"Backup session {session_id} completed")

            self._current_status = "completed" if failed == 0 else "completed_with_errors"

            if self.mqtt_client:
                await self.mqtt_client.publish_session_complete(session or {})
                await self.mqtt_client.publish_status("completed")

            # Return to idle after a short delay
            await asyncio.sleep(5)
            self._current_status = "idle"

            if self.mqtt_client:
                await self.mqtt_client.publish_status("idle")

    async def get_status(self) -> dict:
        """Get current service status"""
        active_session = await self.database.get_active_session()

        return {
            "status": self._current_status,
            "current_session": active_session,
            "immich_enabled": self.config.immich.enabled,
            "unraid_enabled": self.config.unraid.enabled,
            "mqtt_enabled": self.config.mqtt.enabled
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
