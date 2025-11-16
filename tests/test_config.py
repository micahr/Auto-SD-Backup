"""
Tests for the configuration module.

Tests cover:
- YAML configuration loading
- Environment variable overrides
- Configuration validation
- Default values
- Configuration saving
- Sensitive data handling
"""
import os
import pytest
import yaml
from pathlib import Path

from src.config import (
    Config, ServiceConfig, SDCardConfig, FilesConfig,
    ImmichConfig, UnraidConfig, MQTTConfig, BackupConfig
)


@pytest.mark.unit
@pytest.mark.config
class TestConfigDefaults:
    """Test default configuration values."""

    def test_default_service_config(self):
        """Test default ServiceConfig values."""
        config = ServiceConfig()
        assert config.name == "SnapSync"
        assert config.database_path == "./snapsync.db"
        assert config.log_level == "INFO"
        assert config.web_ui_port == 8080

    def test_default_sd_card_config(self):
        """Test default SDCardConfig values."""
        config = SDCardConfig()
        assert config.auto_detect is True
        assert config.mount_points == []
        assert config.detection_mode == "auto"

    def test_default_files_config(self):
        """Test default FilesConfig values."""
        config = FilesConfig()
        assert len(config.extensions) > 0
        assert '.jpg' in config.extensions
        assert '.mp4' in config.extensions
        assert config.min_size == 1024

    def test_default_immich_config(self):
        """Test default ImmichConfig values."""
        config = ImmichConfig()
        assert config.enabled is True
        assert config.url == ""
        assert config.api_key == ""
        assert config.timeout == 300
        assert config.organize_by_date is True

    def test_default_unraid_config(self):
        """Test default UnraidConfig values."""
        config = UnraidConfig()
        assert config.enabled is True
        assert config.protocol == "smb"
        assert config.organize_by_date is True

    def test_default_mqtt_config(self):
        """Test default MQTTConfig values."""
        config = MQTTConfig()
        assert config.enabled is True
        assert config.broker == "homeassistant.local"
        assert config.port == 1883
        assert config.discovery_prefix == "homeassistant"
        assert config.topic_prefix == "snapsync"

    def test_default_backup_config(self):
        """Test default BackupConfig values."""
        config = BackupConfig()
        assert config.parallel is True
        assert config.concurrent_files == 3
        assert config.verify_checksums is True
        assert config.max_retries == 3
        assert config.retry_delay == 5
        assert config.require_approval is False
        assert config.auto_backup_enabled is True

    def test_default_main_config(self):
        """Test default main Config."""
        config = Config()
        assert isinstance(config.service, ServiceConfig)
        assert isinstance(config.sd_card, SDCardConfig)
        assert isinstance(config.files, FilesConfig)
        assert isinstance(config.immich, ImmichConfig)
        assert isinstance(config.unraid, UnraidConfig)
        assert isinstance(config.mqtt, MQTTConfig)
        assert isinstance(config.backup, BackupConfig)


@pytest.mark.unit
@pytest.mark.config
class TestConfigLoading:
    """Test configuration loading from YAML files."""

    def test_load_from_nonexistent_file(self, tmp_path):
        """Test loading from non-existent file returns defaults."""
        config_path = tmp_path / "nonexistent.yaml"
        config = Config.from_file(str(config_path))

        # Should return default config
        assert isinstance(config, Config)
        assert config.service.name == "SnapSync"

    def test_load_from_yaml_file(self, sample_yaml_config):
        """Test loading configuration from YAML file."""
        config = Config.from_file(sample_yaml_config)

        assert config.service.name == "SnapSync Test"
        assert config.service.log_level == "DEBUG"
        assert config.immich.url == "http://localhost:2283"
        assert config.unraid.host == "192.168.1.100"
        assert config.mqtt.broker == "localhost"

    def test_load_partial_config(self, tmp_path):
        """Test loading partial configuration (some sections missing)."""
        partial_config = {
            "service": {
                "name": "PartialConfig",
                "log_level": "WARNING"
            },
            "immich": {
                "enabled": False,
                "url": "http://immich.local"
            }
        }

        config_file = tmp_path / "partial.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(partial_config, f)

        config = Config.from_file(str(config_file))

        # Specified values should be loaded
        assert config.service.name == "PartialConfig"
        assert config.service.log_level == "WARNING"
        assert config.immich.enabled is False

        # Missing sections should have defaults
        assert config.backup.parallel is True  # default value
        assert config.mqtt.broker == "homeassistant.local"  # default value

    def test_load_empty_yaml(self, tmp_path):
        """Test loading empty YAML file."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        config = Config.from_file(str(config_file))

        # Should return default config
        assert isinstance(config, Config)
        assert config.service.name == "SnapSync"


@pytest.mark.unit
@pytest.mark.config
class TestEnvironmentVariables:
    """Test environment variable loading and overrides."""

    def test_load_env_from_file(self, tmp_path, monkeypatch):
        """Test loading environment variables from .env file."""
        # Create .env file
        env_file = tmp_path / ".env"
        env_content = """
