#!/bin/bash

# SnapSync Installation Script
# This script installs SnapSync as a systemd service

set -e

echo "========================================="
echo "  SnapSync Installation Script"
echo "========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Detect user
if [ -n "$SUDO_USER" ]; then
    INSTALL_USER=$SUDO_USER
    INSTALL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    INSTALL_USER=$(whoami)
    INSTALL_HOME=$HOME
fi

echo "Installing for user: $INSTALL_USER"
echo "Home directory: $INSTALL_HOME"
echo ""

# Get the absolute path of the script's directory
INSTALL_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "Installing from directory: $INSTALL_DIR"
echo ""

# Ensure the user owns the installation directory
chown -R "$INSTALL_USER:$INSTALL_USER" "$INSTALL_DIR"

# Check Python version
echo "Checking Python version..."
python3 --version

if ! python3 -c 'import sys; exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "Error: Python 3.9 or higher is required"
    exit 1
fi

# Install system dependencies
echo ""
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3-pip python3-venv libudev-dev exfatprogs

# Create virtual environment
echo ""
echo "Creating Python virtual environment..."
cd "$INSTALL_DIR"
sudo -u "$INSTALL_USER" python3 -m venv venv

# Install Python dependencies
echo "Installing Python dependencies..."
sudo -u "$INSTALL_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$INSTALL_USER" "$INSTALL_DIR/venv/bin/pip" install -r requirements.txt

# Create config from template if it doesn't exist
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    echo ""
    echo "Creating configuration file..."
    sudo -u "$INSTALL_USER" cp "$INSTALL_DIR/config.yaml.example" "$INSTALL_DIR/config.yaml"
    echo "Please edit $INSTALL_DIR/config.yaml with your settings"
fi

# Create .env from template if it doesn't exist
if [ ! -f "$INSTALL_DIR/.env" ]; then
    sudo -u "$INSTALL_USER" cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "Please edit $INSTALL_DIR/.env with your credentials"
fi

# Update systemd service file with correct paths and user
echo ""
echo "Installing systemd service..."
sed -e "s|User=pi|User=$INSTALL_USER|g" \
    -e "s|Group=pi|Group=$INSTALL_USER|g" \
    -e "s|WorkingDirectory=/home/pi/snapsync|WorkingDirectory=$INSTALL_DIR|g" \
    -e "s|ExecStart=/usr/bin/python3 /home/pi/snapsync/snapsync.py|ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/snapsync.py|g" \
    "$INSTALL_DIR/snapsync.service" > /etc/systemd/system/snapsync.service

# Reload systemd
systemctl daemon-reload

# Enable service to start on boot
echo "Enabling service..."
systemctl enable snapsync

# Setup auto-mounting via udev
echo ""
echo "Setting up auto-mounting rules..."
cat > /etc/udev/rules.d/99-snapsync-automount.rules << EOF
# Auto-mount USB drives (partitions)
KERNEL=="sd[a-z][0-9]", ACTION=="add", RUN+="/usr/bin/systemd-mount --no-block --automount=no --collect \$devnode"
# Auto-mount SD cards (MMC partitions)
KERNEL=="mmcblk[0-9]p[0-9]", ACTION=="add", RUN+="/usr/bin/systemd-mount --no-block --automount=no --collect \$devnode"
EOF

# Reload udev rules
udevadm control --reload-rules
udevadm trigger

# Create symlink for CLI
echo ""
echo "Creating CLI symlink..."
ln -sf "$INSTALL_DIR/venv/bin/python" /usr/local/bin/snapsync-python
cat > /usr/local/bin/snapsync << EOF
#!/bin/bash
cd "$INSTALL_DIR"
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/snapsync.py" "\$@"
EOF
chmod +x /usr/local/bin/snapsync

echo ""
echo "========================================="
echo "  Installation Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Edit configuration: nano $INSTALL_DIR/config.yaml"
echo "2. Add credentials: nano $INSTALL_DIR/.env"
echo "3. Test connection: snapsync test-connection"
echo "4. Enable service: sudo systemctl enable snapsync"
echo "5. Start service: sudo systemctl start snapsync"
echo "6. Check status: sudo systemctl status snapsync"
echo "7. View logs: sudo journalctl -u snapsync -f"
echo "8. Access web UI: http://localhost:8080"
echo ""
echo "CLI commands:"
echo "  snapsync start          - Start the service (foreground)"
echo "  snapsync status         - Show current status"
echo "  snapsync sessions       - List recent backup sessions"
echo "  snapsync test-connection - Test Immich/Unraid connections"
echo "  snapsync web            - Start web UI only"
echo ""
