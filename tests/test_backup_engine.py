"""
Tests for the backup engine module.

Tests cover:
- SD card scanning and file discovery
- File filtering (extensions, size)
- Deduplication logic
- Parallel and sequential uploads
- Retry logic on failures
- Upload verification
- Session management and progress tracking
"""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from src.backup_engine import BackupEngine
from src.sd_detector import SDCard


@pytest.fixture
def sd_card(tmp_path):
    """Create a mock SD card."""
    return SDCard(
        device_name="TEST_SD_CARD",
        device_path="/dev/sdc1",
        mount_point=str(tmp_path),
        size_bytes=32000000000,
        label="TEST_CARD"
    )


@pytest.fixture
def backup_engine(sample_config, test_db, mock_immich_client, mock_unraid_client):
    """Create a BackupEngine instance with mocked clients."""
    return BackupEngine(
        config=sample_config,
        database=test_db,
        immich_client=mock_immich_client,
        unraid_client=mock_unraid_client
    )


@pytest.fixture
def backup_engine_with_callback(sample_config, test_db, mock_immich_client, mock_unraid_client):
    """Create a BackupEngine instance with progress callback."""
    callback = AsyncMock()
    engine = BackupEngine(
        config=sample_config,
        database=test_db,
        immich_client=mock_immich_client,
        unraid_client=mock_unraid_client,
        progress_callback=callback
    )
    engine.callback_mock = callback  # Store for access in tests
    return engine


@pytest.mark.unit
@pytest.mark.backup
class TestBackupEngineInitialization:
    """Test BackupEngine initialization."""

    def test_initialization(self, backup_engine, sample_config):
        """Test BackupEngine initializes correctly."""
        assert backup_engine.config == sample_config
        assert backup_engine.database is not None
        assert backup_engine.immich_client is not None
        assert backup_engine.unraid_client is not None
        assert backup_engine._current_session_id is None

    def test_semaphore_initialization(self, backup_engine, sample_config):
        """Test that semaphore is initialized with correct concurrency."""
        assert backup_engine._semaphore._value == sample_config.backup.concurrent_files


@pytest.mark.unit
@pytest.mark.backup
class TestFileScanning:
    """Test SD card scanning functionality."""

    @pytest.mark.asyncio
    async def test_scan_sd_card_empty(self, backup_engine, sd_card):
        """Test scanning an empty SD card."""
        files = await backup_engine._scan_sd_card(sd_card)
        assert files == []

    @pytest.mark.asyncio
    async def test_scan_sd_card_with_files(self, backup_engine, sd_card, tmp_path):
        """Test scanning SD card with valid files."""
        # Create test files
        (tmp_path / "IMG_001.jpg").write_bytes(b"test image content" * 100)
        (tmp_path / "VID_001.mp4").write_bytes(b"test video content" * 200)

        files = await backup_engine._scan_sd_card(sd_card)

        assert len(files) == 2
        assert any(f['file_name'] == 'IMG_001.jpg' for f in files)
        assert any(f['file_name'] == 'VID_001.mp4' for f in files)

    @pytest.mark.asyncio
    async def test_scan_filters_by_extension(self, backup_engine, sd_card, tmp_path):
        """Test that scanning filters files by extension."""
        # Create files with various extensions
        (tmp_path / "valid.jpg").write_bytes(b"valid" * 1000)
        (tmp_path / "invalid.txt").write_bytes(b"invalid" * 1000)
        (tmp_path / "document.pdf").write_bytes(b"pdf" * 1000)

        files = await backup_engine._scan_sd_card(sd_card)

        # Only .jpg should be included
        assert len(files) == 1
        assert files[0]['file_name'] == 'valid.jpg'

    @pytest.mark.asyncio
    async def test_scan_filters_by_size(self, backup_engine, sd_card, tmp_path, sample_config):
        """Test that scanning filters files by minimum size."""
        # Create files with different sizes
        (tmp_path / "large.jpg").write_bytes(b"x" * 10000)  # Larger than min_size
        (tmp_path / "tiny.jpg").write_bytes(b"x" * 100)     # Smaller than min_size

        files = await backup_engine._scan_sd_card(sd_card)

        # Only large file should be included
        assert len(files) == 1
        assert files[0]['file_name'] == 'large.jpg'

    @pytest.mark.asyncio
    async def test_scan_nested_directories(self, backup_engine, sd_card, tmp_path):
        """Test scanning nested directory structure."""
        # Create nested directories (like DCIM structure)
        dcim_dir = tmp_path / "DCIM" / "100CANON"
        dcim_dir.mkdir(parents=True)

        (dcim_dir / "IMG_001.jpg").write_bytes(b"test" * 1000)
        (dcim_dir / "IMG_002.jpg").write_bytes(b"test" * 1000)

        files = await backup_engine._scan_sd_card(sd_card)

        assert len(files) == 2

    @pytest.mark.asyncio
    async def test_scan_calculates_md5(self, backup_engine, sd_card, tmp_path):
        """Test that scanning calculates MD5 hash."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test content" * 100)

        files = await backup_engine._scan_sd_card(sd_card)

        assert len(files) == 1
        assert 'md5_hash' in files[0]
        assert len(files[0]['md5_hash']) == 32  # MD5 is 32 chars

    @pytest.mark.asyncio
    async def test_scan_deduplication(self, backup_engine, sd_card, tmp_path, test_db):
        """Test that already backed up files are skipped."""
        # Create a file
        test_file = tmp_path / "test.jpg"
        content = b"test content" * 100
        test_file.write_bytes(content)

        # First scan should find the file
        files = await backup_engine._scan_sd_card(sd_card)
        assert len(files) == 1

        # Add file to database (simulate it was backed up)
        file_info = files[0]
        await test_db.add_file(file_info)

        # Second scan should skip the file
        files = await backup_engine._scan_sd_card(sd_card)
        assert len(files) == 0

    @pytest.mark.asyncio
    async def test_scan_extracts_backup_date(self, backup_engine, sd_card, tmp_path):
        """Test that backup date is extracted from file mtime."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test" * 1000)

        files = await backup_engine._scan_sd_card(sd_card)

        assert len(files) == 1
        assert 'backup_date' in files[0]
        # Should be in YYYY/MM/DD format
        assert '/' in files[0]['backup_date']


