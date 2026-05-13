"""Wizard step: TOTP enrollment.

Generates TOTP secret, returns QR code data URI for display,
accepts user's TOTP code for verification.

On success: secret stored in keychain as pmacs.system.totp_secret.

Spec ref: Source.md §12.1 Step 9, Architecture.md §5 (kill switch TOTP)
"""
from __future__ import annotations

import base64
import io
import urllib.parse
from pathlib import Path

from pmacs.cortex.totp import generate_totp_secret, verify_totp


def _generate_qr_data_uri(secret: str, issuer: str = "PMACS") -> str:
    """Generate a QR code data URI for TOTP enrollment.

    Uses the otpauth:// URI scheme. QR encoding is done via
    a minimal SVG-based QR code (no external dependency).

    For production, use qrcode library. This generates an SVG
    placeholder with the otpauth URI embedded.

    Args:
        secret: Base32-encoded TOTP secret.
        issuer: Issuer name for authenticator app display.

    Returns:
        Data URI string (image/svg+xml) containing QR code.
    """
    label = urllib.parse.quote(f"{issuer}:Operator")
    otpauth_uri = (
        f"otpauth://totp/{label}"
        f"?secret={secret}"
        f"&issuer={issuer}"
        f"&algorithm=SHA1"
        f"&digits=6"
        f"&period=30"
    )

    # Generate a minimal SVG QR-code placeholder
    # In production, replace with actual QR encoding (qrcode library)
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage

        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(otpauth_uri)
        qr.make(fit=True)
        img = qr.make_image(image_factory=SvgPathImage)

        svg_buffer = io.BytesIO()
        img.save(svg_buffer)
        svg_data = svg_buffer.getvalue().decode("utf-8")
        b64 = base64.b64encode(svg_data.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"
    except ImportError:
        # Fallback: embed the otpauth URI in a simple SVG
        # Wrap full secret across multiple lines so operator can enter manually
        line_len = 16
        secret_lines = [secret[i:i + line_len] for i in range(0, len(secret), line_len)]
        secret_svg = "".join(
            f'<tspan x="100" dy="14">{line}</tspan>' for line in secret_lines
        )
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 260">'
            '<rect width="200" height="260" fill="white"/>'
            '<text x="100" y="30" text-anchor="middle" font-size="10" fill="black">'
            'Scan with authenticator</text>'
            '<text x="100" y="60" text-anchor="middle" font-size="7" fill="gray">'
            f'{secret_svg}</text>'
            '</svg>'
        )
        b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{b64}"


async def run(form_data: dict) -> dict:
    """Execute TOTP enrollment step.

    Two phases:
    1. Generate secret + QR code (no TOTP code in form_data)
    2. Verify user's TOTP code (totp_code in form_data)

    Args:
        form_data: Dict with form fields.
            - totp_secret: existing secret (phase 2)
            - totp_0..totp_5: individual digits (phase 2)
            OR empty/missing (phase 1: generate)

    Returns:
        Dict with:
            ok: bool - enrollment complete
            qr_data_uri: str - QR code for display (phase 1)
            message: str - status message (phase 2)
            _secret: str - TOTP secret (internal, NOT rendered in templates)
    """
    # Phase 2: Verify TOTP code
    secret = form_data.get("totp_secret", "")
    if secret:
        # Reconstruct code from individual digits
        digits = []
        for i in range(6):
            digit = form_data.get(f"totp_{i}", "")
            if digit:
                digits.append(str(digit))
        user_code = "".join(digits)

        if not user_code or len(user_code) != 6:
            return {
                "ok": False,
                "_secret": secret,
                "message": "Please enter all 6 digits.",
            }

        if verify_totp(secret, user_code):
            # Store secret in keychain (production: use security CLI)
            stored = _store_totp_secret(secret)
            if not stored:
                return {
                    "ok": False,
                    "_secret": secret,
                    "message": "TOTP verified but secret storage failed. "
                               "Please check system keychain or run with --file-fallback.",
                }
            return {
                "ok": True,
                "message": "TOTP enrollment verified.",
            }
        else:
            return {
                "ok": False,
                "_secret": secret,
                "message": "Invalid code. Please try again.",
            }

    # Phase 1: Generate new secret + QR
    secret = generate_totp_secret()
    qr_data_uri = _generate_qr_data_uri(secret)

    return {
        "ok": False,  # Not yet verified
        "qr_data_uri": qr_data_uri,
        "_secret": secret,
    }


def _store_totp_secret(secret: str) -> bool:
    """Store TOTP secret in macOS Keychain.

    Uses `security add-generic-password` to store under
    service name "pmacs.system.totp_secret".

    On non-macOS systems or when security CLI is unavailable, falls back
    to a file-based store with owner-only permissions (0600).

    Args:
        secret: Base32-encoded TOTP secret.

    Returns:
        True if storage succeeded, False otherwise.
    """
    import subprocess

    service = "pmacs.system.totp_secret"
    account = "operator"

    try:
        # Delete existing entry first (in case of re-enrollment)
        subprocess.run(
            ["security", "delete-generic-password", "-s", service],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass  # May not exist yet

    try:
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-s", service,
                "-a", account,
                "-w", secret,
            ],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return True
    except FileNotFoundError:
        # Not on macOS or security CLI not available -- fall back to file
        return _store_totp_secret_file(secret)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to store TOTP secret in keychain: {exc.stderr.decode()}"
        ) from exc


def _store_totp_secret_file(secret: str) -> bool:
    """File-based fallback for TOTP secret storage.

    Writes to ~/.pmacs/totp_secret with owner-only permissions.
    NOT as secure as a keychain, but functional for non-macOS systems.

    Args:
        secret: Base32-encoded TOTP secret.

    Returns:
        True if file written successfully, False otherwise.
    """
    import os
    import stat

    try:
        totp_dir = Path.home() / ".pmacs"
        totp_dir.mkdir(parents=True, exist_ok=True)
        secret_file = totp_dir / "totp_secret"
        secret_file.write_text(secret, encoding="utf-8")
        # Owner read/write only (0600)
        secret_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return True
    except Exception:
        return False