IMMICH_API_KEY=test_key_from_env
UNRAID_USERNAME=env_user
UNRAID_PASSWORD=env_pass
MQTT_USERNAME=mqtt_user
MQTT_PASSWORD=mqtt_pass
"""
        env_file.write_text(env_content)

        # Change working directory to tmp_path
        monkeypatch.chdir(tmp_path)

        # Create config YAML
        config_data = {
            "immich": {"enabled": True, "url": "http://localhost"},
            "unraid": {"enabled": True, "host": "unraid.local", "share": "backups"},
            "mqtt": {"enabled": True, "broker": "mqtt.local"}
        }

        config_file = tmp_path / "config.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        # Load config (should read .env file)
        config = Config.from_file(str(config_file))

        # Environment variables should override
        assert config.immich.api_key == "test_key_from_env"
        assert config.unraid.username == "env_user"
        assert config.unraid.password == "env_pass"
        assert config.mqtt.username == "mqtt_user"
        assert config.mqtt.password == "mqtt_pass"

    def test_env_vars_override_yaml(self, tmp_path, monkeypatch):
        """Test that environment variables override YAML values."""
        # Set environment variables
        monkeypatch.setenv("IMMICH_API_KEY", "env_key_123")
        monkeypatch.setenv("UNRAID_USERNAME", "env_unraid_user")

        # Create config with different values
        config_data = {
            "immich": {
                "enabled": True,
                "url": "http://localhost",
                "api_key": "yaml_key_456"  # Should be overridden
            },
            "unraid": {
                "enabled": True,
                "host": "unraid.local",
                "share": "backups",
                "username": "yaml_user"  # Should be overridden
            }
        }

        config_file = tmp_path / "config.yaml"
        with open(config_file, 'w') as f:
            yaml.dump(config_data, f)

        config = Config.from_file(str(config_file))

        # Environment variables should take precedence
        assert config.immich.api_key == "env_key_123"
        assert config.unraid.username == "env_unraid_user"

    def test_save_env_vars(self, tmp_path, monkeypatch):
        """Test saving environment variables to .env file."""
        monkeypatch.chdir(tmp_path)

        updates = {
            "IMMICH_API_KEY": "new_key_789",
            "UNRAID_PASSWORD": "new_password"
        }

        Config.save_env_vars(updates)

        # Check .env file was created
        env_file = tmp_path / ".env"
        assert env_file.exists()

        # Verify content
        content = env_file.read_text()
        assert "IMMICH_API_KEY=new_key_789" in content
        assert "UNRAID_PASSWORD=new_password" in content

        # Check os.environ was updated
        assert os.environ.get("IMMICH_API_KEY") == "new_key_789"
        assert os.environ.get("UNRAID_PASSWORD") == "new_password"

    def test_save_env_vars_preserves_existing(self, tmp_path, monkeypatch):
        """Test that saving env vars preserves existing values."""
        monkeypatch.chdir(tmp_path)

        # Create existing .env file
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_VAR=existing_value\nANOTHER_VAR=another")

        # Save new vars
        updates = {"NEW_VAR": "new_value"}
        Config.save_env_vars(updates)

        # Read file
        content = env_file.read_text()

        # Both old and new should be present
        assert "EXISTING_VAR=existing_value" in content
        assert "ANOTHER_VAR=another" in content
        assert "NEW_VAR=new_value" in content


@pytest.mark.unit
@pytest.mark.config
class TestConfigValidation:
    """Test configuration validation."""

    def test_valid_config(self, sample_config):
        """Test validation of valid configuration."""
        assert sample_config.validate() is True

    def test_immich_enabled_without_api_key(self):
        """Test validation fails when Immich is enabled without API key."""
        config = Config()
        config.immich.enabled = True
        config.immich.url = "http://localhost"
        config.immich.api_key = ""  # Missing API key

        assert config.validate() is False

    def test_immich_enabled_without_url(self):
        """Test validation fails when Immich is enabled without URL."""
        config = Config()
        config.immich.enabled = True
        config.immich.api_key = "test_key"
        config.immich.url = ""  # Missing URL

        assert config.validate() is False

    def test_immich_disabled_validation_passes(self):
        """Test validation passes when Immich is disabled."""
        config = Config()
        config.immich.enabled = False
        config.immich.api_key = ""  # Empty is OK when disabled
        config.immich.url = ""

        # Should still fail because unraid is enabled by default and has validation
        config.unraid.enabled = False
        assert config.validate() is True

    def test_unraid_smb_without_host(self):
        """Test validation fails for SMB without host."""
        config = Config()
        config.immich.enabled = False
        config.unraid.enabled = True
        config.unraid.protocol = "smb"
        config.unraid.host = ""  # Missing host
        config.unraid.share = "backups"

        assert config.validate() is False

    def test_unraid_smb_without_share(self):
        """Test validation fails for SMB without share."""
        config = Config()
        config.immich.enabled = False
        config.unraid.enabled = True
        config.unraid.protocol = "smb"
        config.unraid.host = "unraid.local"
        config.unraid.share = ""  # Missing share

        assert config.validate() is False

    def test_unraid_nfs_without_mount_point(self):
        """Test validation fails for NFS without mount point."""
        config = Config()
        config.immich.enabled = False
        config.unraid.enabled = True
        config.unraid.protocol = "nfs"
        config.unraid.mount_point = ""  # Missing mount point

        assert config.validate() is False

    def test_unraid_local_without_mount_point(self):
        """Test validation fails for local without mount point."""
        config = Config()
        config.immich.enabled = False
        config.unraid.enabled = True
        config.unraid.protocol = "local"
        config.unraid.mount_point = ""  # Missing mount point

        assert config.validate() is False

    def test_both_services_disabled_validation_passes(self):
        """Test validation passes when both services are disabled."""
        config = Config()
        config.immich.enabled = False
        config.unraid.enabled = False

        assert config.validate() is True


@pytest.mark.unit
@pytest.mark.config
class TestConfigSaving:
    """Test saving configuration to YAML."""

    def test_save_to_yaml(self, sample_config, tmp_path):
        """Test saving configuration to YAML file."""
        config_path = tmp_path / "saved_config.yaml"
        sample_config.to_yaml(str(config_path))

        assert config_path.exists()

        # Load and verify
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)

        assert data['service']['name'] == sample_config.service.name
        assert data['immich']['url'] == sample_config.immich.url
        assert data['backup']['parallel'] == sample_config.backup.parallel

    def test_saved_yaml_excludes_sensitive_data(self, sample_config, tmp_path):
        """Test that saved YAML doesn't include sensitive credentials."""
        config_path = tmp_path / "saved_config.yaml"
        sample_config.to_yaml(str(config_path))

        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)

        # Sensitive fields should not be in YAML
        assert 'api_key' not in data.get('immich', {})
        assert 'password' not in data.get('unraid', {})
        assert 'username' not in data.get('unraid', {})
        assert 'password' not in data.get('mqtt', {})
        assert 'username' not in data.get('mqtt', {})

    def test_roundtrip_config(self, sample_config, tmp_path, monkeypatch):
        """Test saving and loading configuration (roundtrip)."""
        monkeypatch.chdir(tmp_path)

        # Save config to YAML
        config_path = tmp_path / "roundtrip.yaml"
        sample_config.to_yaml(str(config_path))

        # Save sensitive data to .env
        env_vars = {
            "IMMICH_API_KEY": sample_config.immich.api_key,
            "UNRAID_USERNAME": sample_config.unraid.username,
            "UNRAID_PASSWORD": sample_config.unraid.password,
            "MQTT_USERNAME": sample_config.mqtt.username,
            "MQTT_PASSWORD": sample_config.mqtt.password
        }
        Config.save_env_vars(env_vars)

        # Load config back
        loaded_config = Config.from_file(str(config_path))

        # Verify non-sensitive data matches
        assert loaded_config.service.name == sample_config.service.name
        assert loaded_config.immich.url == sample_config.immich.url
        assert loaded_config.unraid.host == sample_config.unraid.host

        # Verify sensitive data was loaded from .env
        assert loaded_config.immich.api_key == sample_config.immich.api_key
        assert loaded_config.unraid.username == sample_config.unraid.username


