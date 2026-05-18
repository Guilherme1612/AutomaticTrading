"""PMACS CLI entry point — init, start, stop, status, version."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

# Default paths
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("PMACS_DATA_DIR", "/usr/local/var/pmacs"))
_CONFIG_DIR = Path(os.environ.get("PMACS_CONFIG_DIR", str(_PROJECT_ROOT / "config")))
_PID_DIR = _DATA_DIR / "pids"
_HEARTBEAT_DIR = _DATA_DIR / "heartbeats"

# Process definitions: name → (launchd_label, heartbeat_file)
_PROCESSES = {
    "inference": ("com.pmacs.inference", "pmacs-inference.json"),
    "cortex": ("com.pmacs.cortex", "pmacs-cortex.json"),
    "cortex-self-check": ("com.pmacs.cortex-self-check", "pmacs-cortex-self-check.json"),
    "execution": ("com.pmacs.execution", "pmacs-execution.json"),
    "nervous": ("com.pmacs.nervous", "pmacs-nervous.json"),
    "stoploss": ("com.pmacs.stoploss", "pmacs-stoploss.json"),
    "mutation": ("com.pmacs.mutation", "pmacs-mutation.json"),
    "dashboard": ("com.pmacs.dashboard", "pmacs-dashboard.json"),
}

_ORDERED_START = [
    "inference",
    "cortex",
    "cortex-self-check",
    "execution",
    "nervous",
    "stoploss",
    "mutation",
    "dashboard",
]


def _ensure_data_dir() -> None:
    """Create data directories if they don't exist."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PID_DIR.mkdir(parents=True, exist_ok=True)
    _HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)


def cmd_init() -> None:
    """Initialize PMACS databases and directories."""
    from pmacs.storage.sqlite import init_db

    _ensure_data_dir()

    db_path = _DATA_DIR / "pmacs.db"
    print(f"Initializing SQLite at {db_path} ...")
    init_db(str(db_path))
    print("  SQLite OK")

    # KuzuDB (creates on connect)
    kuzu_path = _DATA_DIR / "pmacs.kuzu"
    try:
        from pmacs.storage.kuzu import KuzuDBAdapter
        adapter = KuzuDBAdapter(kuzu_path)
        print(f"  KuzuDB OK ({kuzu_path})")
    except Exception as exc:
        print(f"  KuzuDB: skipped ({exc})")

    # Qdrant (creates collections on connect)
    try:
        from pmacs.storage.qdrant import QdrantAdapter
        adapter = QdrantAdapter()
        print("  Qdrant OK")
    except Exception as exc:
        print(f"  Qdrant: skipped ({exc})")

    # DuckDB (embedded, creates on first write)
    duckdb_path = _DATA_DIR / "analytics.duckdb"
    print(f"  DuckDB: will auto-create at {duckdb_path}")

    # Audit log
    audit_path = _DATA_DIR / "audit.log"
    audit_path.touch()
    print(f"  Audit log: {audit_path}")

    print("\nInitialization complete.")
    print(f"  Data dir:  {_DATA_DIR}")
    print(f"  Config dir: {_CONFIG_DIR}")


