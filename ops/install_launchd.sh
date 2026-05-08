#!/bin/bash
# Install all PMACS launchd services
# Creates users, directories, loads plists
set -euo pipefail

PMACS_HOME="/usr/local/var/pmacs"
PMACS_DB="/var/db/pmacs"
PMACS_LOG="/var/log/pmacs"
LAUNCHD_DIR="$(cd "$(dirname "$0")" && pwd)/../launchd"

# Create directories
sudo mkdir -p "$PMACS_HOME" "$PMACS_DB" "$PMACS_LOG" "$PMACS_DB/heartbeat"
sudo mkdir -p "$PMACS_HOME/ops" "$PMACS_HOME/config"

# Create process users (macOS)
for user in _pmacs_inference _pmacs_cortex _pmacs_exec _pmacs_nervous _pmacs_dashboard; do
    if ! dscl . -read "/Users/$user" UniqueID &>/dev/null; then
        echo "Creating user: $user"
        sudo dscl . -create "/Users/$user"
        sudo dscl . -create "/Users/$user" UserShell /usr/bin/false
        sudo dscl . -create "/Users/$user" UniqueID $(dscl . -list /Users UniqueID | awk '{print $2}' | sort -n | tail -1 | xargs -I{} expr {} + 1)
        sudo dscl . -create "/Users/$user" PrimaryGroupID 20
    fi
done

# Set permissions
sudo chown -R _pmacs_cortex "$PMACS_DB"
sudo chown -R _pmacs_nervous "$PMACS_HOME"

# Load plists
for plist in "$LAUNCHD_DIR"/*.plist; do
    label=$(/usr/libexec/PlistBuddy -c "Print :Label" "$plist")
    if launchctl list | grep -q "$label"; then
        echo "Unloading existing: $label"
        launchctl unload "$plist" 2>/dev/null || true
    fi
    echo "Loading: $plist"
    sudo launchctl load -w "$plist"
done

echo "PMACS services installed. Check status with: launchctl list | grep pmacs"