@pytest.mark.unit
@pytest.mark.config
class TestConfigDataclasses:
    """Test dataclass functionality."""

    def test_service_config_instantiation(self):
        """Test ServiceConfig can be instantiated with custom values."""
        config = ServiceConfig(
            name="CustomName",
            database_path="/custom/path.db",
            log_level="DEBUG",
            web_ui_port=9090
        )

        assert config.name == "CustomName"
        assert config.database_path == "/custom/path.db"
        assert config.log_level == "DEBUG"
        assert config.web_ui_port == 9090

    def test_immich_config_instantiation(self):
        """Test ImmichConfig can be instantiated with custom values."""
        config = ImmichConfig(
            enabled=True,
            url="http://custom-immich:2283",
            api_key="custom_key_123",
            timeout=600
        )

        assert config.enabled is True
        assert config.url == "http://custom-immich:2283"
        assert config.api_key == "custom_key_123"
        assert config.timeout == 600

    def test_backup_config_instantiation(self):
        """Test BackupConfig can be instantiated with custom values."""
        config = BackupConfig(
            parallel=False,
            concurrent_files=10,
            verify_checksums=False,
            max_retries=5,
            retry_delay=10,
            require_approval=True,
            auto_backup_enabled=False
        )

        assert config.parallel is False
        assert config.concurrent_files == 10
        assert config.verify_checksums is False
        assert config.max_retries == 5
        assert config.retry_delay == 10
        assert config.require_approval is True
        assert config.auto_backup_enabled is False
