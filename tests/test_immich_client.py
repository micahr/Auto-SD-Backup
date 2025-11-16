"""
Tests for the Immich client module.

Tests cover:
- Client initialization and connection
- Asset upload functionality
- Asset verification
- Error handling
- MIME type detection
"""
import pytest
import respx
import httpx
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch

from src.immich_client import ImmichClient


@pytest.fixture
async def immich_client():
    """Create an Immich client instance."""
    client = ImmichClient(
        url="http://localhost:2283",
        api_key="test_api_key_123",
        timeout=300
    )
    await client.initialize()
    yield client
    await client.close()


@pytest.mark.unit
@pytest.mark.client
class TestImmichClientInitialization:
    """Test Immich client initialization."""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test client initializes correctly."""
        client = ImmichClient(
            url="http://localhost:2283",
            api_key="test_key",
            timeout=300
        )
        await client.initialize()

        assert client.url == "http://localhost:2283"
        assert client.api_key == "test_key"
        assert client.timeout == 300
        assert client.client is not None

        await client.close()

    @pytest.mark.asyncio
    async def test_url_normalization(self):
        """Test that trailing slash is removed from URL."""
        client = ImmichClient(
            url="http://localhost:2283/",
            api_key="test_key"
        )
        await client.initialize()

        assert client.url == "http://localhost:2283"

        await client.close()

    @pytest.mark.asyncio
    async def test_headers_set(self):
        """Test that API key header is set."""
        client = ImmichClient(
            url="http://localhost:2283",
            api_key="test_key"
        )
        await client.initialize()

        assert client.client.headers['x-api-key'] == "test_key"
        assert client.client.headers['Accept'] == "application/json"

        await client.close()

    @pytest.mark.asyncio
    async def test_close(self):
        """Test client cleanup."""
        client = ImmichClient(
            url="http://localhost:2283",
            api_key="test_key"
        )
        await client.initialize()
        await client.close()

        # Client should be closed
        assert client.client is not None  # Reference remains but connection is closed


@pytest.mark.unit
@pytest.mark.client
@pytest.mark.integration
class TestImmichConnection:
    """Test Immich server connection."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_connection_success(self, immich_client):
        """Test successful connection check."""
        # Mock the ping endpoint
        respx.get("http://localhost:2283/api/server-info/ping").mock(
            return_value=httpx.Response(200, json={"pong": True})
        )

        result = await immich_client.check_connection()

        assert result is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_connection_failure(self, immich_client):
        """Test failed connection check."""
        # Mock failed ping
        respx.get("http://localhost:2283/api/server-info/ping").mock(
            return_value=httpx.Response(500, text="Server error")
        )

        result = await immich_client.check_connection()

        assert result is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_connection_network_error(self, immich_client):
        """Test connection check with network error."""
        # Mock network error
        respx.get("http://localhost:2283/api/server-info/ping").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await immich_client.check_connection()

        assert result is False


@pytest.mark.unit
@pytest.mark.client
@pytest.mark.integration
class TestAssetUpload:
    """Test asset upload functionality."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_upload_asset_success(self, immich_client, tmp_path):
        """Test successful asset upload."""
        # Create test file
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"fake image content")

        # Mock upload endpoint
        respx.post("http://localhost:2283/api/asset/upload").mock(
            return_value=httpx.Response(201, json={
                "id": "asset-123",
                "status": "created"
            })
        )

        result = await immich_client.upload_asset(test_file)

        assert result is not None
        assert result['id'] == "asset-123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_upload_asset_with_created_at(self, immich_client, tmp_path):
        """Test asset upload with custom creation date."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"fake image content")

        created_at = datetime(2025, 1, 15, 10, 30, 0)

        # Mock upload endpoint
        route = respx.post("http://localhost:2283/api/asset/upload").mock(
            return_value=httpx.Response(201, json={"id": "asset-123"})
        )

        result = await immich_client.upload_asset(test_file, created_at=created_at)

        assert result is not None

    @pytest.mark.asyncio
    @respx.mock
    async def test_upload_asset_with_device_id(self, immich_client, tmp_path):
        """Test asset upload with custom device ID."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"fake image content")

        # Mock upload endpoint
        respx.post("http://localhost:2283/api/asset/upload").mock(
            return_value=httpx.Response(201, json={"id": "asset-123"})
        )

        result = await immich_client.upload_asset(
            test_file,
            device_id="CUSTOM_DEVICE_ID"
        )

        assert result is not None

    @pytest.mark.asyncio
    async def test_upload_nonexistent_file(self, immich_client, tmp_path):
        """Test upload of non-existent file."""
        non_existent = tmp_path / "nonexistent.jpg"

        result = await immich_client.upload_asset(non_existent)

        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_upload_asset_server_error(self, immich_client, tmp_path):
        """Test asset upload with server error."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"fake image content")

        # Mock server error
        respx.post("http://localhost:2283/api/asset/upload").mock(
            return_value=httpx.Response(500, text="Internal server error")
        )

        result = await immich_client.upload_asset(test_file)

        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_upload_asset_network_error(self, immich_client, tmp_path):
        """Test asset upload with network error."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"fake image content")

        # Mock network error
        respx.post("http://localhost:2283/api/asset/upload").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await immich_client.upload_asset(test_file)

        assert result is None


@pytest.mark.unit
@pytest.mark.client
@pytest.mark.integration
class TestAssetVerification:
    """Test asset verification functionality."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_verify_asset_success(self, immich_client):
        """Test successful asset verification."""
        asset_id = "asset-123"

        # Mock get asset endpoint
        respx.get(f"http://localhost:2283/api/asset/assetById/{asset_id}").mock(
            return_value=httpx.Response(200, json={
                "id": asset_id,
                "checksum": "abc123"
            })
        )

        result = await immich_client.verify_asset(asset_id)

        assert result is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_verify_asset_not_found(self, immich_client):
        """Test verification of non-existent asset."""
        asset_id = "nonexistent-asset"

        # Mock 404 response
        respx.get(f"http://localhost:2283/api/asset/assetById/{asset_id}").mock(
            return_value=httpx.Response(404, text="Not found")
        )

        result = await immich_client.verify_asset(asset_id)

        assert result is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_verify_asset_network_error(self, immich_client):
        """Test asset verification with network error."""
        asset_id = "asset-123"

        # Mock network error
        respx.get(f"http://localhost:2283/api/asset/assetById/{asset_id}").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await immich_client.verify_asset(asset_id)

        assert result is False


