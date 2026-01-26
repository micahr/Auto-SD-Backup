"""Database module for tracking backup status"""
import aiosqlite
import hashlib
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class BackupDatabase:
    """SQLite database for tracking file backups"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        """Initialize database connection and create tables"""
        self.db = await aiosqlite.connect(self.db_path)
        self.db.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._migrate_schema()  # Ensure schema is up-to-date
        logger.info(f"Database initialized at {self.db_path}")

    async def _migrate_schema(self):
        """Perform simple schema migrations to keep the DB up-to-date."""
        # Migration for adding 'mount_point' to 'backup_sessions'
        cursor = await self.db.execute("PRAGMA table_info(backup_sessions)")
        columns = [row['name'] for row in await cursor.fetchall()]
        if 'mount_point' not in columns:
            logger.info("Applying schema migration: Adding 'mount_point' to 'backup_sessions' table.")
            await self.db.execute("ALTER TABLE backup_sessions ADD COLUMN mount_point TEXT")
            await self.db.commit()

    async def _create_tables(self):
        """Create database tables if they don't exist"""
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                md5_hash TEXT NOT NULL,
                source_device TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                immich_uploaded BOOLEAN DEFAULT 0,
                unraid_uploaded BOOLEAN DEFAULT 0,
                immich_asset_id TEXT,
                unraid_path TEXT,
                backup_date TEXT NOT NULL,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                UNIQUE(md5_hash, source_device)
            )
        """)

        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS backup_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                device_name TEXT NOT NULL,
                device_path TEXT NOT NULL,
                mount_point TEXT,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                status TEXT NOT NULL,
                total_files INTEGER DEFAULT 0,
                completed_files INTEGER DEFAULT 0,
                failed_files INTEGER DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                transferred_bytes INTEGER DEFAULT 0
            )
        """)

        # Create indexes for performance
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_hash ON files(md5_hash)
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_status ON backup_sessions(status)
        """)

        await self.db.commit()

    async def close(self):
        """Close database connection"""
        if self.db:
            await self.db.close()
            logger.info("Database connection closed")

    async def file_exists(self, md5_hash: str, source_device: str) -> bool:
        """Check if file with given hash already exists in database and is completed"""
        cursor = await self.db.execute(
            "SELECT id FROM files WHERE md5_hash = ? AND source_device = ? AND status = 'completed'",
            (md5_hash, source_device)
        )
        result = await cursor.fetchone()
        return result is not None

    async def file_exists_by_metadata(self, file_name: str, file_size: int, source_device: str) -> bool:
        """Check if file exists based on metadata (name, size, device) to avoid rehashing"""
        # We check for 'completed' status to ensure we don't skip files that failed previously
        cursor = await self.db.execute(
            "SELECT id FROM files WHERE file_name = ? AND file_size = ? AND source_device = ? AND status = 'completed'",
            (file_name, file_size, source_device)
        )
        result = await cursor.fetchone()
        return result is not None

    async def get_existing_files_metadata(self, source_device: str) -> set[tuple[str, int]]:
        """
        Get a set of (file_name, file_size) tuples for all completed backups from a device.
        Used for bulk checking to avoid N+1 queries.
        """
        cursor = await self.db.execute(
            "SELECT file_name, file_size FROM files WHERE source_device = ? AND status = 'completed'",
            (source_device,)
        )
        rows = await cursor.fetchall()
        return {(row['file_name'], row['file_size']) for row in rows}

    async def add_file(self, file_info: Dict[str, Any]) -> int:
        """Add a new file to the database or update existing failed one"""
        # SQLite 3.24.0+ supports ON CONFLICT DO UPDATE (UPSERT)
        query = """
            INSERT INTO files (
                file_path, file_name, file_size, md5_hash,
                source_device, status, backup_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(md5_hash, source_device) DO UPDATE SET
                status = excluded.status,
                file_path = excluded.file_path,
                updated_at = CURRENT_TIMESTAMP,
                error_message = NULL,
                retry_count = retry_count + 1
        """
        
        cursor = await self.db.execute(query, (
            file_info['file_path'],
            file_info['file_name'],
            file_info['file_size'],
            file_info['md5_hash'],
            file_info['source_device'],
            file_info['status'],
            file_info['backup_date'],
            file_info['created_at']
        ))
        await self.db.commit()
        return cursor.lastrowid

    async def update_file_status(
        self,
        file_id: int,
        status: str,
        error_message: Optional[str] = None,
        immich_uploaded: Optional[bool] = None,
        unraid_uploaded: Optional[bool] = None,
        immich_asset_id: Optional[str] = None,
        unraid_path: Optional[str] = None
    ):
        """Update file status and related fields"""
        updates = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params = [status]

        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        if immich_uploaded is not None:
            updates.append("immich_uploaded = ?")
            params.append(1 if immich_uploaded else 0)

        if unraid_uploaded is not None:
            updates.append("unraid_uploaded = ?")
            params.append(1 if unraid_uploaded else 0)

        if immich_asset_id is not None:
            updates.append("immich_asset_id = ?")
            params.append(immich_asset_id)

        if unraid_path is not None:
            updates.append("unraid_path = ?")
            params.append(unraid_path)

        params.append(file_id)

        await self.db.execute(
            f"UPDATE files SET {', '.join(updates)} WHERE id = ?",
            params
        )
        await self.db.commit()

    async def increment_retry_count(self, file_id: int):
        """Increment retry count for a file"""
        await self.db.execute(
            "UPDATE files SET retry_count = retry_count + 1 WHERE id = ?",
            (file_id,)
        )
        await self.db.commit()

    async def create_session(self, session_info: Dict[str, Any]) -> int:
        """Create a new backup session"""
        cursor = await self.db.execute("""
            INSERT INTO backup_sessions (
                session_id, device_name, device_path, mount_point, status, total_files, total_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session_info['session_id'],
            session_info['device_name'],
            session_info['device_path'],
            session_info.get('mount_point'),
            session_info['status'],
            session_info.get('total_files', 0),
            session_info.get('total_bytes', 0)
        ))
        await self.db.commit()
        return cursor.lastrowid

    async def update_session(
        self,
        session_id: str,
        status: Optional[str] = None,
        completed_files: Optional[int] = None,
        failed_files: Optional[int] = None,
        transferred_bytes: Optional[int] = None,
        total_files: Optional[int] = None,
        total_bytes: Optional[int] = None
    ):
        """Update backup session progress"""
        updates = []
        params = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status in ['completed', 'failed']:
                updates.append("end_time = CURRENT_TIMESTAMP")

        if completed_files is not None:
            updates.append("completed_files = ?")
            params.append(completed_files)

        if failed_files is not None:
            updates.append("failed_files = ?")
            params.append(failed_files)

        if transferred_bytes is not None:
            updates.append("transferred_bytes = ?")
            params.append(transferred_bytes)

        if total_files is not None:
            updates.append("total_files = ?")
            params.append(total_files)

        if total_bytes is not None:
            updates.append("total_bytes = ?")
            params.append(total_bytes)

        if not updates:
            return

        params.append(session_id)

        await self.db.execute(
            f"UPDATE backup_sessions SET {', '.join(updates)} WHERE session_id = ?",
            params
        )
        await self.db.commit()

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session information"""
        cursor = await self.db.execute(
            "SELECT * FROM backup_sessions WHERE session_id = ?",
            (session_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None

    async def get_active_session(self) -> Optional[Dict[str, Any]]:
        """Get currently active backup session"""
        cursor = await self.db.execute(
            "SELECT * FROM backup_sessions WHERE status = 'backing_up' ORDER BY start_time DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None

    async def get_recent_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent backup sessions"""
        cursor = await self.db.execute(
            "SELECT * FROM backup_sessions ORDER BY start_time DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_files_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Get all files with a specific status"""
        cursor = await self.db.execute(
            "SELECT * FROM files WHERE status = ? ORDER BY created_at DESC",
            (status,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_stats(self) -> Dict[str, Any]:
        """Get overall backup statistics"""
        cursor = await self.db.execute("""
            SELECT
                COUNT(*) as total_files,
                SUM(file_size) as total_size,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_files,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_files,
                SUM(CASE WHEN status = 'backing_up' THEN 1 ELSE 0 END) as in_progress_files
            FROM files
        """)
        row = await cursor.fetchone()
        return dict(row) if row else {}

    async def reset(self):
        """Delete all records from files and backup_sessions tables."""
        logger.warning("Resetting database - all backup history will be erased.")
        await self.db.execute("DELETE FROM files")
        await self.db.execute("DELETE FROM backup_sessions")
        await self.db.commit()
        logger.info("Database has been reset.")


def calculate_file_hash(file_path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Calculate MD5 hash of a file"""
    md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        while chunk := f.read(chunk_size):
            md5.update(chunk)
    return md5.hexdigest()
