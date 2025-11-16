# SnapSync

**Automatic SD Card Backup Service for Photographers**

SnapSync is a Python-based service that automatically detects SD cards when inserted, backs up your photos and videos to both Immich and Unraid (or any SMB/NFS share), and integrates seamlessly with Home Assistant for status monitoring.

## Features

- **Automatic SD Card Detection**: Monitors for SD card insertion and starts backup immediately
- **Parallel Uploads**: Uploads to Immich and Unraid simultaneously for faster backups
- **Smart Deduplication**: Tracks files via MD5 hash to avoid backing up the same file twice
- **Checksum Verification**: Verifies file integrity after upload with MD5 checksums
- **Date-Based Organization**: Organizes files by date (YYYY/MM/DD) on both destinations
- **Home Assistant Integration**: MQTT integration with auto-discovery for status monitoring
- **Web Dashboard**: Beautiful web UI for monitoring backups and viewing history
- **CLI Tools**: Command-line interface for status checks and manual operations
- **Configurable File Filters**: Support for common camera file formats (JPG, RAW, videos)
- **Resilient**: Automatic retry on failures, resume interrupted backups
- **Progress Tracking**: Real-time progress updates via MQTT and web UI
- **Session History**: SQLite database tracks all backup sessions and file status

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Home Assistant Integration](#home-assistant-integration)
- [Web UI](#web-ui)
- [CLI Commands](#cli-commands)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

## Requirements

### Hardware
- Raspberry Pi (or any Linux system)
- SD card reader

### Software
- Python 3.9 or higher
- Linux with systemd
- Access to:
  - Immich server with API key
  - Unraid server or SMB/NFS share
  - MQTT broker (for Home Assistant integration)

### Python Dependencies
All dependencies are listed in `requirements.txt` and installed automatically:
- FastAPI & Uvicorn (Web UI)
- paho-mqtt (MQTT/Home Assistant)
- pyudev (SD card detection)
- httpx (Immich API)
- smbprotocol (Unraid/SMB)
- click (CLI)
- aiosqlite (Database)
- pyyaml (Configuration)

## Installation

### Quick Install (Raspberry Pi)

1. Clone the repository:
```bash
git clone https://github.com/yourusername/snapsync.git
cd snapsync
```

2. Run the installation script:
```bash
sudo ./install.sh
```

The installer will:
- Install system dependencies
- Create a Python virtual environment
- Install Python packages
- Set up the systemd service
- Create configuration templates
- Install CLI tools

### Manual Installation

1. Install system dependencies:
```bash
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv libudev-dev
```

2. Create virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install Python dependencies:
```bash
pip install -r requirements.txt
```

4. Copy configuration templates:
```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

## Configuration

### Main Configuration File (`config.yaml`)

Edit `config.yaml` with your settings:

```yaml
service:
  name: "SnapSync"
  database_path: "./snapsync.db"
  log_level: "INFO"
  web_ui_port: 8080

sd_card:
  auto_detect: true
  mount_points: []  # Optional: specific mount points to monitor

files:
  extensions:
    - .jpg
    - .jpeg
    - .png
    - .raw
    - .cr2
    - .cr3
    - .nef
    - .arw
    - .dng
    - .mp4
    - .mov
    # Add more as needed
  min_size: 1024  # Minimum file size in bytes

immich:
  enabled: true
  url: "http://your-immich-server:2283"
  api_key: "your-api-key-here"
  timeout: 300
  organize_by_date: true

unraid:
  enabled: true
  protocol: "smb"  # smb, nfs, or local
  host: "your-unraid-server"
  share: "backups"
  path: "camera-backups"
  username: "your-username"
  password: "your-password"
  organize_by_date: true

mqtt:
  enabled: true
  broker: "homeassistant.local"
  port: 1883
  username: ""
  password: ""
  discovery_prefix: "homeassistant"
  topic_prefix: "snapsync"
  client_id: "snapsync"

backup:
  parallel: true  # Upload to Immich and Unraid simultaneously
  concurrent_files: 3  # Number of files to process at once
  verify_checksums: true  # Verify file integrity after upload
  max_retries: 3
  retry_delay: 5  # Seconds
```

### Environment Variables (`.env`)

Store sensitive credentials in `.env`:

```bash
IMMICH_API_KEY=your-immich-api-key-here
UNRAID_USERNAME=your-unraid-username
UNRAID_PASSWORD=your-unraid-password
MQTT_USERNAME=your-mqtt-username
MQTT_PASSWORD=your-mqtt-password
```

### Immich API Key

To get your Immich API key:
1. Log into your Immich web interface
2. Go to Account Settings
3. Navigate to API Keys
4. Create a new API key
5. Copy the key and add it to your `.env` file

### Unraid/SMB Configuration

For Unraid or any SMB share:
- **host**: IP address or hostname of your server
- **share**: Name of the SMB share
- **path**: Path within the share for backups
- **username/password**: SMB credentials (stored in `.env`)

For NFS or local mounts:
- Set `protocol: "nfs"` or `protocol: "local"`
- Set `mount_point` to the mounted path

## Usage

### Starting the Service

#### As a systemd service (recommended):
```bash
# Enable auto-start on boot
sudo systemctl enable snapsync

# Start the service
sudo systemctl start snapsync

# Check status
sudo systemctl status snapsync

# View logs
sudo journalctl -u snapsync -f
```

#### Manual start (foreground):
```bash
snapsync start
```

### Testing Your Setup

Before starting the service, test your connections:

```bash
snapsync test-connection
```

This will verify:
- Immich API connection
- Unraid/SMB share access
- Configuration validity

### Normal Operation

1. Start the service (either as systemd or manually)
2. Insert an SD card into your card reader
3. SnapSync will automatically:
   - Detect the SD card
   - Scan for new files
   - Calculate MD5 hashes
   - Upload to Immich and Unraid in parallel
   - Verify checksums
   - Update Home Assistant with progress
4. Remove the SD card when backup is complete

## Home Assistant Integration

SnapSync automatically registers with Home Assistant via MQTT auto-discovery.

### Sensors Created

After starting SnapSync with MQTT enabled, you'll see these entities in Home Assistant:

- **sensor.snapsync_status**: Current status (idle, backing_up, completed, failed)
- **sensor.snapsync_progress**: Backup progress percentage with attributes
- **sensor.snapsync_files**: Files completed/total with attributes
- **sensor.snapsync_current_file**: Currently backing up file

### Example Home Assistant Automation

```yaml
automation:
  - alias: "SnapSync Backup Complete Notification"
    trigger:
      - platform: state
        entity_id: sensor.snapsync_status
        to: "completed"
    action:
      - service: notify.mobile_app
        data:
          title: "Backup Complete"
          message: "SD card backup finished: {{ state_attr('sensor.snapsync_files', 'completed') }} files backed up"
```

### Example Lovelace Card

```yaml
type: entities
title: SnapSync
entities:
  - entity: sensor.snapsync_status
    name: Status
  - entity: sensor.snapsync_progress
    name: Progress
  - entity: sensor.snapsync_files
    name: Files
  - entity: sensor.snapsync_current_file
    name: Current File
```

## Web UI

Access the web dashboard at: `http://your-raspberry-pi-ip:8080`

The dashboard shows:
- Current backup status
- Real-time progress
- Overall statistics
- Recent backup sessions
- Failed files (if any)

The web UI auto-refreshes every 5 seconds during backups.

## CLI Commands

SnapSync provides several CLI commands:

### Status
```bash
snapsync status
```
Shows current service status, active backups, and statistics.

### Recent Sessions
```bash
snapsync sessions
```
Lists the 10 most recent backup sessions with details.

### Test Connections
```bash
snapsync test-connection
```
Tests connectivity to Immich and Unraid.

### Configuration
```bash
# View current configuration
snapsync config

# Generate config template
snapsync config --template
```

### Web UI Only
```bash
snapsync web
```
Starts only the web UI without the backup service (useful for testing).

### Help
```bash
snapsync --help
```

## Architecture

### Components

```
┌─────────────────────────────────────────────────────┐
│                  SnapSync Service                    │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────┐      ┌──────────────┐            │
│  │ SD Detector  │─────▶│Backup Engine │            │
│  │  (pyudev)    │      │              │            │
│  └──────────────┘      └───────┬──────┘            │
│                                │                    │
│                        ┌───────┴────────┐           │
│                        │                │           │
│                   ┌────▼─────┐   ┌─────▼────┐      │
│                   │  Immich  │   │ Unraid   │      │
│                   │  Client  │   │  Client  │      │
│                   └──────────┘   └──────────┘      │
│                                                      │
│  ┌──────────────┐      ┌──────────────┐            │
│  │  MQTT Client │      │   Web UI     │            │
│  │(Home Assist.)│      │  (FastAPI)   │            │
│  └──────────────┘      └──────────────┘            │
│                                                      │
│  ┌──────────────────────────────────────┐           │
│  │      SQLite Database                 │           │
│  │  (Files, Sessions, Status)           │           │
│  └──────────────────────────────────────┘           │
└─────────────────────────────────────────────────────┘
```

### Backup Flow

1. **Detection**: pyudev monitors for block device events
2. **Scanning**: When SD card detected, scan for files matching configured extensions
3. **Hashing**: Calculate MD5 hash for each file
4. **Deduplication**: Check database if file already backed up
5. **Upload**: Parallel upload to Immich and Unraid
6. **Verification**: Verify MD5 checksums after upload
7. **Database**: Update file status in SQLite database
8. **MQTT**: Publish progress to Home Assistant
9. **Completion**: Mark session as complete

### File States

Files in the database progress through these states:
- `new`: File discovered, not yet processed
- `scanning`: Currently calculating hash
- `backing_up`: Upload in progress
- `verifying`: Verifying checksums
- `completed`: Successfully backed up
- `failed`: Backup failed (will retry)

### Database Schema

**files table**:
- Tracks individual files with hash, status, destinations
- Prevents duplicate backups via unique hash+device constraint

**backup_sessions table**:
- Tracks each SD card insertion as a session
- Records totals, progress, completion status

## Troubleshooting

### SD Card Not Detected

1. Check if card is mounted:
```bash
lsblk
mount | grep media
```

2. Check udev permissions:
```bash
ls -la /dev/sd*
groups $USER  # Ensure user is in 'plugdev' or similar
```

3. Check service logs:
```bash
sudo journalctl -u snapsync -n 50
```

### Upload Failures

1. Test Immich connection:
```bash
snapsync test-connection
```

2. Check Immich API key is valid
3. Verify network connectivity
4. Check Immich server logs

### Unraid/SMB Connection Issues

1. Test SMB connection manually:
```bash
smbclient //your-server/share -U username
```

2. Verify credentials in `.env`
3. Check firewall rules
4. Ensure SMB share has write permissions

### MQTT/Home Assistant Not Working

1. Verify MQTT broker is running
2. Check MQTT credentials
3. Test MQTT connection:
```bash
mosquitto_sub -h homeassistant.local -t "snapsync/#" -v
```

4. Check Home Assistant MQTT integration is enabled

### Service Won't Start

1. Check Python version:
```bash
python3 --version  # Should be 3.9+
```

2. Check dependencies:
```bash
cd /path/to/snapsync
./venv/bin/pip list
```

3. Validate configuration:
```bash
snapsync config
```

4. Check systemd logs:
```bash
sudo journalctl -u snapsync -xe
```

### Database Issues

If the database becomes corrupted:
```bash
# Backup current database
cp snapsync.db snapsync.db.backup

# Delete and restart service (will recreate)
rm snapsync.db
sudo systemctl restart snapsync
```

### Performance Issues

If backups are slow:

1. Reduce concurrent files in config:
```yaml
backup:
  concurrent_files: 2  # Lower number
```

2. Disable parallel uploads:
```yaml
backup:
  parallel: false  # Upload sequentially
```

3. Check network speed to Immich/Unraid
4. Consider disabling checksum verification:
```yaml
backup:
  verify_checksums: false  # Not recommended
```

## Development

### Project Structure

```
snapsync/
├── src/
│   ├── __init__.py
│   ├── backup_engine.py    # Core backup logic
│   ├── cli.py              # CLI commands
│   ├── config.py           # Configuration management
│   ├── database.py         # SQLite database operations
│   ├── immich_client.py    # Immich API client
│   ├── mqtt_client.py      # MQTT/Home Assistant
│   ├── sd_detector.py      # SD card detection
│   ├── service.py          # Main service orchestrator
│   ├── unraid_client.py    # Unraid/SMB client
│   ├── web_ui.py           # FastAPI web server
│   └── templates/
│       └── dashboard.html  # Web UI template
├── snapsync.py             # Entry point
├── setup.py                # Package setup
├── requirements.txt        # Python dependencies
├── config.yaml.example     # Config template
├── .env.example            # Environment template
├── snapsync.service        # Systemd service
├── install.sh              # Installation script
└── README.md               # This file
```

### Running Tests

```bash
# Activate virtual environment
source venv/bin/activate

# Run in development mode
python snapsync.py start
```

### Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Roadmap

Future enhancements:
- [ ] Multiple SD card readers support
- [ ] Email notifications
- [ ] Backup to additional cloud services (Google Drive, S3)
- [ ] Automatic SD card unmount after backup
- [ ] Custom filename patterns
- [ ] Backup verification reports
- [ ] Mobile app
- [ ] Docker container support
- [ ] Windows/macOS support

## License

MIT License - see LICENSE file for details

## Credits

Created for photographers who want automatic, reliable backups of their work.

Built with:
- Python
- FastAPI
- Immich
- Home Assistant
- Raspberry Pi

## Support

For issues, questions, or contributions:
- GitHub Issues: [your-repo-url/issues]
- Documentation: [your-docs-url]

---

**SnapSync** - Never lose a photo again! 📸
