#!/bin/bash
# Install pf firewall rules to block PMACS inference process from internet.
# macOS pf (packet filter) anchors for pmacs-inference: deny all outbound
# except loopback. Enforces local-only LLM execution (Non-Negotiable #4).
# Spec ref: Architecture.md §4.1, §3 (process isolation)
# Items: 3.8, 4.18
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

PF_ANCHOR="/etc/pf.anchors/pmacs"
PF_CONF="/etc/pf.conf"
ACTION="${1:-install}"

usage() {
    echo "Usage: sudo $0 [install|uninstall|status]"
    echo ""
    echo "  install    Install pf rules to block inference from internet (default)"
    echo "  uninstall  Remove PMACS pf rules and restore pf.conf"
    echo "  status     Show current PMACS pf rule status"
    exit "${1:-0}"
}

# ── Require root ────────────────────────────────────────────────────────────
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        err "This script must be run with sudo."
        err "Usage: sudo $0 $ACTION"
        exit 1
    fi
}

# ── Install ─────────────────────────────────────────────────────────────────
do_install() {
    info "Installing PMACS pf rules ..."

    # Create pf anchor file
    cat > "$PF_ANCHOR" << 'RULES'
# PMACS pf rules -- block inference process from internet
# Spec: Architecture.md §4.1 (local-only execution)
#
# Block _pmacs_inference user from all outbound except loopback.
# The inference process (llama-server) MUST NOT have internet egress.
# Only loopback (127.0.0.1 / ::1) is permitted for inter-process communication.

# Block IPv4 outbound for _pmacs_inference (allow loopback only)
block drop out inet user _pmacs_inference from any to ! 127.0.0.1

# Block IPv6 outbound for _pmacs_inference (allow loopback only)
block drop out inet6 user _pmacs_inference from any to ! ::1
RULES

    ok "Created anchor: $PF_ANCHOR"

    # Add anchor reference to pf.conf if not already present
    if grep -q "pmacs" "$PF_CONF" 2>/dev/null; then
        info "pf.conf already contains PMACS anchor reference."
    else
        echo "" >> "$PF_CONF"
        echo "# PMACS: block inference from internet" >> "$PF_CONF"
        echo "anchor \"pmacs\"" >> "$PF_CONF"
        echo "load anchor \"pmacs\" from \"/etc/pf.anchors/pmacs\"" >> "$PF_CONF"
        ok "Added anchor reference to pf.conf"
    fi

    # Enable pf and reload rules
    pfctl -ef 2>/dev/null || true
    pfctl -f "$PF_CONF" 2>/dev/null || true

    ok "pf rules installed and active."
    echo ""
    info "Verify: sudo pfctl -sr 2>/dev/null | grep pmacs"
    info "Test:   sudo -u _pmacs_inference curl -sf https://example.com (should fail)"
}

# ── Uninstall ───────────────────────────────────────────────────────────────
do_uninstall() {
    info "Uninstalling PMACS pf rules ..."

    # Remove anchor file
    if [ -f "$PF_ANCHOR" ]; then
        rm -f "$PF_ANCHOR"
        ok "Removed anchor: $PF_ANCHOR"
    else
        warn "Anchor file not found: $PF_ANCHOR"
    fi

    # Remove PMACS lines from pf.conf
    if [ -f "$PF_CONF" ]; then
        # Create a temp file without PMACS lines
        TMP_PF=$(mktemp)
        grep -v "pmacs" "$PF_CONF" > "$TMP_PF" || true
        # Also remove trailing blank lines that may accumulate
        sed -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$TMP_PF" > "${TMP_PF}.cleaned"
        mv "${TMP_PF}.cleaned" "$PF_CONF"
        rm -f "$TMP_PF"
        ok "Cleaned pf.conf"
    fi

    # Reload pf without PMACS rules
    pfctl -f "$PF_CONF" 2>/dev/null || true

    ok "PMACS pf rules uninstalled."
}

# ── Status ──────────────────────────────────────────────────────────────────
do_status() {
    echo "PMACS pf Rule Status"
    echo "===================="

    if [ -f "$PF_ANCHOR" ]; then
        ok "Anchor file exists: $PF_ANCHOR"
        echo "--- Contents ---"
        cat "$PF_ANCHOR"
        echo "--- End ---"
    else
        err "Anchor file missing: $PF_ANCHOR"
    fi

    echo ""
    if grep -q "pmacs" "$PF_CONF" 2>/dev/null; then
        ok "pf.conf contains PMACS reference"
    else
        warn "pf.conf does NOT contain PMACS reference"
    fi

    echo ""
    info "Active rules matching pmacs:"
    pfctl -sr 2>/dev/null | grep -i pmacs || warn "No active PMACS rules found"
}

# ── Main ────────────────────────────────────────────────────────────────────
case "$ACTION" in
    install)
        check_root
        do_install
        ;;
    uninstall|remove)
        check_root
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
