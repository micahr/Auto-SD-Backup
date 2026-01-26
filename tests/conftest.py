"""
Pytest fixtures and configuration for SnapSync tests.

This module provides common fixtures used across all test modules.
"""
import asyncio
import os
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import Mock, AsyncMock

import pytest
from aiosqlite import Connection

from src.database import BackupDatabase
from src.config import Config, ServiceConfig, SDCardConfig, FilesConfig, ImmichConfig, UnraidConfig, MQTTConfig, BackupConfig


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for testing."""
    return tmp_path


@pytest.fixture
def temp_db_path(tmp_path):
    """Create a temporary database path."""
    return str(tmp_path / "test_backup.db")


@pytest.fixture
async def test_db(temp_db_path):
    """Create a test database instance."""
    db = BackupDatabase(temp_db_path)
    await db.initialize()
    yield db
    # Cleanup
    if hasattr(db, 'conn') and db.conn:
        await db.conn.close()
    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)


@pytest.fixture
def sample_config_dict() -> Dict[str, Any]:
    """Return a sample configuration dictionary."""
    return {
        "service": {
            "name": "SnapSync Test",
            "log_level": "DEBUG",
            "database_path": "/tmp/snapsync-test/backup.db",
            "web_ui_port": 8080
        },
        "sd_card": {
            "auto_detect": True,
            "mount_points": ["/media/test"],
            "detection_mode": "auto"
        },
        "files": {
            "extensions": [".jpg", ".jpeg", ".png", ".mp4"],
            "min_size": 1024
        },
        "immich": {
            "enabled": True,
            "url": "http://localhost:2283",
            "api_key": "test_api_key_123"
        },
        "unraid": {
            "enabled": True,
            "protocol": "smb",
            "host": "192.168.1.100",
            "share": "backups",
            "path": "/photos",
            "username": "testuser",
            "password": "testpass",
            "mount_point": "",
            "organize_by_date": True
        },
        "mqtt": {
            "enabled": True,
            "broker": "localhost",
            "port": 1883,
            "username": "mqtt_user",
            "password": "mqtt_pass",
            "topic_prefix": "snapsync/test"
        },
        "backup": {
            "parallel": True,
            "concurrent_files": 5,
            "verify_checksums": True,
            "max_retries": 3,
            "retry_delay": 5,
            "require_approval": False,
            "auto_backup_enabled": True
        }
    }


@pytest.fixture
def sample_config(sample_config_dict, tmp_path) -> Config:
    """Create a sample Config object for testing."""
    # Update database_path to use tmp_path
    sample_config_dict["service"]["database_path"] = str(tmp_path / "backup.db")

    return Config(
        service=ServiceConfig(**sample_config_dict["service"]),
        sd_card=SDCardConfig(**sample_config_dict["sd_card"]),
        files=FilesConfig(**sample_config_dict["files"]),
        immich=ImmichConfig(**sample_config_dict["immich"]),
        unraid=UnraidConfig(**sample_config_dict["unraid"]),
        mqtt=MQTTConfig(**sample_config_dict["mqtt"]),
        backup=BackupConfig(**sample_config_dict["backup"])
    )


@pytest.fixture
def sample_yaml_config(tmp_path, sample_config_dict):
    """Create a sample YAML config file."""
    import yaml

    config_file = tmp_path / "config.yaml"
    with open(config_file, 'w') as f:
        yaml.dump(sample_config_dict, f)

    return str(config_file)


@pytest.fixture
def sample_env_file(tmp_path):
    """Create a sample .env file."""
    env_file = tmp_path / ".env"
    env_content = """
# Immich Configuration
IMMICH_URL=http://env-immich:2283
IMMICH_API_KEY=env_api_key_xyz

# Unraid Configuration
UNRAID_HOST=env-unraid.local
UNRAID_USERNAME=env_user
UNRAID_PASSWORD=env_password

