"""FastAPI web UI for SnapSync"""
import logging
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

logger = logging.getLogger(__name__)


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

    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return JSONResponse({"status": "healthy"})

    return app
