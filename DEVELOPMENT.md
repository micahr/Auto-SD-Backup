# SnapSync Development Guide

This guide covers how to set up and develop SnapSync on macOS (or other non-Linux platforms).

## macOS Setup

### Prerequisites

1. **Python 3.9+**
   ```bash
   # Check your Python version
   python3 --version

   # Install via Homebrew if needed
   brew install python@3.11
   ```

2. **Git**
   ```bash
   brew install git
   ```

### Installation for Development

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/snapsync.git
   cd snapsync
   ```

2. **Create virtual environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt

   # Note: pyudev won't install on macOS (Linux-only), this is expected
   # The service will automatically use macOS-compatible detection
   ```

4. **Create configuration**:
   ```bash
   cp config.yaml.example config.yaml
   cp .env.example .env
   ```

5. **Edit configuration** (`config.yaml`):
   ```yaml
   # For development, you can disable services you don't have
   immich:
     enabled: false  # Set to true if you have Immich running

   unraid:
     enabled: false  # Set to true if you have an SMB share

   mqtt:
     enabled: false  # Set to true if you have MQTT broker

   # Optional: Use dev mode for manual triggering
   sd_card:
     detection_mode: dev  # or "macos" to monitor /Volumes
   ```

## Development Modes

SnapSync supports three detection modes for development:

### 1. Auto Mode (Recommended)
```yaml
sd_card:
  detection_mode: auto
```
Automatically selects the appropriate detector:
- Linux → pyudev (USB detection)
- macOS → /Volumes monitoring
- Other → dev mode

### 2. macOS Mode
```yaml
sd_card:
  detection_mode: macos
```
Monitors `/Volumes` directory and uses `diskutil` for removable media detection:
- **Works with built-in SD card readers** (MacBook Pro/Air built-in slots)
- Works with external USB card readers
- Works with USB drives, external drives
- Uses `diskutil list external` to identify removable media
- Polls every 1 second for fast detection
- Excludes system volumes (Macintosh HD, etc.)
- Shows device type (SD Card Reader, USB, etc.) in logs

### 3. Dev Mode (Manual Triggering)
```yaml
sd_card:
  detection_mode: dev
```
No automatic detection, use CLI to trigger backups manually:
```bash
python snapsync.py backup /path/to/test/directory
```

## Testing Without Immich/Unraid

You can test the backup engine locally without Immich or Unraid:

### Option 1: Local Testing Only

Edit `config.yaml`:
```yaml
immich:
  enabled: false

unraid:
  enabled: true
  protocol: local  # Use local filesystem instead of SMB
  path: "/Users/yourname/snapsync-backups"  # Local backup directory
  organize_by_date: true
```

This will copy files to a local directory with date-based organization.

### Option 2: Mock Services

Create test directories to simulate backups:
```bash
mkdir -p ~/snapsync-test/source
mkdir -p ~/snapsync-test/backups

# Copy some test images
cp ~/Pictures/*.jpg ~/snapsync-test/source/

# Run backup
python snapsync.py backup ~/snapsync-test/source
```

## Running the Service

### Start Service (with auto-detection)
```bash
python snapsync.py start
```

On macOS, this will monitor `/Volumes` for new drives.

### Manual Backup (dev mode)
```bash
# Backup a specific directory
python snapsync.py backup ~/Pictures/test-photos

# Or use an absolute path
python snapsync.py backup /Users/yourname/test-sd-card
```

### Web UI Only
```bash
python snapsync.py web
```
Access at `http://localhost:8080`

### Check Status
```bash
python snapsync.py status
```

### View Sessions
```bash
python snapsync.py sessions
```

## Testing Workflow

### 1. Prepare Test Data
```bash
mkdir -p ~/test-sd-card
cp ~/Pictures/*.{jpg,png,raw} ~/test-sd-card/
```

### 2. Configure for Local Backup
Edit `config.yaml`:
```yaml
immich:
  enabled: false

unraid:
  enabled: true
  protocol: local
  path: "~/snapsync-backups"
  organize_by_date: true

mqtt:
  enabled: false

sd_card:
  detection_mode: dev
```

### 3. Run Backup
```bash
python snapsync.py backup ~/test-sd-card
```

### 4. Verify Results
```bash
# Check backup directory
ls -R ~/snapsync-backups

# Check database
python snapsync.py sessions

# View in web UI
python snapsync.py web
# Open http://localhost:8080
```

