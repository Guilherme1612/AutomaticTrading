"""Unit tests for pmacs.execution.signing — Ed25519 key generation and signing."""
from __future__ import annotations

import stat
from pathlib import Path

import pytest

from pmacs.execution.signing import (
    generate_keypair,
    load_private_key,
    load_public_key,
    sign_bytes,
    verify_signature,
)


class TestGenerateKeypair:
    """Tests for generate_keypair."""

    def test_returns_32_byte_keys(self) -> None:
        priv, pub = generate_keypair()
        assert len(priv) == 32
        assert len(pub) == 32

    def test_different_keys_each_call(self) -> None:
        priv1, pub1 = generate_keypair()
        priv2, pub2 = generate_keypair()
        assert priv1 != priv2
        assert pub1 != pub2


class TestSignAndVerify:
    """Tests for sign_bytes and verify_signature."""

    def test_sign_verify_succeeds(self) -> None:
        priv, pub = generate_keypair()
        data = b"trade plan payload"
        sig = sign_bytes(data, priv)
        assert verify_signature(data, sig, pub) is True

    def test_tampered_data_fails(self) -> None:
        priv, pub = generate_keypair()
        data = b"original payload"
        sig = sign_bytes(data, priv)
        assert verify_signature(b"tampered payload", sig, pub) is False

    def test_tampered_signature_fails(self) -> None:
        priv, pub = generate_keypair()
        data = b"trade plan payload"
        sig = sign_bytes(data, priv)
        tampered = bytes((b + 1) % 256 for b in sig)
        assert verify_signature(data, tampered, pub) is False

    def test_wrong_key_fails(self) -> None:
        priv1, pub1 = generate_keypair()
        priv2, pub2 = generate_keypair()
        data = b"trade plan payload"
        sig = sign_bytes(data, priv1)
        # Verify with unrelated public key
        assert verify_signature(data, sig, pub2) is False


class TestFileBasedKeypair:
    """Tests for keypair persistence to disk."""

    def test_generate_and_load(self, tmp_path: Path) -> None:
        key_path = tmp_path / "keys" / "trade.key"
        priv, pub = generate_keypair(key_path)

        # Files exist
        assert key_path.exists()
        assert key_path.with_suffix(".pub").exists()

        # Correct permissions
        priv_stat = key_path.stat()
        pub_stat = key_path.with_suffix(".pub").stat()
        assert stat.S_IMODE(priv_stat.st_mode) == 0o600
        assert stat.S_IMODE(pub_stat.st_mode) == 0o644

        # Load and use
        loaded_priv = load_private_key(key_path)
        loaded_pub = load_public_key(key_path.with_suffix(".pub"))
        assert loaded_priv == priv
        assert loaded_pub == pub

        # Sign with loaded key, verify with loaded pub
        data = b"signed trade plan"
        sig = sign_bytes(data, loaded_priv)
        assert verify_signature(data, sig, loaded_pub) is True

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        key_path = tmp_path / "deep" / "nested" / "dir" / "key.pem"
        generate_keypair(key_path)
        assert key_path.exists()
        assert key_path.with_suffix(".pub").exists()
