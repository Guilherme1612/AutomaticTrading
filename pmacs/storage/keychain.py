"""macOS Keychain wrapper for API key management (Architecture.md §18).

Error recovery:
- Startup degraded mode: if Keychain is unavailable at startup, the calling
  process should log a warning and continue in degraded mode (no API keys).
- Runtime cycle abort: if Keychain fails mid-cycle, the cycle should be
  aborted with ABORT_PRE_LLM since no evidence can be fetched without keys.
"""

from __future__ import annotations

import subprocess

# Error codes referenced from error_classifier.py
KEYCHAIN_UNAVAILABLE = "KEYCHAIN_UNAVAILABLE"
KEYCHAIN_RUNTIME_FAILURE = "KEYCHAIN_RUNTIME_FAILURE"


class KeychainError(Exception):
    """Raised when Keychain access fails."""


def _scrub_secrets(message: str, secrets: list[str]) -> str:
    """Replace each secret substring with ***REDACTED***.

    Args:
        message: The message potentially containing secrets.
        secrets: List of secret strings to redact.

    Returns:
        Message with all secrets replaced.
    """
    result = message
    for secret in secrets:
        if secret and secret in result:
            result = result.replace(secret, "***REDACTED***")
    return result


def get_api_key(service: str, account: str) -> str:
    """Retrieve an API key from macOS Keychain.

    Args:
        service: The service name (e.g., 'pmacs-polygon').
        account: The account name (e.g., 'api-key').

    Returns:
        The API key string.

    Raises:
        KeychainError: If the key is not found or access fails.
            Error messages are scrubbed to never expose key values.
    """
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise KeychainError(
                f"Key not found: service={service!r}, account={account!r}. "
                f"Run: security add-generic-password -s {service!r} -a {account!r} -w <YOUR_KEY>"
            )
        key = result.stdout.strip()
        if not key:
            raise KeychainError(f"Empty key for service={service!r}, account={account!r}")
        return key
    except subprocess.TimeoutExpired:
        raise KeychainError(f"Keychain access timed out for service={service!r}")
    except FileNotFoundError:
        raise KeychainError("security CLI not found (macOS only)")
    except KeychainError:
        raise
    except Exception as exc:
        raise KeychainError(
            _scrub_secrets(f"Keychain read failed: {exc}", [str(exc)])
        ) from exc


def set_api_key(service: str, account: str, key: str) -> None:
    """Store an API key in macOS Keychain.

    Args:
        service: The service name (e.g., 'pmacs-polygon').
        account: The account name (e.g., 'api-key').
        key: The API key to store.

    Raises:
        KeychainError: If storage fails. Error messages scrubbed.
    """
    try:
        # Delete existing if present
        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True,
            timeout=10,
        )

        result = subprocess.run(
            ["security", "add-generic-password", "-s", service, "-a", account, "-w", key],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise KeychainError(
                _scrub_secrets(f"Failed to store key: {result.stderr}", [key, result.stderr])
            )
    except subprocess.TimeoutExpired:
        raise KeychainError(f"Keychain access timed out for service={service!r}")
    except FileNotFoundError:
        raise KeychainError("security CLI not found (macOS only)")
    except KeychainError:
        raise
    except Exception as exc:
        raise KeychainError(
            _scrub_secrets(f"Keychain write failed: {exc}", [key, str(exc)])
        ) from exc


def read_key(dotted_name: str) -> str | None:
    """Retrieve an API key using a dotted service name.

    Convenience wrapper around get_api_key that splits a dotted name like
    ``"pmacs.finnhub.api_key"`` into service=``"pmacs.finnhub"`` and
    account=``"api_key"``.  Returns ``None`` on any failure instead of
    raising, so callers can fall back gracefully.

    Args:
        dotted_name: Dotted string ``"<service>.<account>"`` or
            ``"<service>.<sub>.<account>"``.  The *last* segment is the
            account; everything before it is the service.

    Returns:
        The key string, or ``None`` if not found / unavailable.
    """
    parts = dotted_name.rsplit(".", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    service, account = parts
    try:
        return get_api_key(service, account)
    except KeychainError:
        return None


def rotate_api_key(service: str, account: str, old_key: str, new_key: str) -> None:
    """Rotate an API key: verify old key matches, then replace with new.

    Args:
        service: The service name.
        account: The account name.
        old_key: The current key value (must match stored key).
        new_key: The new key value to store.

    Raises:
        KeychainError: If old_key doesn't match or rotation fails.
            Error messages scrubbed to never expose key values.
    """
    try:
        current = get_api_key(service, account)
        if current != old_key:
            raise KeychainError(
                f"Key rotation failed: old_key does not match current value "
                f"for service={service!r}, account={account!r}"
            )
        set_api_key(service, account, new_key)
    except KeychainError:
        raise
    except Exception as exc:
        raise KeychainError(
            _scrub_secrets(f"Key rotation failed: {exc}", [old_key, new_key, str(exc)])
        ) from exc
