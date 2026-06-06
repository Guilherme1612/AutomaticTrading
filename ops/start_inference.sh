#!/bin/bash
# Start llama-server with the configured Qwen3.6 GGUF model.
# Binds to 127.0.0.1:8080 (pf-blocked from internet).
# Config: config/model_registry.json for model path, config/resources.toml for runtime params.
# Spec ref: Architecture.md §4.1
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

PMACS_HOME="${PMACS_HOME:-/usr/local/var/pmacs}"
PID_FILE="${PMACS_HOME}/inference.pid"

REGISTRY="${PROJECT_ROOT}/config/model_registry.json"
RESOURCES="${PROJECT_ROOT}/config/resources.toml"

# ── Validate prerequisites ──────────────────────────────────────────────────
if ! command -v llama-server &>/dev/null; then
    err "llama-server not found in PATH."
    err "Install: brew install llama.cpp  or  build from source."
    exit 1
fi

if [ ! -f "$REGISTRY" ]; then
    err "Model registry not found: $REGISTRY"
    exit 1
fi

if [ ! -f "$RESOURCES" ]; then
    err "Resources config not found: $RESOURCES"
    exit 1
fi

# ── Read config ─────────────────────────────────────────────────────────────
# Model name from model_registry.json
DEFAULT_MODEL=$(python3 -c "
import json, sys
with open('$REGISTRY') as f:
    cfg = json.load(f)
active = cfg.get('active', 'llama_server')
backend = cfg['backends'][active]
print(backend.get('default_model', ''))
")

# Runtime params from resources.toml
GGUF_PATH=$(python3 -c "
import sys
for line in open('$RESOURCES'):
    if 'gguf_path' in line and '=' in line:
        print(line.split('=',1)[1].strip().strip('\"').strip(\"'\"))
        break
")

CTX_SIZE=$(python3 -c "
import sys
for line in open('$RESOURCES'):
    if 'ctx_size' in line and '=' in line:
        print(line.split('=',1)[1].strip())
        break
")

THREADS=$(python3 -c "
import sys
for line in open('$RESOURCES'):
    if line.strip().startswith('threads') and '=' in line:
        print(line.split('=',1)[1].strip())
        break
")

PARALLEL_SLOTS=$(python3 -c "
import sys
for line in open('$RESOURCES'):
    if 'parallel_slots' in line and '=' in line:
        print(line.split('=',1)[1].strip())
        break
")

# Defaults if parsing fails
CTX_SIZE="${CTX_SIZE:-32768}"
THREADS="${THREADS:-8}"
PARALLEL_SLOTS="${PARALLEL_SLOTS:-3}"

# ── Validate GGUF ───────────────────────────────────────────────────────────
if [ -z "$GGUF_PATH" ]; then
    err "gguf_path not set in $RESOURCES"
    exit 1
fi

if [ ! -f "$GGUF_PATH" ]; then
    err "GGUF file not found: $GGUF_PATH"
    err "Update config/resources.toml [runtime] gguf_path to point to your model."
    exit 1
fi

info "Model: $DEFAULT_MODEL"
info "GGUF:  $GGUF_PATH"
info "Ctx:   $CTX_SIZE  Threads: $THREADS  Slots: $PARALLEL_SLOTS"

# ── Check for running instance ──────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        err "llama-server already running (PID $OLD_PID)."
        err "Stop it first: kill $OLD_PID"
        exit 1
    else
        warn "Stale PID file found, removing."
        rm -f "$PID_FILE"
    fi
fi

# ── Ensure runtime dir ──────────────────────────────────────────────────────
mkdir -p "$PMACS_HOME"

# ── Start llama-server ──────────────────────────────────────────────────────
info "Starting llama-server on 127.0.0.1:8080 ..."

llama-server \
    --model "$GGUF_PATH" \
    --host 127.0.0.1 \
    --port 8080 \
    --ctx-size "$CTX_SIZE" \
    --threads "$THREADS" \
    --parallel "$PARALLEL_SLOTS" \
    --cont-batching \
    --metrics \
    &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# ── Health check loop ───────────────────────────────────────────────────────
MAX_WAIT=120
info "Waiting for server to respond (max ${MAX_WAIT}s) ..."

for i in $(seq 1 "$MAX_WAIT"); do
    if curl -sf http://127.0.0.1:8080/health >/dev/null 2>&1; then
        ok "llama-server ready on :8080 (PID $SERVER_PID)"
        exit 0
    fi

    # Check if process is still alive
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        err "llama-server process died during startup."
        rm -f "$PID_FILE"
        exit 1
    fi

    # Progress every 10s
    if (( i % 10 == 0 )); then
        info "  ... ${i}s elapsed, still waiting"
    fi

    sleep 1
done

err "llama-server failed to start within ${MAX_WAIT}s."
kill "$SERVER_PID" 2>/dev/null || true
rm -f "$PID_FILE"
exit 1