# MQTT Configuration
MQTT_BROKER=env-mqtt.local
MQTT_USERNAME=env_mqtt_user
MQTT_PASSWORD=env_mqtt_password
"""
    with open(env_file, 'w') as f:
        f.write(env_content)

    return str(env_file)


@pytest.fixture
def sample_files(tmp_path):
    """Create sample test files."""
    files = []

    # Create some test image files
    for i in range(5):
        file_path = tmp_path / f"test_image_{i}.jpg"
        content = f"fake image content {i}" * 100
        file_path.write_bytes(content.encode())
        files.append(file_path)

    # Create some test video files
    for i in range(3):
        file_path = tmp_path / f"test_video_{i}.mp4"
        content = f"fake video content {i}" * 200
        file_path.write_bytes(content.encode())
        files.append(file_path)

    # Create a file that should be excluded (too small)
    small_file = tmp_path / "small.jpg"
    small_file.write_bytes(b"tiny")

    # Create a file with wrong extension
    wrong_ext = tmp_path / "document.txt"
    wrong_ext.write_text("should be excluded")

    return {
        'files': files,
        'small_file': small_file,
        'wrong_ext': wrong_ext,
        'dir': tmp_path
    }


@pytest.fixture
def mock_immich_client():
    """Create a mock Immich client."""
    client = AsyncMock()
    client.check_connection = AsyncMock(return_value=True)
    client.upload_asset = AsyncMock(return_value={'id': "test-asset-id-123"})
    client.verify_asset = AsyncMock(return_value=True)
    client.get_asset_info = AsyncMock(return_value={
        'id': 'test-asset-id-123',
        'checksum': 'abc123',
        'originalPath': '/test/path.jpg'
    })
    return client


@pytest.fixture
def mock_unraid_client():
    """Create a mock Unraid client."""
    client = AsyncMock()
    client.check_connection = AsyncMock(return_value=True)
    client.upload_file = AsyncMock(return_value="/backups/2025/01/test.jpg")
    client.verify_file = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_mqtt_client():
    """Create a mock MQTT client."""
    client = Mock()
    client.connect = Mock(return_value=0)
    client.publish = Mock()
    client.subscribe = Mock()
    client.disconnect = Mock()
    client.is_connected = Mock(return_value=True)
    return client


@pytest.fixture
def mock_sd_detector():
    """Create a mock SD card detector."""
    detector = Mock()
    detector.start = AsyncMock()
    detector.stop = AsyncMock()
    detector.get_detected_devices = Mock(return_value=[])
    return detector


@pytest.fixture
def sample_file_record():
    """Return a sample file record dictionary."""
    return {
        'id': 1,
        'file_path': '/media/sd/DCIM/IMG_001.jpg',
        'file_name': 'IMG_001.jpg',
        'file_size': 2048000,
        'md5_hash': 'abc123def456',
        'source_device': 'SD_CARD_001',
        'status': 'completed',
        'immich_uploaded': True,
        'unraid_uploaded': True,
        'immich_asset_id': 'asset-123',
        'unraid_path': '/backups/2025/01/IMG_001.jpg',
        'backup_date': '2025-01-15 10:30:00',
        'error_message': None,
        'retry_count': 0
    }


@pytest.fixture
def sample_session_record():
    """Return a sample session record dictionary."""
    return {
        'session_id': 'session-123',
        'device_name': 'SD_CARD_001',
        'device_path': '/media/sd',
        'status': 'completed',
        'total_files': 10,
        'completed_files': 10,
        'failed_files': 0,
        'total_bytes': 20480000,
        'transferred_bytes': 20480000,
        'start_time': '2025-01-15 10:00:00',
        'end_time': '2025-01-15 10:30:00'
    }


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# Helper functions for tests

def create_test_file(path: Path, content: bytes = None, size: int = None) -> Path:
    """
    Create a test file with specific content or size.

    Args:
        path: Path where to create the file
        content: Specific content for the file
        size: Size in bytes (generates random content)

    Returns:
        Path to the created file
    """
    if content is not None:
        path.write_bytes(content)
    elif size is not None:
        path.write_bytes(b'x' * size)
    else:
        path.write_bytes(b'test content')

    return path


def calculate_md5(file_path: Path) -> str:
    """Calculate MD5 hash of a file."""
    import hashlib

    md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            md5.update(chunk)

    return md5.hexdigest()
