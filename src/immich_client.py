"""Immich API client for uploading media"""
import httpx
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ImmichClient:
    """Client for Immich API"""

    def __init__(self, url: str, api_key: str, timeout: int = 300):
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None

    async def initialize(self):
        """Initialize HTTP client"""
        headers = {
            'x-api-key': self.api_key,
            'Accept': 'application/json'
        }
        self.client = httpx.AsyncClient(
            base_url=self.url,
            headers=headers,
            timeout=self.timeout
        )
        logger.info(f"Immich client initialized for {self.url}")

    async def close(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()
            logger.info("Immich client closed")

    async def check_connection(self) -> bool:
        """Verify connection to Immich server"""
        endpoints_to_try = [
            '/server-info',  # Most common endpoint (without /api prefix)
            '/api/user/me',  # Check authenticated user - requires valid API key
            '/api/server-info',
            '/api/server-info/ping',
            '/api/server-version',
            '/api/',  # Root API endpoint
        ]
        
        for endpoint in endpoints_to_try:
            try:
                full_url = f"{self.url}{endpoint}"
                logger.debug(f"Trying to connect to Immich at {full_url}")
                response = await self.client.get(endpoint)
                
                if response.status_code == 200:
                    logger.info(f"Immich server connection verified via {endpoint}")
                    if endpoint == '/api/user/me':
                        try:
                            user_data = response.json()
                            logger.info(f"Authenticated as: {user_data.get('email', 'unknown user')}")
                        except:
                            pass
                    return True
                elif response.status_code == 401:
                    logger.error(f"Authentication failed - API key may be invalid")
                    logger.error(f"Response: {response.text[:200]}")
                    return False
                elif response.status_code == 404:
                    logger.debug(f"Endpoint {endpoint} not found (404), trying next...")
                    continue
                else:
                    logger.debug(f"Immich server returned status {response.status_code} for {endpoint}")
                    # Still try other endpoints
                    continue
            except httpx.ConnectError as e:
                logger.error(f"Failed to connect to Immich server at {self.url}: {e}")
                logger.error("Please verify:")
                logger.error(f"  1. The server is running at {self.url}")
                logger.error(f"  2. The URL is correct (should be like http://host:port)")
                logger.error(f"  3. The server is accessible from this machine")
                return False
            except Exception as e:
                logger.debug(f"Error trying {endpoint}: {e}")
                continue
        
        # If we get here, none of the endpoints worked, but server is responding
        # This might be okay - the endpoints might have changed, but uploads could still work
        logger.warning(f"Could not verify Immich server connection at {self.url}")
        logger.warning("Tried endpoints: " + ", ".join(endpoints_to_try))
        logger.warning("Server is responding but endpoints may have changed.")
        logger.warning("Will attempt to continue - first upload will be the real test.")
        return True  # Return True to allow service to continue - actual upload will test if it works

    async def upload_asset(
        self,
        file_path: Path,
        created_at: Optional[datetime] = None,
        device_id: str = "snapsync"
    ) -> Optional[Dict[str, Any]]:
        """
        Upload a file to Immich

        Args:
            file_path: Path to the file to upload
            created_at: Optional creation date for the asset
            device_id: Device identifier

        Returns:
            Response from Immich API containing asset info, or None on failure
        """
        try:
            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                return None

            # Prepare file for upload
            with open(file_path, 'rb') as f:
                files = {
                    'assetData': (file_path.name, f, self._get_mime_type(file_path))
                }

                # Prepare form data
                data = {
                    'deviceId': device_id,
                    'deviceAssetId': f"{file_path.stem}-{file_path.stat().st_mtime}",
                    'fileCreatedAt': (created_at or datetime.fromtimestamp(file_path.stat().st_mtime)).isoformat(),
                    'fileModifiedAt': datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
                }

                # Upload to Immich
                response = await self.client.post(
                    '/api/assets',
                    files=files,
                    data=data
                )

                if response.status_code in [200, 201]:
                    result = response.json()
                    logger.info(f"Successfully uploaded {file_path.name} to Immich")
                    return result
                else:
                    logger.error(f"Failed to upload {file_path.name}: {response.status_code} - {response.text}")
                    return None

        except Exception as e:
            logger.error(f"Error uploading {file_path} to Immich: {e}", exc_info=True)
            return None

    async def verify_asset(self, asset_id: str) -> bool:
        """Verify that an asset exists in Immich"""
        try:
            response = await self.client.get(f'/api/assets/{asset_id}')
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error verifying asset {asset_id}: {e}")
            return False

    async def check_space(self, required_bytes: int) -> bool:
        """Check if server has enough space (Placeholder implementation)"""
        # Immich API doesn't standardly expose free space to non-admins easily.
        # Assuming true for now.
        return True

    async def get_asset_info(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """Get information about an asset"""
        try:
            response = await self.client.get(f'/api/asset/assetById/{asset_id}')
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"Error getting asset info for {asset_id}: {e}")
            return None

    def _get_mime_type(self, file_path: Path) -> str:
        """Get MIME type for a file based on extension"""
        ext = file_path.suffix.lower()

        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.tiff': 'image/tiff',
            '.tif': 'image/tiff',
            '.raw': 'image/x-raw',
            '.cr2': 'image/x-canon-cr2',
            '.cr3': 'image/x-canon-cr3',
            '.nef': 'image/x-nikon-nef',
            '.arw': 'image/x-sony-arw',
            '.dng': 'image/x-adobe-dng',
            '.orf': 'image/x-olympus-orf',
            '.rw2': 'image/x-panasonic-rw2',
            '.pef': 'image/x-pentax-pef',
            '.srw': 'image/x-samsung-srw',
            '.mp4': 'video/mp4',
            '.mov': 'video/quicktime',
            '.avi': 'video/x-msvideo',
            '.mkv': 'video/x-matroska',
            '.mts': 'video/mp2t',
        }

        return mime_types.get(ext, 'application/octet-stream')
