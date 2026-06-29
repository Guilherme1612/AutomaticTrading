"""PMACS CLI entry point — init, start, stop, status, version."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

# Default paths — centralized via config.data_dir()
from pmacs.config import data_dir as _get_data_dir
from pmacs.storage.sqlite import connect as _sql_connect

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _get_data_dir()
_CONFIG_DIR = Path(os.environ.get("PMACS_CONFIG_DIR", str(_PROJECT_ROOT / "config")))
_PID_DIR = _DATA_DIR / "pids"
_HEARTBEAT_DIR = _DATA_DIR / "heartbeats"


def _resolve_python() -> str:
    """Return the Python executable that has pmacs dependencies installed.

    Prefers the project .venv if present, otherwise falls back to sys.executable.
    """
    venv_candidates = [
        _PROJECT_ROOT / ".venv" / "bin" / "python3",
        _PROJECT_ROOT / ".venv" / "bin" / "python",
    ]
    for candidate in venv_candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable

# Process definitions: name → (launchd_label, heartbeat_file)
_PROCESSES = {
    "inference": ("com.pmacs.inference", "pmacs-inference.json"),
    "cortex": ("com.pmacs.cortex", "pmacs-cortex.json"),
    "cortex-self-check": ("com.pmacs.cortex-self-check", "pmacs-cortex-self-check.json"),
    "execution": ("com.pmacs.execution", "pmacs-execution.json"),
    "nervous": ("com.pmacs.nervous", "pmacs-nervous.json"),
    "stoploss": ("com.pmacs.stoploss", "pmacs-stoploss.json"),
    "mutation": ("com.pmacs.mutation", "pmacs-mutation.json"),
}

_ORDERED_START = [
    "inference",
    "cortex",
    "cortex-self-check",
    "execution",
    "nervous",
    "stoploss",
    "mutation",
]


def _ensure_data_dir() -> None:
    """Create data directories if they don't exist."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _PID_DIR.mkdir(parents=True, exist_ok=True)
    _HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)


def _is_wizard_completed() -> bool:
    """Check if the first-run wizard has been completed by reading SQLite."""
    db_path = _DATA_DIR / "pmacs.db"
    if not db_path.exists():
        return False
    try:
        conn = _sql_connect(db_path)
        row = conn.execute(
            "SELECT value FROM wizard_state WHERE key = ?", ("wizard_completed",)
        ).fetchone()
        conn.close()
        return row is not None and row[0] == "1"
    except Exception:
        return False


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


def _read_gguf_path() -> str:
    """Read gguf_path from resources.toml, or empty string."""
    resources_path = _CONFIG_DIR / "resources.toml"
    if not resources_path.exists():
        return ""
    for line in resources_path.read_text().splitlines():
        if "gguf_path" in line and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _read_active_backend() -> str:
    """Read the active backend from model_registry.json, or empty string."""
    import json as _json
    registry_path = _CONFIG_DIR / "model_registry.json"
    if not registry_path.exists():
        return ""
    try:
        return _json.loads(registry_path.read_text()).get("active", "")
    except Exception:
        return ""