@pytest.mark.unit
@pytest.mark.client
@pytest.mark.integration
class TestAssetInfo:
    """Test getting asset information."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_asset_info_success(self, immich_client):
        """Test getting asset info successfully."""
        asset_id = "asset-123"

        asset_data = {
            "id": asset_id,
            "checksum": "abc123",
            "originalPath": "/path/to/asset.jpg"
        }

        # Mock get asset endpoint
        respx.get(f"http://localhost:2283/api/asset/assetById/{asset_id}").mock(
            return_value=httpx.Response(200, json=asset_data)
        )

        result = await immich_client.get_asset_info(asset_id)

        assert result is not None
        assert result['id'] == asset_id
        assert result['checksum'] == "abc123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_asset_info_not_found(self, immich_client):
        """Test getting info for non-existent asset."""
        asset_id = "nonexistent"

        # Mock 404 response
        respx.get(f"http://localhost:2283/api/asset/assetById/{asset_id}").mock(
            return_value=httpx.Response(404, text="Not found")
        )

        result = await immich_client.get_asset_info(asset_id)

        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_asset_info_network_error(self, immich_client):
        """Test getting asset info with network error."""
        asset_id = "asset-123"

        # Mock network error
        respx.get(f"http://localhost:2283/api/asset/assetById/{asset_id}").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        result = await immich_client.get_asset_info(asset_id)

        assert result is None


@pytest.mark.unit
@pytest.mark.client
class TestMimeTypeDetection:
    """Test MIME type detection."""

    def test_image_mime_types(self, tmp_path):
        """Test MIME type detection for image files."""
        client = ImmichClient(url="http://localhost", api_key="key")

        image_files = {
            'test.jpg': 'image/jpeg',
            'test.jpeg': 'image/jpeg',
            'test.png': 'image/png',
            'test.gif': 'image/gif',
            'test.bmp': 'image/bmp',
            'test.tiff': 'image/tiff',
        }

        for filename, expected_mime in image_files.items():
            file_path = tmp_path / filename
            assert client._get_mime_type(file_path) == expected_mime

    def test_raw_image_mime_types(self, tmp_path):
        """Test MIME type detection for RAW image files."""
        client = ImmichClient(url="http://localhost", api_key="key")

        raw_files = {
            'test.cr2': 'image/x-canon-cr2',
            'test.cr3': 'image/x-canon-cr3',
            'test.nef': 'image/x-nikon-nef',
            'test.arw': 'image/x-sony-arw',
            'test.dng': 'image/x-adobe-dng',
        }

        for filename, expected_mime in raw_files.items():
            file_path = tmp_path / filename
            assert client._get_mime_type(file_path) == expected_mime

    def test_video_mime_types(self, tmp_path):
        """Test MIME type detection for video files."""
        client = ImmichClient(url="http://localhost", api_key="key")

        video_files = {
            'test.mp4': 'video/mp4',
            'test.mov': 'video/quicktime',
            'test.avi': 'video/x-msvideo',
            'test.mkv': 'video/x-matroska',
            'test.mts': 'video/mp2t',
        }

        for filename, expected_mime in video_files.items():
            file_path = tmp_path / filename
            assert client._get_mime_type(file_path) == expected_mime

    def test_case_insensitive_mime_type(self, tmp_path):
        """Test that MIME type detection is case insensitive."""
        client = ImmichClient(url="http://localhost", api_key="key")

        assert client._get_mime_type(tmp_path / "test.JPG") == 'image/jpeg'
        assert client._get_mime_type(tmp_path / "test.Mp4") == 'video/mp4'
        assert client._get_mime_type(tmp_path / "test.PNG") == 'image/png'

    def test_unknown_extension(self, tmp_path):
        """Test MIME type for unknown extension."""
        client = ImmichClient(url="http://localhost", api_key="key")

        unknown_file = tmp_path / "test.xyz"
        assert client._get_mime_type(unknown_file) == 'application/octet-stream'
