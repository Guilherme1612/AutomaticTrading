#!/bin/bash
# Standalone audit chain verification shell wrapper.
# Delegates to the Python implementation for hash-chain integrity checks.
# Reports chain status and entry count.
# Spec ref: Architecture.md §5.1 (audit chain), Phases §15 exit test #4.
# Item: 15.10
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

# ── Resolve paths ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Prefer the Python tool if available ─────────────────────────────────────
# ops/audit_chain_verify.py is the primary implementation with full features
# (--json, --after N, --verbose). This shell wrapper provides a simple CLI
# that also works as a fallback by calling python -m pmacs.storage.audit.

PYTHON_TOOL="${SCRIPT_DIR}/audit_chain_verify.py"

if [ -f "$PYTHON_TOOL" ]; then
    info "Using audit_chain_verify.py ..."
    python3 "$PYTHON_TOOL" "$@"
    exit $?
fi

# ── Fallback: direct module invocation ──────────────────────────────────────
info "Python tool not found, falling back to module invocation ..."

AUDIT_LOG="${PROJECT_ROOT}/data/audit.log"

if [ ! -f "$AUDIT_LOG" ]; then
    err "Audit log not found: $AUDIT_LOG"
    err "Run a PMACS cycle first to generate audit entries."
    exit 2
fi

# Add project root to PYTHONPATH so pmacs.storage.audit is importable
export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}"

info "Verifying audit chain integrity ..."
info "Log file: $AUDIT_LOG"

python3 -m pmacs.storage.audit verify "$AUDIT_LOG" 2>/dev/null && {
    ok "Audit chain integrity verified."
    exit 0
} || {
    err "Audit chain verification FAILED."
    err "The hash chain is broken or the log is corrupted."
    exit 1
}
