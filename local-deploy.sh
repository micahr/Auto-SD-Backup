#!/bin/bash
#
# Script to deploy the SnapSync project to a remote machine using rsync.
#

set -e # Exit immediately if a command exits with a non-zero status.

# --- Configuration ---
# You can pre-fill these variables or provide them as command-line arguments.
REMOTE_USER=""
REMOTE_HOST=""
DEST_PATH=""

# --- Exclusions ---
# List of files/directories to exclude from the transfer.
# .gitignore is also used by rsync if available.
EXCLUDES=(
  ".git"
  "venv"
  "__pycache__"
  "*.pyc"
  "*.db"
  "*.db-journal"
  ".env"
  "GEMINI.md"
  "deploy.sh"
)

# --- Functions ---
function print_usage() {
  echo "Usage: $0 -u <user> -h <host> -p <path>"
  echo "  -u: Username for the remote machine."
  echo "  -h: Hostname or IP address of the remote machine."
  echo "  -p: Absolute destination path on the remote machine (e.g., /home/user/snapsync)."
  exit 1
}

# --- Argument Parsing ---
while getopts "u:h:p:" opt; do
  case ${opt} in
    u ) REMOTE_USER=$OPTARG;;
    h ) REMOTE_HOST=$OPTARG;;
    p ) DEST_PATH=$OPTARG;;
    \? ) print_usage;;
  esac
done

if [ -z "${REMOTE_USER}" ] || [ -z "${REMOTE_HOST}" ] || [ -z "${DEST_PATH}" ]; then
    echo "Error: Missing required arguments."
    print_usage
fi

# --- Main Script ---
echo "▶️  Starting deployment to ${REMOTE_USER}@${REMOTE_HOST}..."

# Get the directory of the script itself.
SOURCE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Build the exclude options for rsync.
EXCLUDE_OPTS=""
for item in "${EXCLUDES[@]}"; do
  EXCLUDE_OPTS+="--exclude=${item} "
done

# Perform the rsync.
# -a: archive mode (preserves permissions, ownership, etc.)
# -v: verbose
# -z: compress file data during the transfer
# --delete: delete extraneous files from the destination directory
echo "  Source: ${SOURCE_DIR}/"
echo "  Destination: ${REMOTE_USER}@${REMOTE_HOST}:${DEST_PATH}"

rsync -avz --delete \
  ${EXCLUDE_OPTS} \
  "${SOURCE_DIR}/" \
  "${REMOTE_USER}@${REMOTE_HOST}:${DEST_PATH}"

echo "✅ Deployment complete!"
echo "➡️  Next steps on the remote machine:"
echo "   1. ssh ${REMOTE_USER}@${REMOTE_HOST}"
echo "   2. cd ${DEST_PATH}"
echo "   3. sudo ./install.sh"