def cmd_start() -> None:
    """Start all PMACS processes via launchd."""
    _ensure_data_dir()

    # Check if wizard has been completed
    if not _is_wizard_completed():
        print("First-run setup not completed.")
        print()
        print("  Launching the setup wizard in your browser...")
        print()
        cmd_wizard()
        return

    # Check if databases exist (should exist after wizard, but safety check)
    db_path = _DATA_DIR / "pmacs.db"
    if not db_path.exists():
        print("Databases not initialized. Launching wizard...")
        cmd_wizard()
        return

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

    # Fallback: direct process launch (all 8 processes, spec dependency order)
    print("No launchd plists found. Starting processes directly...")

    python = _resolve_python()

    # Check model availability — local GGUF or cloud backend both count
    gguf_path = _read_gguf_path()
    has_local_model = gguf_path and Path(gguf_path).exists()
    active_backend = _read_active_backend()
    cloud_backends = {"openrouter", "anthropic", "openai"}
    has_cloud_backend = active_backend in cloud_backends
    has_model = has_local_model or has_cloud_backend

    if not has_model:
        print()
        print("  NOTE: No GGUF model found. Run 'pmacs setup' to configure one.")
        print("        Starting in simulation mode (no LLM inference).")
        print()

    # 1. Inference
    if has_cloud_backend:
        # Cloud backend — no local server needed, personas call API directly
        print(f"  inference: cloud backend ({active_backend}) — no local server needed")
    elif has_local_model:
        inference_script = _PROJECT_ROOT / "ops" / "start_inference.sh"
        if inference_script.exists():
            subprocess.Popen([str(inference_script)], start_new_session=True)
            print("  Started inference (llama-server on :8080)")
        else:
            print("  inference: script not found (ops/start_inference.sh)")
    else:
        print("  inference: SKIPPED (no model file — simulation mode)")

    # 2. Cortex daemon (health monitoring, kill switch)
    try:
        subprocess.Popen(
            [python, "-m", "pmacs.cortex.daemon"],
            start_new_session=True,
        )
        print("  Started cortex")
    except Exception as exc:
        print(f"  cortex: failed ({exc})")

    # 2.5. Cortex self-check (meta-monitor)
    try:
        subprocess.Popen(
            [python, "-m", "pmacs.cortex.self_check"],
            start_new_session=True,
        )
        print("  Started cortex-self-check")
    except Exception as exc:
        print(f"  cortex-self-check: failed ({exc})")

    # 3. Execution service (UDS trade signing)
    try:
        subprocess.Popen(
            [python, "-m", "pmacs.execution.service"],
            start_new_session=True,
        )
        print("  Started execution")
    except Exception as exc:
        print(f"  execution: failed ({exc})")

    # 4. Combined web + API server on :8000
    if _check_port("127.0.0.1", 8000):
        print("  nervous  already running on :8000")
    else:
        _kill_port(8000)
        try:
            subprocess.Popen(
                [python, "-m", "uvicorn", "pmacs.web.app:app",
                 "--host", "127.0.0.1", "--port", "8000"],
                start_new_session=True,
            )
            print("  Started nervous+dashboard on :8000")
        except Exception as exc:
            print(f"  nervous: failed ({exc})")

    # 5. Stop-loss daemon (RTH position monitoring)
    try:
        subprocess.Popen(
            [python, "-m", "pmacs.cortex.stop_loss_daemon"],
            start_new_session=True,
        )
        print("  Started stoploss")
    except Exception as exc:
        print(f"  stoploss: failed ({exc})")

    # 6. Mutation daemon (flywheel, dormant first 50 cycles)
    try:
        subprocess.Popen(
            [python, "-m", "pmacs.mutation.daemon"],
            start_new_session=True,
        )
        print("  Started mutation")
    except Exception as exc:
        print(f"  mutation: failed ({exc})")

    # (dashboard is served from :8000 in the combined app)

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

    # Fallback: kill by port (network services) + find daemon PIDs
    ports = {
        "inference": 8080,
        "nervous": 8000,
    }
    # Daemon processes (no port) — find by module name
    daemon_modules = [
        "pmacs.cortex.daemon",
        "pmacs.cortex.self_check",
        "pmacs.cortex.stop_loss_daemon",
        "pmacs.mutation.daemon",
        "pmacs.execution.service",
    ]
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

    # Kill daemon processes by matching module name in process list
    try:
        ps_out = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True,
        ).stdout
        for mod in daemon_modules:
            for line in ps_out.splitlines():
                if mod in line and "grep" not in line:
                    parts = line.split()
                    if parts and parts[1].isdigit():
                        try:
                            os.kill(int(parts[1]), signal.SIGTERM)
                            print(f"  Stopped {mod} (PID {parts[1]})")
                            stopped += 1
                        except (ValueError, ProcessLookupError):
                            pass
    except Exception:
        pass

    if stopped == 0:
        print("No running processes found.")