@pytest.mark.unit
@pytest.mark.backup
class TestFileFiltering:
    """Test file filtering logic."""

    def test_should_backup_file_valid_extensions(self, backup_engine, tmp_path):
        """Test that valid extensions are accepted."""
        valid_files = [
            tmp_path / "test.jpg",
            tmp_path / "test.JPG",  # Case insensitive
            tmp_path / "test.jpeg",
            tmp_path / "test.png",
            tmp_path / "test.mp4",
        ]

        for file_path in valid_files:
            assert backup_engine._should_backup_file(file_path) is True

    def test_should_backup_file_invalid_extensions(self, backup_engine, tmp_path):
        """Test that invalid extensions are rejected."""
        invalid_files = [
            tmp_path / "test.txt",
            tmp_path / "test.pdf",
            tmp_path / "test.doc",
            tmp_path / "test.zip",
        ]

        for file_path in invalid_files:
            assert backup_engine._should_backup_file(file_path) is False

    def test_should_backup_file_case_insensitive(self, backup_engine, tmp_path):
        """Test that extension matching is case insensitive."""
        assert backup_engine._should_backup_file(tmp_path / "test.JPG") is True
        assert backup_engine._should_backup_file(tmp_path / "test.Mp4") is True
        assert backup_engine._should_backup_file(tmp_path / "test.PnG") is True


@pytest.mark.unit
@pytest.mark.backup
class TestBackupSession:
    """Test backup session management."""

    @pytest.mark.asyncio
    async def test_start_backup_creates_session(self, backup_engine, sd_card, tmp_path, test_db):
        """Test that starting backup creates a session."""
        # Create a test file
        (tmp_path / "test.jpg").write_bytes(b"test" * 1000)

        session_id = await backup_engine.start_backup(sd_card)

        assert session_id is not None

        # Wait a bit for background task to start
        await asyncio.sleep(0.1)

        # Verify session in database
        session = await test_db.get_session(session_id)
        assert session is not None
        assert session['device_name'] == "TEST_SD_CARD"
        assert session['status'] in ['backing_up', 'completed', 'completed_with_errors']

    @pytest.mark.asyncio
    async def test_start_backup_no_files(self, backup_engine, sd_card, test_db):
        """Test starting backup with no files to backup."""
        session_id = await backup_engine.start_backup(sd_card)

        # Session should be created and immediately completed
        session = await test_db.get_session(session_id)
        assert session['status'] == 'completed'
        assert session['total_files'] == 0

    @pytest.mark.asyncio
    async def test_get_session_status(self, backup_engine, sd_card, tmp_path):
        """Test getting session status."""
        (tmp_path / "test.jpg").write_bytes(b"test" * 1000)

        session_id = await backup_engine.start_backup(sd_card)
        status = await backup_engine.get_session_status(session_id)

        assert status is not None
        assert status['session_id'] == session_id

    @pytest.mark.asyncio
    async def test_get_active_session(self, backup_engine, sd_card, tmp_path):
        """Test getting active session."""
        # Create multiple files to ensure backup takes some time
        for i in range(3):
            (tmp_path / f"test_{i}.jpg").write_bytes(b"test" * 1000)

        session_id = await backup_engine.start_backup(sd_card)

        # Give it a moment to start
        await asyncio.sleep(0.1)

        active = await backup_engine.get_active_session()
        # Session might have completed already in fast environments
        if active:
            assert active['session_id'] == session_id


