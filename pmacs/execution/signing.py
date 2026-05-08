"""Ed25519 keypair generation and trade plan signing."""
from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate_keypair(
    private_key_path: Path | None = None,
) -> tuple[bytes, bytes]:
    """Generate Ed25519 keypair. Returns (private_key_bytes, public_key_bytes)."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    if private_key_path is not None:
        private_key_path.parent.mkdir(parents=True, exist_ok=True)
        private_key_path.write_bytes(priv_bytes)
        private_key_path.chmod(0o600)

        pub_path = private_key_path.with_suffix(".pub")
        pub_path.write_bytes(pub_bytes)
        pub_path.chmod(0o644)

    return priv_bytes, pub_bytes


def sign_bytes(data: bytes, private_key: bytes) -> bytes:
    """Sign data with Ed25519 private key."""
    private_key = Ed25519PrivateKey.from_private_bytes(private_key)
    return private_key.sign(data)


def verify_signature(data: bytes, signature: bytes, public_key: bytes) -> bool:
    """Verify Ed25519 signature. Returns True if valid."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    pub = Ed25519PublicKey.from_public_bytes(public_key)
    try:
        pub.verify(signature, data)
        return True
    except InvalidSignature:
        return False


def load_private_key(path: Path) -> bytes:
    """Load Ed25519 private key from file."""
    return path.read_bytes()


def load_public_key(path: Path) -> bytes:
    """Load Ed25519 public key from file."""
    return path.read_bytes()