def cmd_start() -> None:
    """Start all PMACS processes via launchd."""
    _ensure_data_dir()

    # Check if databases exist
    db_path = _DATA_DIR / "pmacs.db"
    if not db_path.exists():
        print("Databases not initialized. Run 'pmacs init' first.")
        sys.exit(1)

    # Try launchd first (macOS)
    launched = 0
    for name in _ORDERED_START:
        label, _ = _PROCESSES[name]
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if plist.exists():
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True)
                print(f"  Started {name} (launchd)")
            else:
                print(f"  {name} already running")
            launched += 1

    if launched > 0:
        print(f"\n{launched} processes started via launchd.")
        return

    # Fallback: direct process launch
    print("No launchd plists found. Starting processes directly...")

    # Start inference server
    inference_script = _PROJECT_ROOT / "ops" / "start_inference.sh"
    if inference_script.exists():
        subprocess.Popen([str(inference_script)], start_new_session=True)
        print("  Started inference")
    else:
        print("  inference: script not found (ops/start_inference.sh)")

    # Start nervous (FastAPI)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "pmacs.web.app:app",
             "--host", "127.0.0.1", "--port", "8000"],
            start_new_session=True,
        )
        print("  Started nervous on :8000")
    except Exception as exc:
        print(f"  nervous: failed ({exc})")

    # Start dashboard
    try:
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "pmacs.web.app:app",
             "--host", "127.0.0.1", "--port", "8001"],
            start_new_session=True,
        )
        print("  Started dashboard on :8001")
    except Exception as exc:
        print(f"  dashboard: failed ({exc})")

    print("\nProcesses started. Use 'pmacs status' to check health.")


def cmd_stop() -> None:
    """Stop all PMACS processes."""
    stopped = 0
    for name in _ORDERED_START:
        label, _ = _PROCESSES[name]
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if plist.exists():
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                subprocess.run(
                    ["launchctl", "unload", str(plist)],
                    capture_output=True,
                )
                print(f"  Stopped {name} (launchd)")
                stopped += 1

    if stopped > 0:
        print(f"\n{stopped} processes stopped.")
        return

    # Fallback: kill by port
    ports = {"inference": 8080, "nervous": 8000, "dashboard": 8001}
    for name, port in ports.items():
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True,
            )
            if result.stdout.strip():
                pid = int(result.stdout.strip().split("\n")[0])
                os.kill(pid, signal.SIGTERM)
                print(f"  Stopped {name} (PID {pid})")
                stopped += 1
        except (ValueError, ProcessLookupError):
            pass

    if stopped == 0:
        print("No running processes found.")


def cmd_status() -> None:
    """Show status of all PMACS processes."""
    print("PMACS Process Status")
    print("=" * 50)

    all_healthy = True
    for name in _ORDERED_START:
        _, hb_file = _PROCESSES[name]
        hb_path = _HEARTBEAT_DIR / hb_file

        if hb_path.exists():
            try:
                data = json.loads(hb_path.read_text())
                status = data.get("status", "unknown")
                ts = data.get("ts", "?")
                print(f"  {name:20s} {status:10s} (last heartbeat: {ts})")
            except json.JSONDecodeError:
                print(f"  {name:20s} CORRUPT    (heartbeat file invalid)")
                all_healthy = False
        else:
            # Check launchd
            label, _ = _PROCESSES[name]
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"  {name:20s} RUNNING    (no heartbeat yet)")
            else:
                print(f"  {name:20s} STOPPED")
                all_healthy = False

    print()
    # Check database files
    db_path = _DATA_DIR / "pmacs.db"
    print(f"  SQLite:   {'OK' if db_path.exists() else 'MISSING'} ({db_path})")
    print(f"  Config:   {'OK' if _CONFIG_DIR.exists() else 'MISSING'} ({_CONFIG_DIR})")

    if all_healthy:
        print("\nAll processes healthy.")
    else:
        print("\nSome processes not running. Use 'pmacs start' to start them.")


def cmd_version() -> None:
    """Print PMACS version."""
    from pmacs import __version__
    print(f"pmacs {__version__}")


def main() -> None:
    """PMACS command-line interface."""
    if len(sys.argv) < 2:
        print("Usage: pmacs <command>")
        print("Commands: init, start, stop, status, version")
        sys.exit(1)

    command = sys.argv[1]
    commands = {
        "version": cmd_version,
        "init": cmd_init,
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
    }

    handler = commands.get(command)
    if handler:
        handler()
    else:
        print(f"Unknown command: {command}")
        print("Commands: init, start, stop, status, version")
        sys.exit(1)


if __name__ == "__main__":
    main()
