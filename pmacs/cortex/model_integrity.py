"""Model integrity verification — GGUF SHA256 hash checking (Architecture.md §5.3).

Verifies that the loaded GGUF model matches the expected hash from
config/model_hashes.toml. Prevents running tampered or corrupted models.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pmacs.logsys import log_debug

_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB


def verify_gguf_hash(gguf_path: Path, expected_sha256: str) -> bool:
    """Verify SHA256 hash of a GGUF model file.

    Args:
        gguf_path: Path to the GGUF file on disk.
        expected_sha256: Expected hex-encoded SHA256 digest.

    Returns:
        True if hash matches, False otherwise.
    """
    if not gguf_path.exists():
        log_debug(
            "MODEL_INTEGRITY_CHECK",
            payload={
                "path": str(gguf_path),
                "error": "file not found",
            },
            level="WARN",
            error_code="MODEL_HASH_MISMATCH",
            msg=f"GGUF file not found: {gguf_path}",
        )
        return False

    sha256 = hashlib.sha256()
    try:
        with open(gguf_path, "rb") as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)
    except OSError as exc:
        log_debug(
            "MODEL_INTEGRITY_CHECK",
            payload={
                "path": str(gguf_path),
                "error": str(exc),
            },
            level="WARN",
            error_code="MODEL_HASH_MISMATCH",
            msg=f"Failed to read GGUF file: {exc}",
        )
        return False

    actual = sha256.hexdigest()
    match = actual == expected_sha256

    if match:
        log_debug(
            "MODEL_INTEGRITY_CHECK",
            payload={
                "path": str(gguf_path),
                "hash": actual[:16] + "...",
                "status": "OK",
            },
            level="DEBUG",
            msg="GGUF hash verified successfully",
        )
    else:
        log_debug(
            "MODEL_INTEGRITY_CHECK",
            payload={
                "path": str(gguf_path),
                "expected": expected_sha256[:16] + "...",
                "actual": actual[:16] + "...",
                "status": "MISMATCH",
            },
            level="WARN",
            error_code="MODEL_HASH_MISMATCH",
            msg="GGUF hash mismatch — model may be corrupted or tampered",
        )

    return match


def check_model_integrity() -> bool:
    """Load config and verify the active model's GGUF hash.

    Reads config/model_hashes.toml and config/resources.toml to find
    the GGUF path and expected hash.

    Returns:
        True if the model hash matches or no hash is configured,
        False on mismatch or error.
    """
    try:
        from pmacs.config import load_config

        config = load_config()
    except Exception as exc:
        log_debug(
            "MODEL_INTEGRITY_CHECK",
            payload={"error": str(exc)},
            level="WARN",
            error_code="MODEL_HASH_MISMATCH",
            msg=f"Failed to load config: {exc}",
        )
        return False

    gguf_path_str = config.resources.gguf_path
    if not gguf_path_str:
        # No GGUF path configured — nothing to verify
        log_debug(
            "MODEL_INTEGRITY_CHECK",
            payload={"status": "SKIPPED", "reason": "no gguf_path configured"},
            level="DEBUG",
            msg="No GGUF path configured, skipping integrity check",
        )
        return True

    gguf_path = Path(gguf_path_str)

    # Get the expected hash for the model name derived from path
    model_name = gguf_path.stem
    expected_hash = config.model_hashes.get(model_name, "")

    if not expected_hash:
        log_debug(
            "MODEL_INTEGRITY_CHECK",
            payload={
                "model_name": model_name,
                "status": "SKIPPED",
                "reason": "no hash configured for model",
            },
            level="DEBUG",
            msg=f"No expected hash for model '{model_name}', skipping",
        )
        return True

    return verify_gguf_hash(gguf_path, expected_hash)
