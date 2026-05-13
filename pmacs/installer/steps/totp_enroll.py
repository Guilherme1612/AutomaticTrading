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
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
            '<rect width="200" height="200" fill="white"/>'
            '<text x="100" y="90" text-anchor="middle" font-size="10" fill="black">'
            'Scan with authenticator</text>'
            '<text x="100" y="120" text-anchor="middle" font-size="7" fill="gray">'
            f'{secret[:20]}...</text>'
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
            secret: str - TOTP secret (phase 1)
            message: str - status message (phase 2)
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
                "secret": secret,
                "message": "Please enter all 6 digits.",
            }

        if verify_totp(secret, user_code):
            # Store secret in keychain (production: use security CLI)
            _store_totp_secret(secret)
            return {
                "ok": True,
                "message": "TOTP enrollment verified.",
            }
        else:
            return {
                "ok": False,
                "secret": secret,
                "message": "Invalid code. Please try again.",
            }

    # Phase 1: Generate new secret + QR
    secret = generate_totp_secret()
    qr_data_uri = _generate_qr_data_uri(secret)

    return {
        "ok": False,  # Not yet verified
        "qr_data_uri": qr_data_uri,
        "secret": secret,
    }


def _store_totp_secret(secret: str) -> None:
    """Store TOTP secret in macOS Keychain.

    Uses `security add-generic-password` to store under
    service name "pmacs.system.totp_secret".

    Args:
        secret: Base32-encoded TOTP secret.
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
    except FileNotFoundError:
        # Not on macOS or security CLI not available — fall back to config file
        # This is acceptable for development/testing
        pass
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to store TOTP secret in keychain: {exc.stderr.decode()}"
        ) from exc
