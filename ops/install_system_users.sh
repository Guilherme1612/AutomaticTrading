#!/bin/bash
# Create PMACS system users and set up runtime directories.
# Each PMACS process runs under its own user for defense-in-depth.
# Creates /var/db/pmacs and /var/log/pmacs with correct permissions.
# Must be run with sudo.
# Spec ref: Architecture.md §3 (process isolation)
# Item: 8.11
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
err()   { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }

# ── Require root ────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    err "This script must be run with sudo."
    err "Usage: sudo $0"
    exit 1
fi

# ── Configuration ───────────────────────────────────────────────────────────
PMACS_DB="/var/db/pmacs"
PMACS_LOG="/var/log/pmacs"
PMACS_HOME="/usr/local/var/pmacs"

# All PMACS process users (Architecture.md §4). The combined web + write API
# server runs as _pmacs_nervous (dashboard UI + nervous in one process); there
# is no separate _pmacs_dashboard user. See Architecture.md §2.2 ADR.
USERS=(
    "_pmacs_inference"
    "_pmacs_cortex"
    "_pmacs_execution"
    "_pmacs_nervous"
    "_pmacs_stoploss"
    "_pmacs_mutation"
)

# ── Create system users ────────────────────────────────────────────────────
info "Creating PMACS system users ..."

for user in "${USERS[@]}"; do
    if dscl . -read "/Users/${user}" UniqueID &>/dev/null; then
        ok "User ${user} already exists, skipping."
    else
        # Find next available UID in the 200-400 range (macOS service accounts)
        uid=$(dscl . -list /Users UniqueID 2>/dev/null \
            | awk '{print $2}' \
            | sort -n \
            | tail -1)
        uid=$((uid + 1))
        if [ "$uid" -lt 200 ]; then
            uid=200
        fi
        # Cap at 400 to avoid colliding with real users
        if [ "$uid" -gt 400 ]; then
            err "UID range exhausted. Manually assign a UID for ${user}."
            continue
        fi

        dscl . -create "/Users/${user}"
        dscl . -create "/Users/${user}" UserShell /usr/bin/false
        dscl . -create "/Users/${user}" RealName "PMACS ${user#_pmacs_} process"
        dscl . -create "/Users/${user}" UniqueID "$uid"
        dscl . -create "/Users/${user}" PrimaryGroupID 20  # staff
        dscl . -create "/Users/${user}" NFSHomeDirectory /var/empty

        ok "Created user ${user} (UID=${uid})."
    fi
done

# ── Set up /var/db/pmacs ───────────────────────────────────────────────────
info "Setting up ${PMACS_DB} ..."

mkdir -p "${PMACS_DB}/heartbeat"
mkdir -p "${PMACS_DB}/queue"

# nervous process owns the database directory (primary read/write, serves dashboard UI)
chown -R _pmacs_nervous:staff "${PMACS_DB}"
chmod 750 "${PMACS_DB}"

# Group read access for SQLite (all _pmacs_* users are in staff group)
chmod -R g+r "${PMACS_DB}"

ok "Created ${PMACS_DB} with correct permissions."

# ── Set up /var/log/pmacs ──────────────────────────────────────────────────
info "Setting up ${PMACS_LOG} ..."

mkdir -p "${PMACS_LOG}"

# cortex process owns logs (health monitoring, kill switch events)
chown -R _pmacs_cortex:staff "${PMACS_LOG}"
chmod 750 "${PMACS_LOG}"

# All PMACS users need write access to their own log files
# Using group (staff) read/write
chmod -R g+rw "${PMACS_LOG}"

ok "Created ${PMACS_LOG} with correct permissions."

# ── Set up /usr/local/var/pmacs ────────────────────────────────────────────
info "Setting up ${PMACS_HOME} ..."

mkdir -p "${PMACS_HOME}/models"
mkdir -p "${PMACS_HOME}/config"
mkdir -p "${PMACS_HOME}/ops"

# nervous process owns runtime home
chown -R _pmacs_nervous:staff "${PMACS_HOME}"
chmod 750 "${PMACS_HOME}"

ok "Created ${PMACS_HOME} with correct permissions."

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
ok "PMACS system setup complete."
echo ""
info "Users created:"
for user in "${USERS[@]}"; do
    uid=$(dscl . -read "/Users/${user}" UniqueID 2>/dev/null | awk '{print $2}' || echo "?")
    printf "  %-25s UID=%s\n" "$user" "$uid"
done
echo ""
info "Directories:"
printf "  %-25s %s\n" "Database" "$PMACS_DB"
printf "  %-25s %s\n" "Logs" "$PMACS_LOG"
printf "  %-25s %s\n" "Runtime" "$PMACS_HOME"
echo ""
info "Verify: dscl . -list /Users | grep _pmacs"