@pytest.mark.unit
@pytest.mark.backup
class TestFileUpload:
    """Test file upload functionality."""

    @pytest.mark.asyncio
    async def test_backup_single_file_success(self, backup_engine, tmp_path, test_db):
        """Test successful single file backup."""
        # Create a test file
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test content" * 100)

        from src.database import calculate_file_hash
        file_hash = calculate_file_hash(test_file)

        file_info = {
            'file_path': str(test_file),
            'file_name': 'test.jpg',
            'file_size': test_file.stat().st_size,
            'md5_hash': file_hash,
            'source_device': 'TEST_DEVICE',
            'status': 'new',
            'backup_date': '2025/01/15',
            'created_at': datetime.now()
        }

        success, bytes_transferred = await backup_engine._backup_single_file('session-123', file_info)

        assert success is True
        assert bytes_transferred == file_info['file_size']

    @pytest.mark.asyncio
    async def test_backup_to_immich_only(self, backup_engine, tmp_path, sample_config):
        """Test backup when only Immich is enabled."""
        sample_config.immich.enabled = True
        sample_config.unraid.enabled = False

        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test" * 1000)

        from src.database import calculate_file_hash
        file_hash = calculate_file_hash(test_file)

        file_info = {
            'file_path': str(test_file),
            'file_name': 'test.jpg',
            'file_size': test_file.stat().st_size,
            'md5_hash': file_hash,
            'source_device': 'TEST_DEVICE',
            'status': 'new',
            'backup_date': '2025/01/15',
            'created_at': datetime.now()
        }

        success, _ = await backup_engine._backup_single_file('session-123', file_info)

        assert success is True
        assert backup_engine.immich_client.upload_asset.called

    @pytest.mark.asyncio
    async def test_backup_to_unraid_only(self, backup_engine, tmp_path, sample_config):
        """Test backup when only Unraid is enabled."""
        sample_config.immich.enabled = False
        sample_config.unraid.enabled = True

        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test" * 1000)

        from src.database import calculate_file_hash
        file_hash = calculate_file_hash(test_file)

        file_info = {
            'file_path': str(test_file),
            'file_name': 'test.jpg',
            'file_size': test_file.stat().st_size,
            'md5_hash': file_hash,
            'source_device': 'TEST_DEVICE',
            'status': 'new',
            'backup_date': '2025/01/15',
            'created_at': datetime.now()
        }

        success, _ = await backup_engine._backup_single_file('session-123', file_info)

        assert success is True
        assert backup_engine.unraid_client.upload_file.called

    @pytest.mark.asyncio
    async def test_parallel_uploads(self, backup_engine, tmp_path, sample_config):
        """Test parallel uploads to multiple destinations."""
        sample_config.backup.parallel = True
        sample_config.immich.enabled = True
        sample_config.unraid.enabled = True

        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test" * 1000)

        from src.database import calculate_file_hash
        file_hash = calculate_file_hash(test_file)

        file_info = {
            'file_path': str(test_file),
            'file_name': 'test.jpg',
            'file_size': test_file.stat().st_size,
            'md5_hash': file_hash,
            'source_device': 'TEST_DEVICE',
            'status': 'new',
            'backup_date': '2025/01/15',
            'created_at': datetime.now()
        }

        success, _ = await backup_engine._backup_single_file('session-123', file_info)

        assert success is True
        assert backup_engine.immich_client.upload_asset.called
        assert backup_engine.unraid_client.upload_file.called

    @pytest.mark.asyncio
    async def test_sequential_uploads(self, backup_engine, tmp_path, sample_config):
        """Test sequential uploads when parallel is disabled."""
        sample_config.backup.parallel = False
        sample_config.immich.enabled = True
        sample_config.unraid.enabled = True

        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test" * 1000)

        from src.database import calculate_file_hash
        file_hash = calculate_file_hash(test_file)

        file_info = {
            'file_path': str(test_file),
            'file_name': 'test.jpg',
            'file_size': test_file.stat().st_size,
            'md5_hash': file_hash,
            'source_device': 'TEST_DEVICE',
            'status': 'new',
            'backup_date': '2025/01/15',
            'created_at': datetime.now()
        }

        success, _ = await backup_engine._backup_single_file('session-123', file_info)

        assert success is True


