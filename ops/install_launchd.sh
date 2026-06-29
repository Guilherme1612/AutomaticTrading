#!/bin/bash
# Install all 7 PMACS launchd services from launchd/ directory.
# Loads them with launchctl. Checks for existing services first.
# Includes uninstall option.
# Spec ref: Architecture.md §4 (process topology)
# Item: 4.17
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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHD_DIR="${SCRIPT_DIR}/../launchd"
ACTION="${1:-install}"

PMACS_HOME="/usr/local/var/pmacs"

# The 7 PMACS processes (Architecture.md §4). The combined web + write API
# server (dashboard UI + nervous orchestration/SSE) is a single process served
# by com.pmacs.nervous (uvicorn pmacs.web.app:app on :8000). See Architecture.md
# §2.2 ADR — the dashboard is no longer a separate process/plist.
EXPECTED_PLISTS=(
    "com.pmacs.inference.plist"
    "com.pmacs.cortex.plist"
    "com.pmacs.cortex-self-check.plist"
    "com.pmacs.execution.plist"
    "com.pmacs.nervous.plist"
    "com.pmacs.stoploss.plist"
    "com.pmacs.mutation.plist"
)

usage() {
    echo "Usage: sudo $0 [install|uninstall|status]"
    echo ""
    echo "  install    Load all 7 PMACS launchd plists (default)"
    echo "  uninstall  Unload and remove all PMACS launchd plists"
    echo "  status     Show current status of all PMACS services"
    exit "${1:-0}"
}

# ── Install ─────────────────────────────────────────────────────────────────
do_install() {
    info "Installing PMACS launchd services ..."

    # Validate launchd directory
    if [ ! -d "$LAUNCHD_DIR" ]; then
        err "launchd directory not found: $LAUNCHD_DIR"
        exit 1
    fi

    # Check all expected plists exist
    missing=0
    for plist_name in "${EXPECTED_PLISTS[@]}"; do
        if [ ! -f "${LAUNCHD_DIR}/${plist_name}" ]; then
            err "Missing plist: ${plist_name}"
            missing=$((missing + 1))
        fi
    done

    if [ "$missing" -gt 0 ]; then
        err "${missing} plist(s) missing from ${LAUNCHD_DIR}"
        exit 1
    fi

    # Ensure runtime directories
    mkdir -p "$PMACS_HOME" 2>/dev/null || true

    loaded=0
    skipped=0

    for plist in "${LAUNCHD_DIR}"/*.plist; do
        plist_name=$(basename "$plist")
        label=$(/usr/libexec/PlistBuddy -c "Print :Label" "$plist" 2>/dev/null || echo "")

        if [ -z "$label" ]; then
            err "Cannot read Label from $plist_name, skipping."
            continue
        fi

        # Check if already loaded
        if launchctl list 2>/dev/null | grep -q "$label"; then
            warn "Already loaded: $label -- unloading first."
            launchctl unload "$plist" 2>/dev/null || true
            sleep 1
        fi

        # Copy to /Library/LaunchDaemons if not already there
        SYSTEM_PLIST="/Library/LaunchDaemons/${plist_name}"
        cp "$plist" "$SYSTEM_PLIST" 2>/dev/null || true

        # Load the plist
        launchctl load -w "$plist" 2>/dev/null && {
            ok "Loaded: $label"
            loaded=$((loaded + 1))
        } || {
            err "Failed to load: $label"
            skipped=$((skipped + 1))
        }
    done

    echo ""
    info "Loaded: ${loaded}  Skipped/Failed: ${skipped}"
    info "Verify: launchctl list | grep pmacs"
}

# ── Uninstall ───────────────────────────────────────────────────────────────
do_uninstall() {
    info "Uninstalling PMACS launchd services ..."

    unloaded=0

    for plist_name in "${EXPECTED_PLISTS[@]}"; do
        plist="${LAUNCHD_DIR}/${plist_name}"
        label=$(/usr/libexec/PlistBuddy -c "Print :Label" "$plist" 2>/dev/null || echo "")

        if [ -z "$label" ]; then
            continue
        fi

        # Unload if currently loaded
        if launchctl list 2>/dev/null | grep -q "$label"; then
            launchctl unload "$plist" 2>/dev/null && {
                ok "Unloaded: $label"
                unloaded=$((unloaded + 1))
            } || warn "Failed to unload: $label"
        else
            info "Not loaded: $label"
        fi

        # Remove from system LaunchDaemons
        SYSTEM_PLIST="/Library/LaunchDaemons/${plist_name}"
        if [ -f "$SYSTEM_PLIST" ]; then
            rm -f "$SYSTEM_PLIST"
            info "Removed: $SYSTEM_PLIST"
        fi
    done

    echo ""
    ok "Unloaded ${unloaded} services."
    info "Verify: launchctl list | grep pmacs (should show nothing)"
}

# ── Status ──────────────────────────────────────────────────────────────────
do_status() {
    echo "PMACS Launchd Service Status"
    echo "============================="

    for plist_name in "${EXPECTED_PLISTS[@]}"; do
        plist="${LAUNCHD_DIR}/${plist_name}"
        label=$(/usr/libexec/PlistBuddy -c "Print :Label" "$plist" 2>/dev/null || echo "$plist_name")

        if launchctl list 2>/dev/null | grep -q "$label"; then
            pid=$(launchctl list 2>/dev/null | grep "$label" | awk '{print $1}')
            ok "$label  (PID: ${pid:---})"
        else
            printf "${RED}[STOP]${NC}  %s\n" "$label"
        fi
    done
}

# ── Main ────────────────────────────────────────────────────────────────────
case "$ACTION" in
    install)
        do_install
        ;;
    uninstall|remove)
        do_uninstall
        ;;
    status|show)
        do_status
        ;;
    -h|--help|help)
        usage 0
        ;;
    *)
        err "Unknown action: $ACTION"
        usage 1
        ;;
esac
