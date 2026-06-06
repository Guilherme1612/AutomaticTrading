#!/usr/bin/env bash
# ops/compute_model_hash.sh — Compute SHA256 of the Qwen3.6 GGUF model file
# and update config/model_hashes.toml automatically.
#
# Usage:
#   ./ops/compute_model_hash.sh /path/to/Qwen3.6-35B-A3B-Q4_K_XL.gguf
#
# The model can be downloaded from HuggingFace:
#   huggingface-cli download unsloth/Qwen3.6-35B-A3B-GGUF \
#     UD-Q4_K_XL --local-dir ./models/
#
# Spec ref: Architecture.md §4.1 — GGUF SHA256 verification

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <path-to-gguf-file>"
    echo ""
    echo "Download the model first:"
    echo "  huggingface-cli download unsloth/Qwen3.6-35B-A3B-GGUF UD-Q4_K_XL --local-dir ./models/"
    exit 1
fi

GGUF_PATH="$1"
HASHES_FILE="$(dirname "$0")/../config/model_hashes.toml"

if [ ! -f "$GGUF_PATH" ]; then
    echo "ERROR: File not found: $GGUF_PATH"
    exit 1
fi

echo "Computing SHA256 of $(basename "$GGUF_PATH")..."
echo "(This may take several minutes for large model files)"
HASH=$(shasum -a 256 "$GGUF_PATH" | awk '{print $1}')
echo "SHA256: $HASH"

# Update model_hashes.toml
if [ -f "$HASHES_FILE" ]; then
    sed -i.bak "s/PLACEHOLDER_SHA256_VERIFY_BEFORE_USE/$HASH/" "$HASHES_FILE"
    rm -f "$HASHES_FILE.bak"
    echo "Updated $HASHES_FILE"
else
    echo "[gguf]" > "$HASHES_FILE"
    echo "\"Qwen3.6-35B-A3B-Q4_K_XL\" = \"$HASH\"" >> "$HASHES_FILE"
    echo "Created $HASHES_FILE"
fi
echo "Done."
