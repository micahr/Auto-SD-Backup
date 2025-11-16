"""FastAPI web UI for SnapSync"""
import logging
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ConfigUpdate(BaseModel):
    """Configuration update request"""
    immich_api_key: Optional[str] = None
    immich_url: Optional[str] = None
    immich_enabled: Optional[bool] = None
    unraid_username: Optional[str] = None
    unraid_password: Optional[str] = None
    unraid_host: Optional[str] = None
    unraid_share: Optional[str] = None
    unraid_enabled: Optional[bool] = None
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_broker: Optional[str] = None
    mqtt_enabled: Optional[bool] = None


def create_app(service_manager) -> FastAPI:
    """Create FastAPI application"""
    app = FastAPI(
        title="SnapSync",
        description="SD Card Backup Service",
        version="1.0.0"
    )

    # Setup templates
    templates_dir = Path(__file__).parent / "templates"
    templates_dir.mkdir(exist_ok=True)
    templates = Jinja2Templates(directory=str(templates_dir))

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        """Main dashboard"""
        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "service_name": "SnapSync"
        })

    @app.get("/api/status")
    async def get_status():
        """Get current service status"""
        try:
            status = await service_manager.get_status()
            return JSONResponse(status)
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/sessions")
    async def get_sessions(limit: int = 10):
        """Get recent backup sessions"""
        try:
            sessions = await service_manager.database.get_recent_sessions(limit)
            return JSONResponse(sessions)
        except Exception as e:
            logger.error(f"Error getting sessions: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/session/{session_id}")
    async def get_session(session_id: str):
        """Get specific session details"""
        try:
            session = await service_manager.database.get_session(session_id)
            if session:
                return JSONResponse(session)
            return JSONResponse({"error": "Session not found"}, status_code=404)
        except Exception as e:
            logger.error(f"Error getting session: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/stats")
    async def get_stats():
        """Get overall backup statistics"""
        try:
            stats = await service_manager.database.get_stats()
            return JSONResponse(stats)
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/files/failed")
    async def get_failed_files():
        """Get list of failed files"""
        try:
            files = await service_manager.database.get_files_by_status('failed')
            return JSONResponse(files)
        except Exception as e:
            logger.error(f"Error getting failed files: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/retry/{file_id}")
    async def retry_file(file_id: int):
        """Retry a failed file"""
        try:
            # This would need to be implemented in the service manager
            return JSONResponse({"message": "Retry not yet implemented"})
        except Exception as e:
            logger.error(f"Error retrying file: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/pending")
    async def get_pending_backups():
        """Get list of pending backups"""
        try:
            pending = await service_manager.get_pending_backups()
            return JSONResponse(pending)
        except Exception as e:
            logger.error(f"Error getting pending backups: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/approve/{backup_id}")
    async def approve_backup(backup_id: str):
        """Approve a pending backup"""
        try:
            success = await service_manager.approve_backup(backup_id)
            if success:
                return JSONResponse({"message": "Backup approved", "backup_id": backup_id})
            return JSONResponse({"error": "Backup not found"}, status_code=404)
        except Exception as e:
            logger.error(f"Error approving backup: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/reject/{backup_id}")
    async def reject_backup(backup_id: str):
        """Reject a pending backup"""
        try:
            success = await service_manager.reject_backup(backup_id)
            if success:
                return JSONResponse({"message": "Backup rejected", "backup_id": backup_id})
            return JSONResponse({"error": "Backup not found"}, status_code=404)
        except Exception as e:
            logger.error(f"Error rejecting backup: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/auto-backup/enable")
    async def enable_auto_backup():
        """Enable auto-backup"""
        try:
            await service_manager.set_auto_backup(True)
            return JSONResponse({"message": "Auto-backup enabled"})
        except Exception as e:
            logger.error(f"Error enabling auto-backup: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/auto-backup/disable")
    async def disable_auto_backup():
        """Disable auto-backup"""
        try:
            await service_manager.set_auto_backup(False)
            return JSONResponse({"message": "Auto-backup disabled"})
        except Exception as e:
            logger.error(f"Error disabling auto-backup: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/config")
    async def get_config():
        """Get current configuration (with sensitive values masked)"""
        try:
            config = service_manager.config

            # Return configuration with sensitive values masked
            config_data = {
                "immich": {
                    "enabled": config.immich.enabled,
                    "url": config.immich.url,
                    "api_key": "***" if config.immich.api_key else "",
                    "timeout": config.immich.timeout,
                    "organize_by_date": config.immich.organize_by_date,
                },
                "unraid": {
                    "enabled": config.unraid.enabled,
                    "protocol": config.unraid.protocol,
                    "host": config.unraid.host,
                    "share": config.unraid.share,
                    "path": config.unraid.path,
                    "username": config.unraid.username,
                    "password": "***" if config.unraid.password else "",
                    "mount_point": config.unraid.mount_point,
                    "organize_by_date": config.unraid.organize_by_date,
                },
                "mqtt": {
                    "enabled": config.mqtt.enabled,
                    "broker": config.mqtt.broker,
                    "port": config.mqtt.port,
                    "username": config.mqtt.username,
                    "password": "***" if config.mqtt.password else "",
                    "discovery_prefix": config.mqtt.discovery_prefix,
                    "topic_prefix": config.mqtt.topic_prefix,
                    "client_id": config.mqtt.client_id,
                }
            }
            return JSONResponse(config_data)
        except Exception as e:
            logger.error(f"Error getting config: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/config")
    async def update_config(config_update: ConfigUpdate):
        """Update configuration"""
        try:
            config = service_manager.config
            env_updates = {}

            # Update Immich settings
            if config_update.immich_enabled is not None:
                config.immich.enabled = config_update.immich_enabled
            if config_update.immich_url is not None:
                config.immich.url = config_update.immich_url
            if config_update.immich_api_key is not None and config_update.immich_api_key != "***":
                config.immich.api_key = config_update.immich_api_key
                env_updates['IMMICH_API_KEY'] = config_update.immich_api_key

            # Update Unraid settings
            if config_update.unraid_enabled is not None:
                config.unraid.enabled = config_update.unraid_enabled
            if config_update.unraid_host is not None:
                config.unraid.host = config_update.unraid_host
            if config_update.unraid_share is not None:
                config.unraid.share = config_update.unraid_share
            if config_update.unraid_username is not None:
                config.unraid.username = config_update.unraid_username
                env_updates['UNRAID_USERNAME'] = config_update.unraid_username
            if config_update.unraid_password is not None and config_update.unraid_password != "***":
                config.unraid.password = config_update.unraid_password
                env_updates['UNRAID_PASSWORD'] = config_update.unraid_password

            # Update MQTT settings
            if config_update.mqtt_enabled is not None:
                config.mqtt.enabled = config_update.mqtt_enabled
            if config_update.mqtt_broker is not None:
                config.mqtt.broker = config_update.mqtt_broker
            if config_update.mqtt_username is not None:
                config.mqtt.username = config_update.mqtt_username
                env_updates['MQTT_USERNAME'] = config_update.mqtt_username
            if config_update.mqtt_password is not None and config_update.mqtt_password != "***":
                config.mqtt.password = config_update.mqtt_password
                env_updates['MQTT_PASSWORD'] = config_update.mqtt_password

            # Save to .env file
            if env_updates:
                from config import Config
                Config.save_env_vars(env_updates)

            # Save non-sensitive config to YAML file
            config.to_yaml('config.yaml')

            # Validate the updated configuration
            if not config.validate():
                return JSONResponse({"error": "Invalid configuration"}, status_code=400)

            return JSONResponse({"message": "Configuration updated successfully"})
        except Exception as e:
            logger.error(f"Error updating config: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return JSONResponse({"status": "healthy"})

    return app
