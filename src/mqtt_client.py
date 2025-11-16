"""MQTT client for Home Assistant integration"""
import json
import logging
import asyncio
from typing import Optional, Dict, Any
import paho.mqtt.client as mqtt
from .config import MQTTConfig

logger = logging.getLogger(__name__)


class MQTTClient:
    """MQTT client for publishing to Home Assistant"""

    def __init__(self, config: MQTTConfig, service=None):
        self.config = config
        self.client: Optional[mqtt.Client] = None
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._service = service  # Reference to BackupService for command handling

    async def initialize(self):
        """Initialize MQTT client and connect to broker"""
        self._loop = asyncio.get_event_loop()

        self.client = mqtt.Client(client_id=self.config.client_id)

        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # Set credentials if provided
        if self.config.username and self.config.password:
            self.client.username_pw_set(self.config.username, self.config.password)

        # Connect to broker
        try:
            logger.info(f"Connecting to MQTT broker at {self.config.broker}:{self.config.port}")
            self.client.connect(self.config.broker, self.config.port, 60)
            self.client.loop_start()

            # Wait for connection
            for _ in range(50):  # 5 second timeout
                if self._connected:
                    break
                await asyncio.sleep(0.1)

            if not self._connected:
                raise Exception("Failed to connect to MQTT broker")

            # Send discovery messages
            await self._send_discovery()

            # Subscribe to command topic
            command_topic = f"{self.config.topic_prefix}/command"
            self.client.subscribe(command_topic, qos=1)
            logger.info(f"Subscribed to command topic: {command_topic}")

            logger.info("MQTT client initialized and connected")

        except Exception as e:
            logger.error(f"Failed to initialize MQTT client: {e}", exc_info=True)
            raise

    async def close(self):
        """Close MQTT connection"""
        if self.client:
            # Send offline status
            await self.publish_status("offline")
            self.client.loop_stop()
            self.client.disconnect()
            logger.info("MQTT client disconnected")

    def _on_connect(self, client, userdata, flags, rc):
        """Callback for when client connects to broker"""
        if rc == 0:
            logger.info("Successfully connected to MQTT broker")
            self._connected = True
        else:
            logger.error(f"Failed to connect to MQTT broker with code {rc}")
            self._connected = False

    def _on_disconnect(self, client, userdata, rc):
        """Callback for when client disconnects from broker"""
        logger.warning(f"Disconnected from MQTT broker with code {rc}")
        self._connected = False

    def _on_message(self, client, userdata, msg):
        """Callback for when a message is received"""
        logger.debug(f"Received message on {msg.topic}: {msg.payload}")

        # Handle command messages
        command_topic = f"{self.config.topic_prefix}/command"
        if msg.topic == command_topic and self._service:
            try:
                command = msg.payload.decode('utf-8').strip()
                logger.info(f"Received command: {command}")

                # Schedule command handling in the event loop
                if self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_command(command),
                        self._loop
                    )
            except Exception as e:
                logger.error(f"Error processing command: {e}", exc_info=True)

    async def _handle_command(self, command: str):
        """Handle received MQTT commands"""
        try:
            if command == "auto_backup_enable":
                await self._service.set_auto_backup(True)
                logger.info("Auto-backup enabled via MQTT")
            elif command == "auto_backup_disable":
                await self._service.set_auto_backup(False)
                logger.info("Auto-backup disabled via MQTT")
            elif command.startswith("approve_"):
                backup_id = command.replace("approve_", "")
                success = await self._service.approve_backup(backup_id)
                if success:
                    logger.info(f"Backup {backup_id} approved via MQTT")
                else:
                    logger.warning(f"Failed to approve backup {backup_id}")
            elif command.startswith("reject_"):
                backup_id = command.replace("reject_", "")
                success = await self._service.reject_backup(backup_id)
                if success:
                    logger.info(f"Backup {backup_id} rejected via MQTT")
                else:
                    logger.warning(f"Failed to reject backup {backup_id}")
            else:
                logger.warning(f"Unknown command: {command}")
        except Exception as e:
            logger.error(f"Error handling command '{command}': {e}", exc_info=True)

    async def _send_discovery(self):
        """Send Home Assistant MQTT discovery messages"""
        device_info = {
            "identifiers": ["snapsync"],
            "name": "SnapSync",
            "model": "SD Card Backup Service",
            "manufacturer": "SnapSync",
            "sw_version": "1.0.0"
        }

        # Sensor for backup status
        status_config = {
            "name": "SnapSync Status",
            "unique_id": "snapsync_status",
            "state_topic": f"{self.config.topic_prefix}/status",
            "device": device_info,
            "icon": "mdi:content-save-all"
        }

        await self._publish(
            f"{self.config.discovery_prefix}/sensor/snapsync/status/config",
            json.dumps(status_config),
            retain=True
        )

        # Sensor for current file
        current_file_config = {
            "name": "SnapSync Current File",
            "unique_id": "snapsync_current_file",
            "state_topic": f"{self.config.topic_prefix}/current_file",
            "device": device_info,
            "icon": "mdi:file"
        }

        await self._publish(
            f"{self.config.discovery_prefix}/sensor/snapsync/current_file/config",
            json.dumps(current_file_config),
            retain=True
        )

        # Sensor for progress
        progress_config = {
            "name": "SnapSync Progress",
            "unique_id": "snapsync_progress",
            "state_topic": f"{self.config.topic_prefix}/progress",
            "json_attributes_topic": f"{self.config.topic_prefix}/progress",
            "device": device_info,
            "unit_of_measurement": "%",
            "icon": "mdi:progress-upload"
        }

        await self._publish(
            f"{self.config.discovery_prefix}/sensor/snapsync/progress/config",
            json.dumps(progress_config),
            retain=True
        )

        # Sensor for files completed
        files_config = {
            "name": "SnapSync Files",
            "unique_id": "snapsync_files",
            "state_topic": f"{self.config.topic_prefix}/files",
            "json_attributes_topic": f"{self.config.topic_prefix}/files",
            "device": device_info,
            "icon": "mdi:file-multiple"
        }

        await self._publish(
            f"{self.config.discovery_prefix}/sensor/snapsync/files/config",
            json.dumps(files_config),
            retain=True
        )

        logger.info("Sent Home Assistant discovery messages")

    async def _publish(self, topic: str, payload: str, retain: bool = False):
        """Publish message to MQTT broker"""
        if not self._connected:
            logger.warning("Not connected to MQTT broker, cannot publish")
            return

        def _do_publish():
            result = self.client.publish(topic, payload, qos=1, retain=retain)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.error(f"Failed to publish to {topic}: {result.rc}")

        await asyncio.to_thread(_do_publish)

    async def publish_status(self, status: str):
        """
        Publish backup status

        Args:
            status: One of "idle", "backing_up", "completed", "failed", "offline"
        """
        await self._publish(f"{self.config.topic_prefix}/status", status, retain=True)
        logger.debug(f"Published status: {status}")

    async def publish_progress(
        self,
        completed: int,
        total: int,
        current_file: Optional[str] = None,
        bytes_transferred: int = 0,
        total_bytes: int = 0
    ):
        """Publish backup progress information"""
        if total > 0:
            percentage = int((completed / total) * 100)
        else:
            percentage = 0

        progress_data = {
            "percentage": percentage,
            "completed_files": completed,
            "total_files": total,
            "bytes_transferred": bytes_transferred,
            "total_bytes": total_bytes
        }

        await self._publish(
            f"{self.config.topic_prefix}/progress",
            str(percentage)
        )

        await self._publish(
            f"{self.config.topic_prefix}/progress",
            json.dumps(progress_data),
            retain=False
        )

        if current_file:
            await self._publish(
                f"{self.config.topic_prefix}/current_file",
                current_file
            )

        # Also publish files info
        files_data = {
            "completed": completed,
            "total": total,
            "failed": 0  # Can be enhanced to track failures
        }

        await self._publish(
            f"{self.config.topic_prefix}/files",
            f"{completed}/{total}"
        )

        await self._publish(
            f"{self.config.topic_prefix}/files",
            json.dumps(files_data),
            retain=False
        )

    async def publish_session_complete(self, session_info: Dict[str, Any]):
        """Publish session completion information"""
        await self.publish_status("completed")

        summary = {
            "total_files": session_info.get('total_files', 0),
            "completed_files": session_info.get('completed_files', 0),
            "failed_files": session_info.get('failed_files', 0),
            "total_bytes": session_info.get('total_bytes', 0),
            "transferred_bytes": session_info.get('transferred_bytes', 0)
        }

        await self._publish(
            f"{self.config.topic_prefix}/last_session",
            json.dumps(summary),
            retain=True
        )

    async def publish_error(self, error_message: str):
        """Publish error information"""
        await self.publish_status("failed")
        await self._publish(
            f"{self.config.topic_prefix}/error",
            error_message
        )

    async def publish_pending_backup(self, backup_id: str, sd_card):
        """Publish pending backup information"""
        pending_data = {
            "backup_id": backup_id,
            "device_name": sd_card.device_name,
            "mount_point": sd_card.mount_point,
            "size": sd_card.size,
            "label": sd_card.label
        }

        await self._publish(
            f"{self.config.topic_prefix}/pending_backup",
            json.dumps(pending_data)
        )

    async def publish_auto_backup_status(self, enabled: bool):
        """Publish auto-backup enabled/disabled status"""
        await self._publish(
            f"{self.config.topic_prefix}/auto_backup",
            "enabled" if enabled else "disabled",
            retain=True
        )
