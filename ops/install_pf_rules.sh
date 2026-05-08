#!/bin/bash
# Install pf rules to block inference process from internet
set -euo pipefail

PF_RULES="/etc/pf.anchors/pmacs"
PF_CONF="/etc/pf.conf"

# Create pf anchor for PMACS
sudo tee "$PF_RULES" > /dev/null << 'EOF'
# PMACS pf rules — block inference process from internet
# Block _pmacs_inference user from all outbound except loopback
block drop out user _pmacs_inference from any to ! 127.0.0.1
block drop out user _pmacs_inference from any to ! ::1
EOF

# Add anchor to pf.conf if not already present
if ! grep -q "pmacs" "$PF_CONF" 2>/dev/null; then
    echo "anchor \"pmacs\"" | sudo tee -a "$PF_CONF"
    echo "load anchor \"pmacs\" from \"/etc/pf.anchors/pmacs\"" | sudo tee -a "$PF_CONF"
fi

# Enable rules
sudo pfctl -ef 2>/dev/null || true
sudo pfctl -f "$PF_CONF" 2>/dev/null || true

echo "PF rules installed. Verify: sudo pfctl -sr | grep pmacs"
