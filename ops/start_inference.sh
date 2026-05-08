#!/bin/bash
# Starts llama-server with the configured GGUF model
# Binds to 127.0.0.1:8080
# Configuration from config/resources.toml (read via python -c)
set -euo pipefail

PMACS_HOME="${PMACS_HOME:-/usr/local/var/pmacs}"
PID_FILE="${PMACS_HOME}/inference.pid"

# Resolve project root (directory containing this script's parent)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Read config values via python
GGUF_PATH=$(cd "$PROJECT_ROOT" && python3 -c "from pmacs.config import load_config; c=load_config(); print(c.resources.gguf_path)")
CTX_SIZE=$(cd "$PROJECT_ROOT" && python3 -c "from pmacs.config import load_config; c=load_config(); print(c.resources.ctx_size)")
THREADS=$(cd "$PROJECT_ROOT" && python3 -c "from pmacs.config import load_config; c=load_config(); print(c.resources.threads)")

# Validate GGUF file exists
if [ ! -f "$GGUF_PATH" ]; then
    echo "ERROR: GGUF file not found at $GGUF_PATH"
    echo "Update config/resources.toml [runtime] gguf_path to point to your model."
    exit 1
fi

# Ensure PMACS_HOME exists
mkdir -p "$PMACS_HOME"

# Start llama-server
llama-server \
    --model "$GGUF_PATH" \
    --host 127.0.0.1 \
    --port 8080 \
    --ctx-size "$CTX_SIZE" \
    --threads "$THREADS" \
    --parallel 1 \
    --cont-batching \
    --metrics \
    &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# Wait for server to be ready (max 120s)
for i in $(seq 1 120); do
    if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
        echo "llama-server ready on :8080 (PID $SERVER_PID)"
        exit 0
    fi
    sleep 1
done

echo "ERROR: llama-server failed to start within 120s"
kill "$SERVER_PID" 2>/dev/null || true
exit 1
