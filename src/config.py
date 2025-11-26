"""Configuration management for SnapSync"""
import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ServiceConfig:
    """Service configuration"""
    name: str = "SD Card Backup Service"
    database_path: str = "./backup.db"
    log_level: str = "INFO"
    web_ui_port: int = 8080
    http_log_path: Optional[str] = None

    @staticmethod
    def from_dict(data: dict) -> 'ServiceConfig':
        return ServiceConfig(
            name=data.get('name', "SD Card Backup Service"),
            database_path=data.get('database_path', "./backup.db"),
            log_level=data.get('log_level', "INFO"),
            web_ui_port=data.get('web_ui_port', 8080),
            http_log_path=data.get('http_log_path')
        )


@dataclass
class SDCardConfig:
    """SD Card detection configuration"""
    auto_detect: bool = True
    mount_points: list = field(default_factory=list)
    detection_mode: str = "auto"  # auto, linux, macos, dev


@dataclass
class FilesConfig:
    """File filtering configuration"""
    extensions: list = field(default_factory=lambda: [
        '.jpg', '.jpeg', '.png', '.raw', '.cr2', '.cr3', '.nef',
        '.arw', '.dng', '.orf', '.rw2', '.pef', '.srw',
        '.mp4', '.mov', '.avi', '.mkv', '.mts'
    ])
    min_size: int = 1024


@dataclass
class ImmichConfig:
    """Immich API configuration"""
    enabled: bool = True
    url: str = ""
    api_key: str = ""
    timeout: int = 300
    organize_by_date: bool = True


@dataclass
class UnraidConfig:
    """Unraid/Network storage configuration"""
    enabled: bool = True
    protocol: str = "smb"
    host: str = ""
    share: str = ""
    path: str = ""
    username: str = ""
    password: str = ""
    mount_point: str = ""
    organize_by_date: bool = True


@dataclass
class MQTTConfig:
    """MQTT/Home Assistant configuration"""
    enabled: bool = True
    broker: str = "homeassistant.local"
    port: int = 1883
    username: str = ""
    password: str = ""
    discovery_prefix: str = "homeassistant"
    topic_prefix: str = "snapsync"
    client_id: str = "snapsync"


@dataclass
class BackupConfig:
    """Backup behavior configuration"""
    parallel: bool = True
    concurrent_files: int = 3
    verify_checksums: bool = True
    max_retries: int = 3
    retry_delay: int = 5
    require_approval: bool = False
    auto_backup_enabled: bool = True
    auto_eject: bool = False

    @staticmethod
    def from_dict(data: dict) -> 'BackupConfig':
        return BackupConfig(
            parallel=data.get('parallel', True),
            concurrent_files=data.get('concurrent_files', 3),
            verify_checksums=data.get('verify_checksums', True),
            max_retries=data.get('max_retries', 3),
            retry_delay=data.get('retry_delay', 5),
            require_approval=data.get('require_approval', False),
            auto_backup_enabled=data.get('auto_backup_enabled', True),
            auto_eject=data.get('auto_eject', False)
        )


