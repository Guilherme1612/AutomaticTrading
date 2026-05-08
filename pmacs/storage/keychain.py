"""macOS Keychain wrapper for API key management (Architecture.md §18)."""

from __future__ import annotations

import subprocess


class KeychainError(Exception):
    """Raised when Keychain access fails."""


def get_api_key(service: str, account: str) -> str:
    """Retrieve an API key from macOS Keychain.

    Args:
        service: The service name (e.g., 'pmacs-polygon').
        account: The account name (e.g., 'api-key').

    Returns:
        The API key string.

    Raises:
        KeychainError: If the key is not found or access fails.
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


def set_api_key(service: str, account: str, key: str) -> None:
    """Store an API key in macOS Keychain.

    Args:
        service: The service name (e.g., 'pmacs-polygon').
        account: The account name (e.g., 'api-key').
        key: The API key to store.

    Raises:
        KeychainError: If storage fails.
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
            raise KeychainError(f"Failed to store key: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise KeychainError(f"Keychain access timed out for service={service!r}")
    except FileNotFoundError:
        raise KeychainError("security CLI not found (macOS only)")
