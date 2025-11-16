"""
Tests for the database module.

Tests cover:
- Database initialization and schema creation
- File operations (CRUD, deduplication)
- Session management
- Statistics and queries
- Index functionality
"""
import pytest
import os
from pathlib import Path
from datetime import datetime

from src.database import BackupDatabase, calculate_file_hash


@pytest.mark.unit
@pytest.mark.db
class TestDatabaseInitialization:
    """Test database initialization and schema creation."""

    @pytest.mark.asyncio
    async def test_database_initialization(self, temp_db_path):
        """Test that database initializes correctly."""
        db = BackupDatabase(temp_db_path)
        await db.initialize()

        assert os.path.exists(temp_db_path)
        assert db.db is not None

        await db.close()

    @pytest.mark.asyncio
    async def test_tables_created(self, test_db):
        """Test that all required tables are created."""
        # Check files table exists
        cursor = await test_db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
        )
        result = await cursor.fetchone()
        assert result is not None

        # Check backup_sessions table exists
        cursor = await test_db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='backup_sessions'"
        )
        result = await cursor.fetchone()
        assert result is not None

    @pytest.mark.asyncio
    async def test_indexes_created(self, test_db):
        """Test that performance indexes are created."""
        cursor = await test_db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = await cursor.fetchall()
        index_names = [idx[0] for idx in indexes]

        assert 'idx_files_hash' in index_names
        assert 'idx_files_status' in index_names
        assert 'idx_sessions_status' in index_names

    @pytest.mark.asyncio
    async def test_files_table_schema(self, test_db):
        """Test that files table has correct schema."""
        cursor = await test_db.db.execute("PRAGMA table_info(files)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]

        expected_columns = [
            'id', 'file_path', 'file_name', 'file_size', 'md5_hash',
            'source_device', 'status', 'created_at', 'updated_at',
            'immich_uploaded', 'unraid_uploaded', 'immich_asset_id',
            'unraid_path', 'backup_date', 'error_message', 'retry_count'
        ]

        for col in expected_columns:
            assert col in column_names

    @pytest.mark.asyncio
    async def test_sessions_table_schema(self, test_db):
        """Test that backup_sessions table has correct schema."""
        cursor = await test_db.db.execute("PRAGMA table_info(backup_sessions)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]

        expected_columns = [
            'id', 'session_id', 'device_name', 'device_path',
            'start_time', 'end_time', 'status', 'total_files',
            'completed_files', 'failed_files', 'total_bytes', 'transferred_bytes'
        ]

        for col in expected_columns:
            assert col in column_names


@pytest.mark.unit
@pytest.mark.db
class TestFileOperations:
    """Test file-related database operations."""

    @pytest.mark.asyncio
    async def test_add_file(self, test_db):
        """Test adding a file to the database."""
        file_info = {
            'file_path': '/media/sd/IMG_001.jpg',
            'file_name': 'IMG_001.jpg',
            'file_size': 2048000,
            'md5_hash': 'abc123def456',
            'source_device': 'SD_CARD_001',
            'status': 'new',
            'backup_date': '2025-01-15'
        }

        file_id = await test_db.add_file(file_info)
        assert file_id > 0

        # Verify file was added
        cursor = await test_db.db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = await cursor.fetchone()
        assert row is not None
        assert row['file_name'] == 'IMG_001.jpg'
        assert row['md5_hash'] == 'abc123def456'

    @pytest.mark.asyncio
    async def test_file_exists_deduplication(self, test_db):
        """Test file existence check for deduplication."""
        file_info = {
            'file_path': '/media/sd/IMG_001.jpg',
            'file_name': 'IMG_001.jpg',
            'file_size': 2048000,
            'md5_hash': 'abc123def456',
            'source_device': 'SD_CARD_001',
            'status': 'new',
            'backup_date': '2025-01-15'
        }

        # File should not exist initially
        exists = await test_db.file_exists('abc123def456', 'SD_CARD_001')
        assert exists is False

        # Add file
        await test_db.add_file(file_info)

        # File should now exist
        exists = await test_db.file_exists('abc123def456', 'SD_CARD_001')
        assert exists is True

        # Same hash but different device should not exist
        exists = await test_db.file_exists('abc123def456', 'SD_CARD_002')
        assert exists is False

    @pytest.mark.asyncio
    async def test_unique_constraint(self, test_db):
        """Test that unique constraint on (md5_hash, source_device) works."""
        file_info = {
            'file_path': '/media/sd/IMG_001.jpg',
            'file_name': 'IMG_001.jpg',
            'file_size': 2048000,
            'md5_hash': 'abc123def456',
            'source_device': 'SD_CARD_001',
            'status': 'new',
            'backup_date': '2025-01-15'
        }

        # Add file first time
        await test_db.add_file(file_info)

        # Try to add same file again (should fail due to unique constraint)
        with pytest.raises(Exception):  # aiosqlite.IntegrityError
            await test_db.add_file(file_info)

    @pytest.mark.asyncio
    async def test_update_file_status(self, test_db):
        """Test updating file status."""
        file_info = {
            'file_path': '/media/sd/IMG_001.jpg',
            'file_name': 'IMG_001.jpg',
            'file_size': 2048000,
            'md5_hash': 'abc123def456',
            'source_device': 'SD_CARD_001',
            'status': 'new',
            'backup_date': '2025-01-15'
        }

        file_id = await test_db.add_file(file_info)

        # Update status
        await test_db.update_file_status(
            file_id,
            status='backing_up',
            immich_uploaded=True,
            immich_asset_id='asset-123'
        )

        # Verify update
        cursor = await test_db.db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = await cursor.fetchone()
        assert row['status'] == 'backing_up'
        assert row['immich_uploaded'] == 1
        assert row['immich_asset_id'] == 'asset-123'

    @pytest.mark.asyncio
    async def test_update_file_with_error(self, test_db):
        """Test updating file with error message."""
        file_info = {
            'file_path': '/media/sd/IMG_001.jpg',
            'file_name': 'IMG_001.jpg',
            'file_size': 2048000,
            'md5_hash': 'abc123def456',
            'source_device': 'SD_CARD_001',
            'status': 'new',
            'backup_date': '2025-01-15'
        }

        file_id = await test_db.add_file(file_info)

        # Update with error
        await test_db.update_file_status(
            file_id,
            status='failed',
            error_message='Connection timeout'
        )

        # Verify update
        cursor = await test_db.db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = await cursor.fetchone()
        assert row['status'] == 'failed'
        assert row['error_message'] == 'Connection timeout'

    @pytest.mark.asyncio
    async def test_increment_retry_count(self, test_db):
        """Test incrementing retry count."""
        file_info = {
            'file_path': '/media/sd/IMG_001.jpg',
            'file_name': 'IMG_001.jpg',
            'file_size': 2048000,
            'md5_hash': 'abc123def456',
            'source_device': 'SD_CARD_001',
            'status': 'new',
            'backup_date': '2025-01-15'
        }

        file_id = await test_db.add_file(file_info)

        # Increment retry count multiple times
        await test_db.increment_retry_count(file_id)
        await test_db.increment_retry_count(file_id)
        await test_db.increment_retry_count(file_id)

        # Verify count
        cursor = await test_db.db.execute("SELECT retry_count FROM files WHERE id = ?", (file_id,))
        row = await cursor.fetchone()
        assert row['retry_count'] == 3

    @pytest.mark.asyncio
    async def test_get_files_by_status(self, test_db):
        """Test retrieving files by status."""
        # Add multiple files with different statuses
        for i in range(5):
            file_info = {
                'file_path': f'/media/sd/IMG_{i:03d}.jpg',
                'file_name': f'IMG_{i:03d}.jpg',
                'file_size': 2048000 + i * 1000,
                'md5_hash': f'hash_{i}',
                'source_device': 'SD_CARD_001',
                'status': 'completed' if i < 3 else 'failed',
                'backup_date': '2025-01-15'
            }
            await test_db.add_file(file_info)

        # Get completed files
        completed = await test_db.get_files_by_status('completed')
        assert len(completed) == 3

        # Get failed files
        failed = await test_db.get_files_by_status('failed')
        assert len(failed) == 2


@pytest.mark.unit
@pytest.mark.db
class TestSessionOperations:
    """Test session-related database operations."""

    @pytest.mark.asyncio
    async def test_create_session(self, test_db):
        """Test creating a backup session."""
        session_info = {
            'session_id': 'session-123',
            'device_name': 'SD_CARD_001',
            'device_path': '/media/sd',
            'status': 'backing_up',
            'total_files': 10,
            'total_bytes': 20480000
        }

        session_id = await test_db.create_session(session_info)
        assert session_id > 0

        # Verify session was created
        session = await test_db.get_session('session-123')
        assert session is not None
        assert session['device_name'] == 'SD_CARD_001'
        assert session['total_files'] == 10

    @pytest.mark.asyncio
    async def test_update_session_progress(self, test_db):
        """Test updating session progress."""
        session_info = {
            'session_id': 'session-123',
            'device_name': 'SD_CARD_001',
            'device_path': '/media/sd',
            'status': 'backing_up',
            'total_files': 10,
            'total_bytes': 20480000
        }

        await test_db.create_session(session_info)

        # Update progress
        await test_db.update_session(
            'session-123',
            completed_files=5,
            transferred_bytes=10240000
        )

        # Verify update
        session = await test_db.get_session('session-123')
        assert session['completed_files'] == 5
        assert session['transferred_bytes'] == 10240000

    @pytest.mark.asyncio
    async def test_complete_session(self, test_db):
        """Test completing a session sets end_time."""
        session_info = {
            'session_id': 'session-123',
            'device_name': 'SD_CARD_001',
            'device_path': '/media/sd',
            'status': 'backing_up',
            'total_files': 10,
            'total_bytes': 20480000
        }

        await test_db.create_session(session_info)

        # Complete session
        await test_db.update_session('session-123', status='completed')

        # Verify end_time is set
        session = await test_db.get_session('session-123')
        assert session['status'] == 'completed'
        assert session['end_time'] is not None

    @pytest.mark.asyncio
    async def test_get_active_session(self, test_db):
        """Test retrieving active session."""
        # Create multiple sessions
        for i in range(3):
            session_info = {
                'session_id': f'session-{i}',
                'device_name': 'SD_CARD_001',
                'device_path': '/media/sd',
                'status': 'backing_up' if i == 2 else 'completed',
                'total_files': 10,
                'total_bytes': 20480000
            }
            await test_db.create_session(session_info)

        # Get active session (should be the one with status 'backing_up')
        active = await test_db.get_active_session()
        assert active is not None
        assert active['session_id'] == 'session-2'
        assert active['status'] == 'backing_up'

    @pytest.mark.asyncio
    async def test_get_active_session_none(self, test_db):
        """Test get_active_session returns None when no active sessions."""
        # Create completed session
        session_info = {
            'session_id': 'session-123',
            'device_name': 'SD_CARD_001',
            'device_path': '/media/sd',
            'status': 'completed',
            'total_files': 10,
            'total_bytes': 20480000
        }
        await test_db.create_session(session_info)

        # Should return None
        active = await test_db.get_active_session()
        assert active is None

    @pytest.mark.asyncio
    async def test_get_recent_sessions(self, test_db):
        """Test retrieving recent sessions."""
        # Create multiple sessions
        for i in range(15):
            session_info = {
                'session_id': f'session-{i}',
                'device_name': 'SD_CARD_001',
                'device_path': '/media/sd',
                'status': 'completed',
                'total_files': 10,
                'total_bytes': 20480000
            }
            await test_db.create_session(session_info)

        # Get recent sessions (default limit 10)
        recent = await test_db.get_recent_sessions()
        assert len(recent) == 10

        # Get with custom limit
        recent = await test_db.get_recent_sessions(limit=5)
        assert len(recent) == 5


@pytest.mark.unit
@pytest.mark.db
class TestStatistics:
    """Test statistics and aggregation queries."""

    @pytest.mark.asyncio
    async def test_get_stats_empty(self, test_db):
        """Test getting stats from empty database."""
        stats = await test_db.get_stats()
        assert stats['total_files'] == 0

    @pytest.mark.asyncio
    async def test_get_stats(self, test_db):
        """Test getting overall statistics."""
        # Add files with various statuses
        files_data = [
            ('completed', 1000000),
            ('completed', 2000000),
            ('completed', 3000000),
            ('failed', 500000),
            ('backing_up', 1500000),
        ]

        for i, (status, size) in enumerate(files_data):
            file_info = {
                'file_path': f'/media/sd/IMG_{i:03d}.jpg',
                'file_name': f'IMG_{i:03d}.jpg',
                'file_size': size,
                'md5_hash': f'hash_{i}',
                'source_device': 'SD_CARD_001',
                'status': status,
                'backup_date': '2025-01-15'
            }
            await test_db.add_file(file_info)

        # Get stats
        stats = await test_db.get_stats()
        assert stats['total_files'] == 5
        assert stats['completed_files'] == 3
        assert stats['failed_files'] == 1
        assert stats['in_progress_files'] == 1
        assert stats['total_size'] == 8000000


@pytest.mark.unit
class TestHashCalculation:
    """Test file hash calculation."""

    def test_calculate_file_hash(self, tmp_path):
        """Test MD5 hash calculation."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        # Calculate hash
        hash1 = calculate_file_hash(test_file)
        assert len(hash1) == 32  # MD5 hash is 32 characters

        # Hash should be consistent
        hash2 = calculate_file_hash(test_file)
        assert hash1 == hash2

    def test_calculate_hash_different_files(self, tmp_path):
        """Test that different files have different hashes."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"

        file1.write_text("Content 1")
        file2.write_text("Content 2")

        hash1 = calculate_file_hash(file1)
        hash2 = calculate_file_hash(file2)

        assert hash1 != hash2

    def test_calculate_hash_large_file(self, tmp_path):
        """Test hash calculation for large file."""
        # Create a large file (10MB)
        large_file = tmp_path / "large.bin"
        large_file.write_bytes(b'x' * (10 * 1024 * 1024))

        hash_result = calculate_file_hash(large_file)
        assert len(hash_result) == 32


@pytest.mark.unit
@pytest.mark.db
class TestDatabaseConcurrency:
    """Test database operations under concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_file_additions(self, test_db):
        """Test adding multiple files concurrently."""
        import asyncio

        async def add_file(index):
            file_info = {
                'file_path': f'/media/sd/IMG_{index:03d}.jpg',
                'file_name': f'IMG_{index:03d}.jpg',
                'file_size': 2048000 + index,
                'md5_hash': f'hash_{index}',
                'source_device': 'SD_CARD_001',
                'status': 'new',
                'backup_date': '2025-01-15'
            }
            return await test_db.add_file(file_info)

        # Add 10 files concurrently
        tasks = [add_file(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        # All should succeed
        assert len(results) == 10
        assert all(r > 0 for r in results)

        # Verify all files in database
        cursor = await test_db.db.execute("SELECT COUNT(*) as count FROM files")
        row = await cursor.fetchone()
        assert row['count'] == 10