@pytest.mark.unit
@pytest.mark.backup
class TestUploadFailures:
    """Test handling of upload failures."""

    @pytest.mark.asyncio
    async def test_immich_upload_failure(self, backup_engine, tmp_path, mock_immich_client):
        """Test handling of Immich upload failure."""
        # Make Immich upload fail
        mock_immich_client.upload_asset.side_effect = Exception("Upload failed")

        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test" * 1000)

        from src.database import calculate_file_hash
        file_hash = calculate_file_hash(test_file)

        file_info = {
            'file_path': str(test_file),
            'file_name': 'test.jpg',
            'file_size': test_file.stat().st_size,
            'md5_hash': file_hash,
            'source_device': 'TEST_DEVICE',
            'status': 'new',
            'backup_date': '2025/01/15',
            'created_at': datetime.now(),
            'retry_count': 0
        }

        # Should retry based on config
        backup_engine.config.backup.max_retries = 0  # Disable retries for this test

        success, _ = await backup_engine._backup_single_file('session-123', file_info)

        # With Unraid also enabled and working, overall backup can still succeed
        # But if only Immich is enabled, it should fail
        backup_engine.config.unraid.enabled = False
        success, _ = await backup_engine._backup_single_file('session-123', file_info)
        assert success is False


@pytest.mark.unit
@pytest.mark.backup
class TestUploadVerification:
    """Test upload verification functionality."""

    @pytest.mark.asyncio
    async def test_verification_enabled(self, backup_engine, tmp_path, sample_config):
        """Test that verification is called when enabled."""
        sample_config.backup.verify_checksums = True

        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test" * 1000)

        from src.database import calculate_file_hash
        file_hash = calculate_file_hash(test_file)

        file_info = {
            'file_path': str(test_file),
            'file_name': 'test.jpg',
            'file_size': test_file.stat().st_size,
            'md5_hash': file_hash,
            'source_device': 'TEST_DEVICE',
            'status': 'new',
            'backup_date': '2025/01/15',
            'created_at': datetime.now()
        }

        await backup_engine._backup_single_file('session-123', file_info)

        # Verify that verification methods were called
        assert backup_engine.immich_client.verify_asset.called
        assert backup_engine.unraid_client.verify_file.called

    @pytest.mark.asyncio
    async def test_verification_disabled(self, backup_engine, tmp_path, sample_config, mock_immich_client, mock_unraid_client):
        """Test that verification is skipped when disabled."""
        sample_config.backup.verify_checksums = False

        # Reset call counts
        mock_immich_client.verify_asset.reset_mock()
        mock_unraid_client.verify_file.reset_mock()

        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"test" * 1000)

        from src.database import calculate_file_hash
        file_hash = calculate_file_hash(test_file)

        file_info = {
            'file_path': str(test_file),
            'file_name': 'test.jpg',
            'file_size': test_file.stat().st_size,
            'md5_hash': file_hash,
            'source_device': 'TEST_DEVICE',
            'status': 'new',
            'backup_date': '2025/01/15',
            'created_at': datetime.now()
        }

        await backup_engine._backup_single_file('session-123', file_info)

        # Verification should not be called
        assert not mock_immich_client.verify_asset.called
        assert not mock_unraid_client.verify_file.called


@pytest.mark.unit
@pytest.mark.backup
class TestProgressTracking:
    """Test progress tracking and callbacks."""

    @pytest.mark.asyncio
    async def test_progress_callback_called(self, backup_engine_with_callback, sd_card, tmp_path):
        """Test that progress callback is invoked."""
        # Create test files
        for i in range(3):
            (tmp_path / f"test_{i}.jpg").write_bytes(b"test" * 1000)

        session_id = await backup_engine_with_callback.start_backup(sd_card)

        # Wait for backup to complete
        await asyncio.sleep(0.5)

        # Callback should have been called
        assert backup_engine_with_callback.callback_mock.called


@pytest.mark.unit
@pytest.mark.backup
class TestConcurrencyControl:
    """Test concurrency control with semaphore."""

    @pytest.mark.asyncio
    async def test_concurrent_file_limit(self, backup_engine, sample_config):
        """Test that concurrent file limit is enforced."""
        # Set low concurrency for testing
        sample_config.backup.concurrent_files = 2

        # Create new engine with updated config
        from src.backup_engine import BackupEngine
        engine = BackupEngine(
            config=sample_config,
            database=backup_engine.database,
            immich_client=backup_engine.immich_client,
            unraid_client=backup_engine.unraid_client
        )

        # Semaphore should be initialized with correct value
        assert engine._semaphore._value == 2