@dataclass
class Config:
    """Main configuration class"""
    service: ServiceConfig = field(default_factory=ServiceConfig)
    sd_card: SDCardConfig = field(default_factory=SDCardConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    immich: ImmichConfig = field(default_factory=ImmichConfig)
    unraid: UnraidConfig = field(default_factory=UnraidConfig)
    mqtt: MQTTConfig = field(default_factory=MQTTConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)

    @classmethod
    def from_file(cls, config_path: str) -> 'Config':
        """Load configuration from YAML file"""
        config_file = Path(config_path)

        if not config_file.exists():
            logger.warning(f"Config file {config_path} not found, using defaults")
            return cls()

        with open(config_file, 'r') as f:
            data = yaml.safe_load(f) or {}

        # Load environment variables for sensitive data
        cls._load_env_vars(data)

        config = cls()

        # Service config
        if 'service' in data:
            config.service = ServiceConfig(**data['service'])

        # SD Card config
        if 'sd_card' in data:
            config.sd_card = SDCardConfig(**data['sd_card'])

        # Files config
        if 'files' in data:
            config.files = FilesConfig(**data['files'])

        # Immich config
        if 'immich' in data:
            config.immich = ImmichConfig(**data['immich'])

        # Unraid config
        if 'unraid' in data:
            config.unraid = UnraidConfig(**data['unraid'])

        # MQTT config
        if 'mqtt' in data:
            config.mqtt = MQTTConfig(**data['mqtt'])

        # Backup config
        if 'backup' in data:
            config.backup = BackupConfig(**data['backup'])

        logger.info(f"Configuration loaded from {config_path}")
        return config

    @staticmethod
    def _load_env_vars(data: Dict[str, Any]):
        """Load sensitive values from environment variables
        
        Only uses values from .env if they're not placeholder values.
        This allows .env to be optional - if it has placeholder values,
        config.yaml values will be used instead.
        """
        # Placeholder patterns to skip
        placeholders = [
            'your-immich-api-key-here',
            'your-unraid-username',
            'your-unraid-password',
            'your-mqtt-username',
            'your-mqtt-password',
            '',  # Empty values are also placeholders
        ]
        
        def is_placeholder(value: str) -> bool:
            """Check if a value is a placeholder"""
            if not value:
                return True
            return value.strip() in placeholders or value.strip().startswith('your-')
        
        # Check for .env file
        env_file = Path('.env')
        if env_file.exists():
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        key, _, value = line.partition('=')
                        key = key.strip()
                        value = value.strip()
                        # Only set if it's not a placeholder
                        if not is_placeholder(value):
                            os.environ[key] = value

        # Override with environment variables (only if not placeholders)
        if 'immich' in data:
            if api_key := os.getenv('IMMICH_API_KEY'):
                if not is_placeholder(api_key):
                    data['immich']['api_key'] = api_key

        if 'unraid' in data:
            if username := os.getenv('UNRAID_USERNAME'):
                if not is_placeholder(username):
                    data['unraid']['username'] = username
            if password := os.getenv('UNRAID_PASSWORD'):
                if not is_placeholder(password):
                    data['unraid']['password'] = password

        if 'mqtt' in data:
            if username := os.getenv('MQTT_USERNAME'):
                if not is_placeholder(username):
                    data['mqtt']['username'] = username
            if password := os.getenv('MQTT_PASSWORD'):
                if not is_placeholder(password):
                    data['mqtt']['password'] = password

    @staticmethod
    def save_env_vars(updates: Dict[str, str]):
        """Save environment variables to .env file"""
        env_file = Path('.env')
        env_vars = {}

        # Read existing .env file if it exists
        if env_file.exists():
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        key, _, value = line.partition('=')
                        env_vars[key.strip()] = value.strip()

        # Update with new values
        env_vars.update(updates)

        # Write back to .env file
        with open(env_file, 'w') as f:
            for key, value in env_vars.items():
                f.write(f"{key}={value}\n")

        # Update os.environ as well
        os.environ.update(updates)

        logger.info(f"Environment variables updated: {', '.join(updates.keys())}")

    def to_yaml(self, config_path: str):
        """Save configuration to YAML file (excluding sensitive data)"""
        config_data = {
            'service': {
                'name': self.service.name,
                'database_path': self.service.database_path,
                'log_level': self.service.log_level,
                'web_ui_port': self.service.web_ui_port,
            },
            'sd_card': {
                'auto_detect': self.sd_card.auto_detect,
                'mount_points': self.sd_card.mount_points,
                'detection_mode': self.sd_card.detection_mode,
            },
            'files': {
                'extensions': self.files.extensions,
                'min_size': self.files.min_size,
            },
            'immich': {
                'enabled': self.immich.enabled,
                'url': self.immich.url,
                # api_key is stored in .env
                'timeout': self.immich.timeout,
                'organize_by_date': self.immich.organize_by_date,
            },
            'unraid': {
                'enabled': self.unraid.enabled,
                'protocol': self.unraid.protocol,
                'host': self.unraid.host,
                'share': self.unraid.share,
                'path': self.unraid.path,
                # username and password are stored in .env
                'mount_point': self.unraid.mount_point,
                'organize_by_date': self.unraid.organize_by_date,
            },
            'mqtt': {
                'enabled': self.mqtt.enabled,
                'broker': self.mqtt.broker,
                'port': self.mqtt.port,
                # username and password are stored in .env
                'discovery_prefix': self.mqtt.discovery_prefix,
                'topic_prefix': self.mqtt.topic_prefix,
                'client_id': self.mqtt.client_id,
            },
            'backup': {
                'parallel': self.backup.parallel,
                'concurrent_files': self.backup.concurrent_files,
                'verify_checksums': self.backup.verify_checksums,
                'max_retries': self.backup.max_retries,
                'retry_delay': self.backup.retry_delay,
                'require_approval': self.backup.require_approval,
                'auto_backup_enabled': self.backup.auto_backup_enabled,
            }
        }

        with open(config_path, 'w') as f:
            yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Configuration saved to {config_path}")

    def validate(self) -> bool:
        """Validate configuration"""
        errors = []

        if self.immich.enabled and not self.immich.api_key:
            errors.append("Immich API key is required when Immich is enabled")

        if self.immich.enabled and not self.immich.url:
            errors.append("Immich URL is required when Immich is enabled")

        if self.unraid.enabled:
            if self.unraid.protocol == 'smb':
                if not self.unraid.host or not self.unraid.share:
                    errors.append("Unraid host and share are required for SMB protocol")
            elif self.unraid.protocol in ['nfs', 'local']:
                if not self.unraid.mount_point:
                    errors.append("Mount point is required for NFS/local protocol")

        if errors:
            for error in errors:
                logger.error(f"Configuration error: {error}")
            return False

        return True
