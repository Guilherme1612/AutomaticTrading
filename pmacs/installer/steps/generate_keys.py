"""Wizard step: generate Ed25519 keypair and TOTP secret.

Generates the signing keypair for trade plan signing and a TOTP secret
for operator authentication.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from pmacs.execution.signing import generate_keypair
from pmacs.installer.wizard import Wizard

# Default key paths
DEFAULT_KEY_DIR = Path("keys")
PRIVATE_KEY_FILE = "pmacs_signing.key"
PUBLIC_KEY_FILE = "pmacs_signing.key.pub"
TOTP_SECRET_FILE = "pmacs_totp_secret.b64"


def run(wizard: Wizard, key_dir: Path | None = None) -> dict:
    """Generate Ed25519 keypair and TOTP secret.

    Args:
        wizard: The wizard instance.
        key_dir: Directory to write keys to. Defaults to ./keys.

    Returns:
        Dict with key generation results.
    """
    if key_dir is None:
        key_dir = DEFAULT_KEY_DIR

    key_dir = Path(key_dir)
    key_dir.mkdir(parents=True, exist_ok=True)

    # Generate Ed25519 keypair
    priv_path = key_dir / PRIVATE_KEY_FILE
    priv_bytes, pub_bytes = generate_keypair(private_key_path=priv_path)

    # Generate TOTP secret (20 bytes = 160 bits, standard for TOTP)
    totp_secret = os.urandom(20)
    totp_path = key_dir / TOTP_SECRET_FILE
    totp_path.write_bytes(base64.b64encode(totp_secret))
    totp_path.chmod(0o600)

    return {
        "private_key_path": str(priv_path),
        "public_key_path": str(priv_path.with_suffix(".pub")),
        "totp_secret_path": str(totp_path),
        "key_fingerprint": priv_bytes[:4].hex(),
        "all_ok": priv_path.exists() and totp_path.exists(),
    }