## macOS Volume Monitoring

When using `detection_mode: macos`, SnapSync will:

1. Use `diskutil list external` to detect removable media
2. Monitor `/Volumes` every 1 second (fast detection)
3. Detect new volumes (excluding system volumes)
4. Automatically trigger backup when new volume appears
5. Track removals
6. Show device type in logs (SD Card, USB, etc.)

**Supported on macOS:**
- ✅ Built-in SD card readers (MacBook Pro/Air SDXC slots)
- ✅ External USB SD card readers
- ✅ USB flash drives
- ✅ External hard drives
- ✅ Any removable media that mounts to /Volumes

### Testing Volume Detection

**With built-in SD card reader:**
```bash
# Start service
python snapsync.py start

# Insert SD card into built-in slot
# Watch the logs - you should see:
# ✓ Removable volume detected: 'NO NAME'
#   Mount point: /Volumes/NO NAME
#   Type: SD Card Reader
#   Size: 32.0 GB
```

**With external USB reader:**
```bash
# Start service with debug logging
# Edit config.yaml: log_level: "DEBUG"
python snapsync.py start

# Insert SD card
# Watch detailed logs showing diskutil detection
```

**Testing with disk image:**
```bash
# Create test disk image
hdiutil create -size 100m -fs HFS+ -volname "TestSD" test.dmg

# Mount it
hdiutil attach test.dmg

# Should be detected as removable media
```

## Debugging

### Enable Debug Logging
Edit `config.yaml`:
```yaml
service:
  log_level: "DEBUG"
```

### Check What's Being Detected
```bash
# On macOS, check volumes
ls -la /Volumes/

# Check mounted disks
diskutil list

# Monitor volume changes
fswatch /Volumes
```

### Test Individual Components

**Test database**:
```python
import asyncio
from src.database import BackupDatabase

async def test():
    db = BackupDatabase("test.db")
    await db.initialize()
    stats = await db.get_stats()
    print(stats)
    await db.close()

asyncio.run(test())
```

**Test Immich client** (if you have Immich running):
```python
import asyncio
from src.immich_client import ImmichClient

async def test():
    client = ImmichClient("http://localhost:2283", "your-api-key")
    await client.initialize()
    connected = await client.check_connection()
    print(f"Connected: {connected}")
    await client.close()

asyncio.run(test())
```

## Common Issues

### pyudev Installation Error on macOS
**Expected behavior** - pyudev is Linux-only. SnapSync will automatically use macOS-compatible detection.

### SMB Connection Issues on macOS
If testing SMB:
```bash
# Test SMB connection manually
smbutil statshares -a

# Mount SMB share
mount_smbfs //username:password@server/share /Volumes/share
```

### Permission Errors
```bash
# Make sure you have permissions
chmod -R 755 ~/snapsync-backups

# Or use sudo for system directories
sudo python snapsync.py backup /Volumes/SD_CARD
```

## IDE Setup

### VS Code
Install recommended extensions:
- Python (Microsoft)
- Pylance
- Black Formatter

Create `.vscode/settings.json`:
```json
{
    "python.defaultInterpreterPath": "./venv/bin/python",
    "python.formatting.provider": "black",
    "python.linting.enabled": true,
    "python.linting.pylintEnabled": true
}
```

### PyCharm
1. Open project
2. File → Project Structure → Add Content Root → Select snapsync directory
3. File → Settings → Project → Python Interpreter → Select `venv`

## Contributing

When developing new features:

1. Create feature branch
2. Test on macOS using dev mode
3. Test on Linux VM if making platform-specific changes
4. Ensure both platforms work
5. Submit PR

## Docker Development (Optional)

Test Linux behavior on macOS using Docker:

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libudev-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "snapsync.py", "start"]
```

```bash
docker build -t snapsync-dev .
docker run -v $(pwd)/config.yaml:/app/config.yaml snapsync-dev
```

## Useful Commands

```bash
# Format code
black src/

# Type checking
mypy src/

# Run linter
pylint src/

# Clean up
rm -rf __pycache__ src/__pycache__ *.db

# Reset database
rm snapsync.db

# View logs (if running as service on macOS)
tail -f snapsync.log
```

## Next Steps

- Set up Immich locally: https://immich.app/docs/install/docker-compose
- Set up local SMB share for testing
- Set up MQTT broker: `brew install mosquitto`
- Contribute to the project!
