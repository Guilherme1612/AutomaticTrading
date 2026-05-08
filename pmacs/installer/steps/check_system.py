"""Wizard step: check system prerequisites.

Verifies Python version, available disk space, and required dependencies.
"""
from __future__ import annotations

import platform
import shutil
import sys

from pmacs.installer.wizard import Wizard


def run(wizard: Wizard) -> dict:
    """Check system prerequisites.

    Returns:
        Dict with system info and check results.
    """
    results: dict = {
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.system(),
        "platform_release": platform.release(),
        "checks": {},
    }

    # Python version check (>= 3.11)
    py_ok = sys.version_info >= (3, 11)
    results["checks"]["python_version"] = {
        "ok": py_ok,
        "message": "Python 3.11+ required" if not py_ok else "OK",
    }

    # Disk space check (at least 10 GB free)
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024 ** 3)
        disk_ok = free_gb >= 10.0
        results["checks"]["disk_space"] = {
            "ok": disk_ok,
            "free_gb": round(free_gb, 1),
            "message": f"{free_gb:.1f} GB free" if disk_ok else "Need >= 10 GB free",
        }
    except OSError:
        results["checks"]["disk_space"] = {"ok": False, "message": "Cannot check disk space"}

    # Key dependencies
    deps = ["cryptography", "pydantic"]
    for dep in deps:
        try:
            __import__(dep)
            results["checks"][f"dep_{dep}"] = {"ok": True, "message": "OK"}
        except ImportError:
            results["checks"][f"dep_{dep}"] = {"ok": False, "message": f"Missing: {dep}"}

    results["all_ok"] = all(c["ok"] for c in results["checks"].values())
    return results
