"""Wizard step: generate Ed25519 keypair.

Generates the signing keypair for trade plan signing.
"""
from __future__ import annotations

from pathlib import Path

from pmacs.execution.signing import generate_keypair
from pmacs.installer.wizard import Wizard

# Default key paths
DEFAULT_KEY_DIR = Path("keys")
PRIVATE_KEY_FILE = "pmacs_signing.key"
PUBLIC_KEY_FILE = "pmacs_signing.key.pub"


def run(wizard: Wizard, key_dir: Path | None = None) -> dict:
    """Generate Ed25519 keypair.

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
    key_dir.chmod(0o700)  # Restrict directory for signing keys

    # Generate Ed25519 keypair
    priv_path = key_dir / PRIVATE_KEY_FILE
    priv_bytes, pub_bytes = generate_keypair(private_key_path=priv_path)

    return {
        "private_key_path": str(priv_path),
        "public_key_path": str(priv_path.with_suffix(".pub")),
        "key_fingerprint": priv_bytes[:4].hex(),
        "all_ok": priv_path.exists(),
    }