def cmd_clean() -> None:
    """Remove all personal data (databases, logs, heartbeats, pids).

    Keeps config/ intact. Re-initialize with 'pmacs init' or 'pmacs setup'.
    """
    # Accept --force / -f to skip confirmation
    force = "--force" in sys.argv or "-f" in sys.argv

    if not _DATA_DIR.exists():
        print("No data directory found. Nothing to clean.")
        return

    # Enumerate what will be deleted
    targets = []
    total_size = 0
    for item in sorted(_DATA_DIR.rglob("*")):
        if item.is_file():
            size = item.stat().st_size
            total_size += size
            rel = item.relative_to(_DATA_DIR)
            targets.append(rel)

    if not targets:
        print("Data directory is empty. Nothing to clean.")
        return

    print(f"PMACS Clean — will delete {len(targets)} file(s) ({_fmt_size(total_size)})")
    print(f"  Data dir: {_DATA_DIR}")
    for t in targets[:15]:
        print(f"    - {t}")
    if len(targets) > 15:
        print(f"    ... and {len(targets) - 15} more")

    if not force:
        print("  WARNING: This will also delete .env (API keys will need to be re-entered).")
        print()
        try:
            answer = input("  Delete all personal data? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            print("Aborted.")
            return
        if answer not in ("y", "yes"):
            print("Aborted.")
            return

    # Stop processes first
    print("Stopping processes...")
    cmd_stop()

    # Delete data directory contents, then recreate structure
    print("Cleaning data...")
    shutil.rmtree(_DATA_DIR)
    _ensure_data_dir()

    # Also clear .env if it exists (API keys)
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        env_path.unlink()
        print("  Removed .env (API keys)")

    print("Clean complete. Run 'pmacs init' to re-initialize databases.")


def _fmt_size(n: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def cmd_status() -> None:
    """Show status of all PMACS processes."""
    import time
    from pmacs.cortex.health import check_heartbeats

    print("PMACS Process Status")
    print("=" * 50)

    # Map display name → heartbeat proc name (as written by write_heartbeat calls).
    # Processes with no heartbeat writer use pgrep as fallback.
    _PROC_HB_NAMES: dict[str, str | None] = {
        "inference": None,            # skipped in simulation mode
        "cortex": "pmacs-cortex",
        "cortex-self-check": "cortex-self-check",
        "execution": None,            # UDS service — no heartbeat writer yet
        "nervous": "nervous",
        "stoploss": "pmacs-stoploss",
        "mutation": "pmacs-mutation",
    }
    # Module fragments used for pgrep fallback (processes without heartbeat files)
    _PROC_PGREP: dict[str, str] = {
        "execution": "pmacs.execution.service",
        "mutation": "pmacs.mutation.daemon",
    }

    hb_proc_names = [v for v in _PROC_HB_NAMES.values() if v is not None]
    # Use 120s threshold — some processes write every 60s (self-check, mutation)
    statuses = check_heartbeats(hb_proc_names, heartbeat_dir=_HEARTBEAT_DIR, stale_threshold=120.0)
    by_hb = {s.proc: s for s in statuses}

    all_healthy = True
    for name in _ORDERED_START:
        hb_name = _PROC_HB_NAMES[name]

        if name == "inference":
            print(f"  {name:20s} SKIPPED    (simulation mode — no model)")
            continue

        if hb_name is not None:
            s = by_hb.get(hb_name)
            if s and not s.is_stale:
                ts_str = time.strftime("%H:%M:%S", time.localtime(s.last_ts)) if s.last_ts else "?"
                print(f"  {name:20s} RUNNING    (last heartbeat: {ts_str})")
            else:
                print(f"  {name:20s} STOPPED")
                all_healthy = False
        else:
            # Execution: check for live UDS socket (created when service is listening)
            if name == "execution":
                sock_path = _DATA_DIR / "exec.sock"
                if sock_path.exists():
                    print(f"  {name:20s} RUNNING    (socket: exec.sock)")
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


def cmd_wizard() -> None:
    """Launch the web-based setup wizard in a browser.

    Starts the nervous API temporarily on :8000 to serve the wizard UI,
    then opens the browser to /wizard. After wizard completes, the user
    should run 'pmacs start' to launch all processes.
    """
    import webbrowser

    wizard_url = "http://127.0.0.1:8000/wizard/"

    # Check if the combined server is already running
    if _check_port("127.0.0.1", 8000):
        print(f"Opening wizard in browser: {wizard_url}")
        webbrowser.open(wizard_url)
        return

    # Start combined server on :8000 temporarily for the wizard
    python = _resolve_python()
    _kill_port(8000)
    proc = subprocess.Popen(
        [python, "-m", "uvicorn", "pmacs.web.app:app",
         "--host", "127.0.0.1", "--port", "8000"],
        start_new_session=True,
    )
    print(f"Started server on :8000 for wizard (PID {proc.pid})")
    print(f"Opening wizard in browser: {wizard_url}")
    webbrowser.open(wizard_url)
    print()
    print("After completing the wizard, run 'pmacs start' to launch all processes.")


def _prompt(prompt: str, default: str = "") -> str:
    """Ask the user a question with an optional default."""
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value or default


def _check_port(host: str, port: int) -> bool:
    """Check if a port is already in use."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except (ConnectionRefusedError, OSError):
        return False


def _kill_port(port: int) -> None:
    """Kill any process listening on *port* (macOS)."""
    result = subprocess.run(
        ["lsof", "-ti", f":{port}"],
        capture_output=True, text=True,
    )
    pids = result.stdout.strip().split()
    for pid in pids:
        if pid.isdigit():
            subprocess.run(["kill", pid], capture_output=True)
            print(f"  killed stale PID {pid} on :{port}")


def cmd_setup() -> None:
    """Interactive one-shot setup: configure, init, and start PMACS.

    Asks about model location, API keys, then initializes databases,
    writes config, and starts all services.
    """
    import hashlib

    print()
    print("╔═══════════════════════════════════════════════════╗")
    print("║           PMACS Setup Wizard (CLI)                ║")
    print("║     Portfolio Management & Catalyst Automation    ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()

    # ── Step 1: Model path ──────────────────────────────────────────────────
    resources_path = _CONFIG_DIR / "resources.toml"

    # Read current gguf_path from config
    current_gguf = ""
    if resources_path.exists():
        for line in resources_path.read_text().splitlines():
            if "gguf_path" in line and "=" in line:
                current_gguf = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    print("Step 1: LLM Model")
    print("  PMACS uses a local LLM for analysis. You need a GGUF model file.")
    if current_gguf and Path(current_gguf).exists():
        print(f"  Current model: {current_gguf}")
    print()

    has_model = _prompt("Do you have a GGUF model file? (y/n)", "y")
    gguf_path = current_gguf

    if has_model.lower() == "y":
        if not gguf_path or not Path(gguf_path).exists():
            gguf_path = _prompt("Enter path to GGUF model file", gguf_path or "")
            gguf_path = str(Path(gguf_path).expanduser().resolve())

        if gguf_path and Path(gguf_path).exists():
            # Compute SHA256 (first 1MB for speed)
            sha256 = hashlib.sha256()
            with open(gguf_path, "rb") as f:
                for chunk in iter(lambda: f.read(1_048_576), b""):
                    sha256.update(chunk)
            file_hash = sha256.hexdigest()[:16]
            file_size_gb = Path(gguf_path).stat().st_size / (1024 ** 3)
            print(f"  Model found: {Path(gguf_path).name} ({file_size_gb:.1f} GB, SHA256: {file_hash}...)")

            # Update resources.toml
            if resources_path.exists():
                content = resources_path.read_text()
                new_content = []
                for line in content.splitlines():
                    if "gguf_path" in line and "=" in line:
                        new_content.append(f'gguf_path = "{gguf_path}"')
                    else:
                        new_content.append(line)
                resources_path.write_text("\n".join(new_content) + "\n")
                print("  Updated config/resources.toml")
        else:
            print("  Model file not found. Will run in simulation mode.")
            gguf_path = ""
    else:
        print("  No model — will run in simulation mode (no LLM inference).")
        gguf_path = ""

    print()

    # ── Step 2: Broker API keys ─────────────────────────────────────────────
    print("Step 2: Broker (Alpaca Paper Trading)")
    print("  PMACS paper-trades via Alpaca. Get keys at https://app.alpaca.markets")
    print("  (Paper trading account, free). Leave blank to skip and configure later.")
    print()

    alpaca_key = _prompt("Alpaca API Key", "")
    alpaca_secret = _prompt("Alpaca API Secret", "")

    if alpaca_key and alpaca_secret:
        try:
            import keyring
            keyring.set_password("pmacs.credentials", "alpaca_api_key", alpaca_key)
            keyring.set_password("pmacs.credentials", "alpaca_api_secret", alpaca_secret)
            print("  Stored in system keychain.")
        except Exception:
            # Fallback: write to .env
            env_path = _PROJECT_ROOT / ".env"
            env_path.write_text(
                f"ALPACA_API_KEY={alpaca_key}\nALPACA_API_SECRET={alpaca_secret}\n"
            )
            print(f"  Written to {env_path}")
    else:
        print("  Skipped — will use mock adapter for now.")

    print()

    # ── Step 3: Install dependencies ────────────────────────────────────────
    print("Step 3: Install Dependencies")
    pip_path = shutil.which("pip") or shutil.which("pip3")
    if not pip_path:
        pip_path = str(Path(sys.executable).parent / "pip")

    # Collect missing packages (core runtime only — embeddings/playwright/qrcode
    # are optional extras; see pyproject [project.optional-dependencies]).
    required_packages = [
        "pydantic>=2.5",
        "httpx>=0.27",
        "cryptography>=42.0",
        "keyring>=25.0",
        "fastapi>=0.110",
        "python-multipart>=0.0.7",
        "uvicorn>=0.29",
        "sse-starlette>=2.0",
        "jinja2>=3.1.6",
        "duckdb>=1.5.2",
        "yfinance>=0.2",
        "kuzu>=0.5.0",
        "qdrant-client>=1.12.0",
        "pytz>=2024.1",
    ]
    import importlib
    import re

    # Map pip specifiers to import names
    _pip_to_import = {
        "pydantic": "pydantic",
        "httpx": "httpx",
        "cryptography": "cryptography",
        "keyring": "keyring",
        "fastapi": "fastapi",
        "python-multipart": "multipart",
        "uvicorn": "uvicorn",
        "sse-starlette": "sse_starlette",
        "jinja2": "jinja2",
        "duckdb": "duckdb",
        "yfinance": "yfinance",
        "kuzu": "kuzu",
        "qdrant-client": "qdrant_client",
        "pytz": "pytz",
    }

    missing = []
    for spec in required_packages:
        pkg_name = re.split(r"[>=<~!]", spec)[0]
        import_name = _pip_to_import.get(pkg_name, pkg_name.replace("-", "_"))
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(spec)

    if missing:
        print(f"  {len(missing)} package(s) missing:")
        for pkg in missing:
            print(f"    - {pkg}")
        print(f"  Installing via {sys.executable} ...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *missing],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("  All packages installed.")
        else:
            print(f"  pip install failed: {result.stderr.strip()}")
            print("  Install manually: pip install -e .")
    else:
        print("  All Python packages OK.")

    # Check external tools
    if not shutil.which("llama-server"):
        print("  llama-server not found — install with: brew install llama.cpp")
        print("  (PMACS will run in simulation mode without it)")
    else:
        print("  llama-server OK.")

    print()

    # ── Step 4: Initialize databases ────────────────────────────────────────
    print("Step 4: Initialize Databases")
    _ensure_data_dir()

    # SQLite
    from pmacs.storage.sqlite import init_db as sqlite_init
    db_path = _DATA_DIR / "pmacs.db"
    sqlite_init(str(db_path))
    print(f"  SQLite     {db_path}")

    # KuzuDB
    try:
        from pmacs.storage.kuzu import KuzuDBAdapter
        kuzu = KuzuDBAdapter(_DATA_DIR / "pmacs_graph.kuzu")
        kuzu.initialize()
        print(f"  KuzuDB     {_DATA_DIR}/pmacs_graph.kuzu")
    except Exception as exc:
        print(f"  KuzuDB     skipped ({exc})")

    # DuckDB
    try:
        from pmacs.storage.duckdb import DuckDBAdapter
        duckdb = DuckDBAdapter(_DATA_DIR / "pmacs_analytics.duckdb")
        duckdb.initialize()
        print(f"  DuckDB     {_DATA_DIR}/pmacs_analytics.duckdb")
    except Exception as exc:
        print(f"  DuckDB     skipped ({exc})")

    # Qdrant
    try:
        from pmacs.storage.qdrant import QdrantAdapter
        qdrant = QdrantAdapter()
        qdrant.initialize()
        print(f"  Qdrant     OK")
    except Exception as exc:
        print(f"  Qdrant     skipped ({exc})")

    # Audit genesis
    from pmacs.storage.audit import AuditWriter
    audit_path = _DATA_DIR / "audit.log"
    writer = AuditWriter(audit_path)
    writer.append("SYSTEM_GENESIS", {"event": "cli_setup"}, cycle_id="genesis")
    writer.close()
    print(f"  Audit      {audit_path}")

    # Mode promotion
    import sqlite3
    from pmacs.engines.mode_manager import transition_mode
    from pmacs.schemas.system import Mode
    try:
        mt = transition_mode(
            from_mode=Mode.INSTALLING,
            to_mode=Mode.PAPER,
            reason="CLI setup complete",
            operator_confirmed=False,
        )
        conn = _sql_connect(db_path)
        conn.execute(
            "INSERT INTO mode_history (from_mode, to_mode, reason, triggered_by, changed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mt.from_mode.value, mt.to_mode.value, mt.reason, mt.triggered_by,
             mt.changed_at.isoformat()),
        )
        conn.commit()
        conn.close()
        print(f"  Mode       INSTALLING -> PAPER")
    except Exception as exc:
        print(f"  Mode       skipped ({exc})")

    print()

    # ── Step 5: Start services ──────────────────────────────────────────────
    print("Step 5: Start Services")
    started = []

    # llama-server (only if local model; cloud backends need no local server)
    _active_backend = _read_active_backend()
    _cloud_backends = {"openrouter", "anthropic", "openai"}
    if _active_backend in _cloud_backends:
        print(f"  inference  cloud backend ({_active_backend}) — no local server needed")
    elif gguf_path and Path(gguf_path).exists():
        if _check_port("127.0.0.1", 8080):
            print("  inference  already running on :8080")
        elif not shutil.which("llama-server"):
            print("  inference  llama-server not found (install: brew install llama.cpp)")
            print("             Will run in simulation mode.")
        else:
            inference_script = _PROJECT_ROOT / "ops" / "start_inference.sh"
            if inference_script.exists():
                proc = subprocess.Popen(
                    [str(inference_script)],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                print(f"  inference  starting on :8080 (PID {proc.pid})")
                started.append(proc.pid)
            else:
                print("  inference  ops/start_inference.sh not found")
    else:
        print("  inference  SKIPPED (no model — simulation mode)")

    # Combined web + API server on :8000
    if _check_port("127.0.0.1", 8000):
        print("  nervous+dashboard  already running on :8000")
    else:
        _kill_port(8000)
        proc = subprocess.Popen(
            [_resolve_python(), "-m", "uvicorn", "pmacs.web.app:app",
             "--host", "127.0.0.1", "--port", "8000"],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print(f"  nervous+dashboard  starting on :8000 (PID {proc.pid})")
        started.append(proc.pid)

    print()

    # ── Done ────────────────────────────────────────────────────────────────
    print("═" * 53)
    print("  Setup complete!")
    print()
    print(f"  Dashboard:  http://127.0.0.1:8000")
    if not gguf_path or not Path(gguf_path).exists():
        print("  LLM Mode:   SIMULATION (no model file)")
    elif not shutil.which("llama-server"):
        print("  LLM Mode:   SIMULATION (llama-server not installed)")
    else:
        print("  LLM Mode:   LIVE (llama-server)")
    print()
    print("  Next steps:")
    print("    pmacs status    — check running processes")
    print("    pmacs stop      — stop all processes")
    print("    pmacs setup     — reconfigure and restart")
    print("═" * 53)


def cmd_watch() -> None:
    """Supervise PMACS processes — restart any that die.

    Polls heartbeats every 30s. If a process is stale (no heartbeat for >120s),
    restarts it. Runs until Ctrl-C.
    """
    import time
    from pmacs.cortex.health import check_heartbeats

    _ensure_data_dir()

    # Process name → restart command (same as cmd_start, minus inference)
    python = _resolve_python()
    _RESTART_CMDS: dict[str, list[str]] = {
        "cortex": [python, "-m", "pmacs.cortex.daemon"],
        "cortex-self-check": [python, "-m", "pmacs.cortex.self_check"],
        "execution": [python, "-m", "pmacs.execution.service"],
        "stoploss": [python, "-m", "pmacs.cortex.stop_loss_daemon"],
        "mutation": [python, "-m", "pmacs.mutation.daemon"],
    }
    # nervous runs via uvicorn
    _RESTART_CMDS["nervous"] = [
        python, "-m", "uvicorn", "pmacs.web.app:app",
        "--host", "127.0.0.1", "--port", "8000",
    ]

    _HB_NAMES: dict[str, str] = {
        "cortex": "pmacs-cortex",
        "cortex-self-check": "cortex-self-check",
        "nervous": "nervous",
        "stoploss": "pmacs-stoploss",
        "mutation": "pmacs-mutation",
    }

    print("PMACS Supervisor — watching processes (Ctrl-C to stop)")
    print("=" * 50)

    restart_counts: dict[str, int] = {name: 0 for name in _RESTART_CMDS}
    max_restarts = 5  # per process, then give up

    try:
        while True:
            hb_names = list(_HB_NAMES.values())
            statuses = check_heartbeats(
                hb_names, heartbeat_dir=_HEARTBEAT_DIR, stale_threshold=120.0,
            )
            stale_procs = {s.proc for s in statuses if s.is_stale}

            for name, hb_name in _HB_NAMES.items():
                if hb_name in stale_procs:
                    if restart_counts[name] >= max_restarts:
                        continue  # already gave up on this one
                    restart_counts[name] += 1
                    ts = time.strftime("%H:%M:%S")
                    print(
                        f"  [{ts}] {name} is DOWN — restarting "
                        f"(attempt {restart_counts[name]}/{max_restarts})"
                    )
                    try:
                        if name == "nervous":
                            _kill_port(8000)
                        subprocess.Popen(
                            _RESTART_CMDS[name], start_new_session=True,
                        )
                    except Exception as exc:
                        print(f"  [{ts}] {name} restart FAILED: {exc}")
                else:
                    # Process is alive — reset restart count
                    restart_counts[name] = 0

            # Check execution via socket (no heartbeat)
            sock_path = _DATA_DIR / "exec.sock"
            if not sock_path.exists() and "execution" in _RESTART_CMDS:
                if restart_counts.get("execution", 0) < max_restarts:
                    restart_counts["execution"] = restart_counts.get("execution", 0) + 1
                    ts = time.strftime("%H:%M:%S")
                    print(
                        f"  [{ts}] execution is DOWN — restarting "
                        f"(attempt {restart_counts['execution']}/{max_restarts})"
                    )
                    try:
                        subprocess.Popen(
                            _RESTART_CMDS["execution"], start_new_session=True,
                        )
                    except Exception as exc:
                        print(f"  [{ts}] execution restart FAILED: {exc}")
                elif restart_counts.get("execution", 0) == max_restarts:
                    restart_counts["execution"] = max_restarts + 1  # only warn once
                    ts = time.strftime("%H:%M:%S")
                    print(f"  [{ts}] execution: gave up after {max_restarts} restarts")
            else:
                restart_counts["execution"] = 0

            time.sleep(30)
    except KeyboardInterrupt:
        print("\nSupervisor stopped.")


def main() -> None:
    """PMACS command-line interface."""
    if len(sys.argv) < 2:
        print("Usage: pmacs <command>")
        print("Commands: wizard, setup, init, start, stop, clean, status, watch, version")
        print()
        print("  wizard  — Launch web-based setup wizard (first run)")
        print("  setup   — Interactive one-shot: configure + init + start")
        print("  init    — Initialize databases only")
        print("  start   — Start all processes")
        print("  stop    — Stop all processes")
        print("  clean   — Delete all personal data (use --force to skip prompt)")
        print("  status  — Show process status")
        print("  watch   — Supervise processes (auto-restart on failure)")
        print("  version — Print version")
        sys.exit(1)

    command = sys.argv[1]
    commands = {
        "version": cmd_version,
        "init": cmd_init,
        "start": cmd_start,
        "stop": cmd_stop,
        "clean": cmd_clean,
        "status": cmd_status,
        "setup": cmd_setup,
        "watch": cmd_watch,
        "wizard": cmd_wizard,
    }

    handler = commands.get(command)
    if handler:
        handler()
    else:
        print(f"Unknown command: {command}")
        print("Commands: setup, init, start, stop, clean, status, watch, version")
        sys.exit(1)


if __name__ == "__main__":
    main()
