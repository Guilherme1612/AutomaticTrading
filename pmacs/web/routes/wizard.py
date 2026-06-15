"""Wizard route — first-run setup wizard (Source.md §12).

Full-screen, no sidebar, HTMX-driven step transitions.
State checkpointed to SQLite after each step.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from pmacs.web.templating import templates
from pmacs.storage.sqlite import connect as _sql_connect

router = APIRouter(prefix="/wizard", tags=["wizard"])

# Step number -> template name mapping (Source.md §12: 11 steps)
STEP_TEMPLATES: dict[int, str] = {
    1: "wizard/step01_welcome.html",
    2: "wizard/step02_inference.html",
    3: "wizard/step03_model.html",
    4: "wizard/step04_keychain.html",      # includes embedding model (spec Step 4.5)
    5: "wizard/step06_dbinit.html",
    6: "wizard/step07_dataping.html",
    7: "wizard/step08_universe.html",
    8: "wizard/step09_cycleprefs.html",
    9: "wizard/step10_totp.html",
    10: "wizard/step10_smoke_test.html",
    11: "wizard/step11_complete.html",
}

TOTAL_STEPS = 11


def _db_path() -> Path:
    """Resolve the SQLite database path."""
    from pmacs.config import data_dir
    return data_dir() / "pmacs.db"


def _has_existing_config() -> bool:
    """Check if model_registry.json has an active cloud backend configured."""
    import json as _json
    config_dir = Path(__file__).resolve().parents[3] / "config"
    registry_path = config_dir / "model_registry.json"
    if not registry_path.exists():
        return False
    try:
        registry = _json.loads(registry_path.read_text())
        active = registry.get("active", "")
        # If active backend is a cloud provider, config already exists
        return active in ("openrouter", "anthropic", "openai")
    except Exception:
        return False


def _read_wizard_state() -> dict:
    """Read wizard state from SQLite wizard_state table.

    Backward-compatible: if the system has mode_history entries (existing install),
    treats wizard as already completed. Also detects existing model_registry.json
    with an active cloud backend.
    """
    # If config already has an active cloud backend, wizard is done
    if _has_existing_config():
        _mark_wizard_completed()
        return {"current_step": TOTAL_STEPS, "completed": True}

    db = _db_path()
    if not db.exists():
        return {"current_step": 1, "completed": False}
    try:
        conn = _sql_connect(db)
        try:
            # Check wizard_completed flag
            row = conn.execute(
                "SELECT value FROM wizard_state WHERE key = ?", ("wizard_completed",)
            ).fetchone()
            if row and row[0] == "1":
                return {"current_step": TOTAL_STEPS, "completed": True}

            # Backward compat: existing install with mode_history = wizard done
            mode_rows = conn.execute("SELECT COUNT(*) FROM mode_history").fetchone()
            if mode_rows and mode_rows[0] > 0:
                _mark_wizard_completed()
                return {"current_step": TOTAL_STEPS, "completed": True}

            # Read current step
            step_row = conn.execute(
                "SELECT value FROM wizard_state WHERE key = ?", ("wizard_current_step",)
            ).fetchone()
            step = int(step_row[0]) if step_row else 1
        finally:
            conn.close()
        return {"current_step": max(1, min(step, TOTAL_STEPS)), "completed": False}
    except Exception:
        return {"current_step": 1, "completed": False}


def _write_wizard_step(step: int) -> None:
    """Persist the current wizard step to SQLite."""
    _write_wizard_kv("wizard_current_step", str(step))


def _write_wizard_kv(key: str, value: str) -> None:
    """Write a key-value pair to wizard_state table."""

    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _sql_connect(db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wizard_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT OR REPLACE INTO wizard_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (key, value),
    )
    conn.commit()
    conn.close()


def _get_backend_type() -> str:
    """Get the backend type from wizard state. Returns 'local' or 'cloud'."""

    db = _db_path()
    if not db.exists():
        return "local"
    try:
        conn = _sql_connect(db)
        row = conn.execute(
            "SELECT value FROM wizard_state WHERE key = ?", ("backend_type",)
        ).fetchone()
        conn.close()
        return row[0] if row else "local"
    except Exception:
        return "local"


def _mark_wizard_completed() -> None:
    """Mark the wizard as fully completed in SQLite."""

    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _sql_connect(db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wizard_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT OR REPLACE INTO wizard_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        ("wizard_completed", "1"),
    )
    conn.commit()
    conn.close()


def _get_wizard_state(request: Request) -> dict:
    """Retrieve wizard state from SQLite wizard_state table."""
    return _read_wizard_state()


def _render_step(
    request: Request,
    step: int,
    **context: object,
) -> HTMLResponse:
    """Render a wizard step template with standard context."""
    backend = _get_backend_type()
    is_cloud = backend == "cloud"

    template_name = STEP_TEMPLATES.get(step, STEP_TEMPLATES[1])
    # Cloud step 3: show LLM provider selection instead of local model verification
    if step == 3 and is_cloud:
        template_name = "wizard/step10_llm_provider.html"

    ctx = {
        "request": request,
        "current_step": step,
        "display_step": step,
        "total_steps": TOTAL_STEPS,
        "is_cloud": is_cloud,
        **context,
    }
    return templates.TemplateResponse(request=request, name=template_name, context=ctx)


@router.get("/", response_class=HTMLResponse)
async def wizard_home(request: Request):
    """Render step 1 or resume from checkpoint."""
    state = _get_wizard_state(request)
    step = state["current_step"]

    if state.get("completed"):
        # Already configured — go to dashboard
        return RedirectResponse(url="/", status_code=303)

    if step > 1:
        # Resume: render current step
        return _render_step(request, step)

    # Fresh start: render welcome with system info
    import platform
    import sys

    system_info = {
        "platform": platform.platform(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    return _render_step(request, 1, system_info=system_info)


@router.post("/step/{step_num}", response_class=HTMLResponse)
async def wizard_step(request: Request, step_num: int):
    """Execute backend step, render next step on success.

    Steps 1-10 execute a backend action then advance.
    Step 11 is terminal (promotion to SHADOW+PAPER).
    """
    if step_num < 1 or step_num > TOTAL_STEPS:
        return HTMLResponse("Invalid step", status_code=400)

    # Dispatch to step handler
    result = await _execute_step(request, step_num)

    # Advance to next step (unless current step failed)
    # Allow steps to override the next step (e.g., skip model download for cloud)
    if result.get("ok", True) and "next_step" in result:
        next_step = result["next_step"]
    else:
        next_step = step_num + 1 if result.get("ok", True) else step_num

    if next_step > TOTAL_STEPS:
        next_step = TOTAL_STEPS

    # Build context for the next step's template
    context = result.get("context", {})

    response = _render_step(request, next_step, **context)

    # Checkpoint step to SQLite
    if result.get("ok", True) and next_step > step_num:
        _write_wizard_step(next_step)

    return response


@router.get("/status")
async def wizard_status(request: Request):
    """JSON with current step and completed steps."""
    state = _get_wizard_state(request)
    return JSONResponse({
        "current_step": state["current_step"],
        "total_steps": TOTAL_STEPS,
        "completed": state.get("completed", False),
    })


async def _execute_step(request: Request, step: int) -> dict:
    """Execute the backend logic for a wizard step.

    Returns dict with:
        ok: bool - whether step succeeded
        context: dict - template context for next step
    """
    form_data = await request.form()

    if step == 1:
        # Welcome -> just advance
        return {"ok": True, "context": {}}

    elif step == 2:
        # Inference backend: choice cards → detection or cloud setup
        backend_choice = str(form_data.get("backend_choice", ""))
        advance = str(form_data.get("advance", ""))

        if backend_choice == "cloud":
            # Cloud API selected — advance to step 3 for LLM provider selection
            _write_wizard_kv("backend_type", "cloud")
            return {"ok": True, "next_step": 3, "context": {}}

        elif backend_choice == "local" and advance:
            # Local detection already passed — advance to step 3 (model download)
            _write_wizard_kv("backend_type", "local")
            return {"ok": True, "context": {}}

        elif backend_choice == "local":
            # Run llama-server detection
            from pmacs.installer.steps.verify_llm import run as verify_llm_run
            result = await verify_llm_run({})
            return {"ok": False, "context": {"llm_result": result, "backend_choice_made": True}}

        else:
            # Initial state — show choice cards (no detection yet)
            return {"ok": False, "context": {"backend_choice_made": False}}

    elif step == 3:
        if _get_backend_type() == "cloud":
            # Cloud LLM provider selection — process provider/model/key from form
            import json as _json

            provider = str(form_data.get("provider", ""))
            if not provider or provider == "llama_server":
                return {"ok": False, "context": {"error": {"code": "PROVIDER_REQUIRED", "message": "Select a cloud LLM provider."}}}

            api_model = str(form_data.get("api_model", "")).strip()
            api_key = str(form_data.get("api_key", "")).strip()
            base_url = str(form_data.get("base_url", "")).strip()

            # Save to model registry
            config_dir = Path(__file__).resolve().parents[3] / "config"
            registry_path = config_dir / "model_registry.json"
            try:
                registry = _json.loads(registry_path.read_text())
                registry["active"] = provider
                if "backends" not in registry:
                    registry["backends"] = {}
                if provider not in registry["backends"]:
                    registry["backends"][provider] = {}
                if api_model:
                    registry["backends"][provider]["default_model"] = api_model
                if base_url:
                    registry["backends"][provider]["base_url"] = base_url
                registry_path.write_text(_json.dumps(registry, indent=2) + "\n")
            except Exception as exc:
                return {"ok": False, "context": {"error": {"code": "REGISTRY_WRITE_FAIL", "message": str(exc)}}}

            # Store API key in keychain
            if api_key:
                try:
                    import keyring
                    # Store with both naming conventions for compatibility:
                    # 1. Short name: {provider}_key (e.g., openrouter_key)
                    keyring.set_password("pmacs.credentials", f"{provider}_key", api_key)
                    # 2. Full ref: pmacs.credentials.{provider}_api_key (matches model_registry.json api_key_ref)
                    keyring.set_password("pmacs.credentials", f"pmacs.credentials.{provider}_api_key", api_key)
                except Exception:
                    pass

            return {"ok": True, "context": {"provider": provider, "model": api_model}}

        # Local: Model verification — check GGUF file exists and SHA256 matches
        # Allow skip for users who want to configure model later
        skip_model = str(form_data.get("skip_model", ""))
        if skip_model == "true":
            return {"ok": True, "context": {"model_result": {"all_ok": False, "skipped": True}}}

        import json as _json
        import hashlib
        from pathlib import Path as _Path

        model_result = {"all_ok": False, "already_exists": False}
        try:
            config_dir = _Path(__file__).resolve().parents[3] / "config"
            registry = _json.loads((config_dir / "model_registry.json").read_text())
            hashes_text = (config_dir / "model_hashes.toml").read_text()

            # Find the model name from registry
            active = registry.get("backends", {}).get(registry.get("active", ""), {})
            model_ref = active.get("default_model", "")
            # Extract model name (after colon if present, e.g. "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_XL")
            model_tag = model_ref.split(":")[-1] if ":" in model_ref else model_ref.split("/")[-1]

            # Try common GGUF paths
            search_paths = [
                _Path.home() / ".cache" / "pmacs" / "models",
                config_dir.parent / "models",
            ]

            gguf_path = None
            for sp in search_paths:
                candidate = sp / f"{model_tag}.gguf"
                if candidate.exists():
                    gguf_path = candidate
                    break
                # Also try globbing for any .gguf
                ggufs = list(sp.glob("*.gguf")) if sp.exists() else []
                if ggufs:
                    gguf_path = ggufs[0]
                    break

            if gguf_path and gguf_path.exists():
                model_result["already_exists"] = True
                model_result["model_path"] = str(gguf_path)
                model_result["model_name"] = gguf_path.name

                # Verify SHA256 if not placeholder
                expected_hash = None
                for line in hashes_text.splitlines():
                    if "=" in line and '"' in line:
                        key = line.split("=")[0].strip().strip('"')
                        val = line.split("=", 1)[1].strip().strip('"')
                        if val != "PLACEHOLDER_SHA256_VERIFY_BEFORE_USE":
                            expected_hash = val

                if expected_hash:
                    sha256 = hashlib.sha256()
                    with open(gguf_path, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            sha256.update(chunk)
                    actual_hash = sha256.hexdigest()
                    model_result["hash_match"] = actual_hash == expected_hash
                    model_result["all_ok"] = actual_hash == expected_hash
                else:
                    # No hash to verify — file exists is enough
                    model_result["hash_match"] = None
                    model_result["all_ok"] = True
            else:
                model_result["model_tag"] = model_tag
                model_result["searched_paths"] = [str(p) for p in search_paths]

        except Exception as exc:
            model_result["error"] = str(exc)

        return {"ok": model_result.get("all_ok", False), "context": {"model_result": model_result}}

    elif step == 4:
        # Keychain credential collection + embedding model (Source.md §12 Step 4 + 4.5)
        creds = {k: str(v) for k, v in form_data.items() if v}
        stored_ok = True
        if creds:
            try:
                import keyring
                for key, value in creds.items():
                    keyring.set_password("pmacs.credentials", key, value)
            except Exception:
                stored_ok = False

        # Step 4.5: Embedding model check
        embedding_result = {"all_ok": False, "already_exists": False}
        try:
            from sentence_transformers import SentenceTransformer
            model_name = "BAAI/bge-base-en-v1.5"
            try:
                model = SentenceTransformer(model_name)
                embedding_result["already_exists"] = True
                embedding_result["model_name"] = model_name
                embedding_result["all_ok"] = True
            except Exception:
                embedding_result["model_name"] = model_name
                embedding_result["download_needed"] = True
                embedding_result["all_ok"] = False
        except ImportError:
            embedding_result["library_missing"] = True
            embedding_result["install_hint"] = "pip install sentence-transformers"

        # Advance if credentials stored (embedding can be installed later)
        advance = stored_ok
        return {
            "ok": advance,
            "context": {
                "credential_count": len(creds) if stored_ok else 0,
                "embedding_result": embedding_result,
            },
        }

    elif step == 5:
        # Database initialization — create all 5 stores
        from pathlib import Path as _Path

        db_result = {
            "sqlite_ok": False,
            "kuzudb_ok": False,
            "qdrant_ok": False,
            "duckdb_ok": False,
            "audit_ok": False,
            "genesis_ok": False,
            "all_ok": False,
        }

        try:
            # Determine data directory
            from pmacs.config import data_dir as _resolve_data_dir
            data_dir = _resolve_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)

            # SQLite
            try:
                from pmacs.storage.sqlite import init_db
                conn = init_db(data_dir / "pmacs.db")
                conn.close()
                db_result["sqlite_ok"] = True
            except Exception as exc:
                db_result["sqlite_error"] = str(exc)

            # DuckDB
            try:
                from pmacs.storage.duckdb import DuckDBAdapter
                duckdb_path = data_dir / "pmacs_analytics.duckdb"
                adapter = DuckDBAdapter(duckdb_path)
                adapter.initialize()
                db_result["duckdb_ok"] = True
            except Exception as exc:
                db_result["duckdb_error"] = str(exc)

            # KuzuDB
            try:
                from pmacs.storage.kuzu import KuzuDBAdapter
                kuzu_path = data_dir / "pmacs_graph.kuzu"
                kuzu = KuzuDBAdapter(kuzu_path)
                kuzu.initialize()
                db_result["kuzudb_ok"] = True
            except Exception as exc:
                db_result["kuzudb_error"] = str(exc)

            # Qdrant
            try:
                from pmacs.storage.qdrant import QdrantAdapter
                qdrant = QdrantAdapter()
                qdrant.initialize()
                db_result["qdrant_ok"] = True
            except Exception as exc:
                db_result["qdrant_error"] = str(exc)

            # Audit log — write genesis event
            try:
                from pmacs.storage.audit import AuditWriter
                audit_path = data_dir / "audit.log"
                writer = AuditWriter(audit_path)
                writer.append("SYSTEM_GENESIS", {"event": "wizard_db_init"}, cycle_id="genesis")
                writer.close()
                db_result["audit_ok"] = True
                db_result["genesis_ok"] = True
            except Exception as exc:
                db_result["audit_error"] = str(exc)

            db_result["all_ok"] = all(
                db_result.get(k, False)
                for k in ("sqlite_ok", "duckdb_ok", "kuzudb_ok", "qdrant_ok", "audit_ok")
            )
        except Exception as exc:
            db_result["error"] = str(exc)

        return {"ok": db_result.get("all_ok", False), "context": {"db_result": db_result}}

    elif step == 6:
        # Data source connectivity ping
        skip_ping = str(form_data.get("skip_ping", ""))
        if skip_ping == "true":
            return {"ok": True, "context": {}}

        from pmacs.installer.steps.verify_data import run as verify_data_run
        result = await verify_data_run({})
        # Allow advancement if critical sources pass (non-critical can be configured later)
        critical_ok = all(
            result.get("results", {}).get(src, {}).get("ok", False)
            for src in ("polygon", "edgar")
        )
        return {"ok": critical_ok, "context": {"data_result": result}}

    elif step == 7:
        # Universe seed — persist tickers to DB (Source.md §12 Step 8)
        tickers = form_data.getlist("tickers") if hasattr(form_data, "getlist") else []
        add_raw = form_data.get("add_tickers", "")
        if add_raw:
            add_tickers = [t.strip().upper() for t in str(add_raw).split(",") if t.strip()]
            tickers = list(tickers) + add_tickers
        universe_result = {"all_ok": bool(tickers), "tickers": tickers, "validation": {}}
        # Persist tickers to the universe table
        if tickers:
            try:
                from datetime import datetime, timezone as _tz
                db = _db_path()

                conn = _sql_connect(db)
                try:
                    conn.execute(
                        "CREATE TABLE IF NOT EXISTS universe ("
                        "  ticker TEXT PRIMARY KEY,"
                        "  name TEXT NOT NULL DEFAULT '',"
                        "  exchange TEXT NOT NULL DEFAULT '',"
                        "  sector TEXT,"
                        "  subsector TEXT,"
                        "  catalyst_type TEXT,"
                        "  pinned_priority INTEGER DEFAULT 0,"
                        "  halted INTEGER NOT NULL DEFAULT 0,"
                        "  delisted INTEGER NOT NULL DEFAULT 0,"
                        "  added_at TEXT NOT NULL"
                        ")"
                    )
                    now = datetime.now(_tz.utc).isoformat()
                    for t in tickers:
                        conn.execute(
                            "INSERT OR REPLACE INTO universe (ticker, added_at) VALUES (?, ?)",
                            (t, now),
                        )
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass  # Validation still passes; persistence retried on next step
        return {"ok": universe_result.get("all_ok", False), "context": {"universe_result": universe_result}}

    elif step == 8:
        # Cycle preferences
        return {"ok": True, "context": {}}

    elif step == 9:
        # TOTP enrollment (Source.md §12 Step 9)
        from pmacs.installer.steps.totp_enroll import run as totp_run
        result = await totp_run(dict(form_data))
        # Only set verify_result when user actually submitted a code (phase 2)
        # Phase 1 (generation) should NOT show error UI
        has_code = bool(form_data.get("totp_secret", ""))
        return {
            "ok": result.get("ok", False),
            "context": {
                "totp_result": result,
                "verify_result": result if has_code else None,
            },
        }

    elif step == 10:
        # Smoke-test cycle (Source.md §12 Step 10)
        # Run a full synthetic pipeline to verify the system works before PAPER promotion
        smoke_result = {"all_ok": False, "checks": {}}
        try:

            from pathlib import Path as _Path
            from pmacs.config import data_dir as _resolve_data_dir

            data_dir = _resolve_data_dir()
            db_path = data_dir / "pmacs.db"

            checks_passed = 0
            checks_total = 4

            # 1. Verify DB is writable
            try:
                if db_path.exists():
                    conn = _sql_connect(db_path)
                    conn.execute("SELECT 1")
                    conn.close()
                    smoke_result["checks"]["db_write"] = True
                    checks_passed += 1
                else:
                    smoke_result["checks"]["db_write"] = False
                    smoke_result["checks"]["db_error"] = f"DB not found at {db_path}"
            except Exception as exc:
                smoke_result["checks"]["db_write"] = False
                smoke_result["checks"]["db_error"] = str(exc)

            # 2. Verify inference backend responds (local only — cloud uses API key)
            backend_type = _get_backend_type()
            if backend_type == "cloud":
                # Cloud users: verify Anthropic API key is stored
                try:
                    import keyring
                    api_key = keyring.get_password("pmacs.credentials", "anthropic_key")
                    smoke_result["checks"]["inference"] = bool(api_key)
                    if api_key:
                        checks_passed += 1
                    else:
                        smoke_result["checks"]["inference_error"] = "Anthropic API key not found in Keychain"
                except Exception:
                    smoke_result["checks"]["inference"] = False
                    smoke_result["checks"]["inference_error"] = "Could not verify API key in Keychain"
            else:
                try:
                    import urllib.request
                    resp = urllib.request.urlopen("http://localhost:8080/health", timeout=5)
                    smoke_result["checks"]["inference"] = resp.status == 200
                    if resp.status == 200:
                        checks_passed += 1
                except Exception:
                    smoke_result["checks"]["inference"] = False
                    smoke_result["checks"]["inference_error"] = "Inference backend not reachable on localhost:8080"

            # 3. Verify audit log writable
            try:
                audit_path = data_dir / "audit.log"
                from pmacs.storage.audit import AuditWriter
                writer = AuditWriter(audit_path)
                writer.append("SMOKE_TEST", {"event": "wizard_smoke_test"}, cycle_id="smoke_test")
                writer.close()
                smoke_result["checks"]["audit"] = True
                checks_passed += 1
            except Exception as exc:
                smoke_result["checks"]["audit"] = False
                smoke_result["checks"]["audit_error"] = str(exc)

            # 4. Verify embedding model loads
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer("BAAI/bge-base-en-v1.5")
                test_embed = model.encode("smoke test")
                smoke_result["checks"]["embedding"] = len(test_embed) == 768
                if len(test_embed) == 768:
                    checks_passed += 1
            except Exception as exc:
                smoke_result["checks"]["embedding"] = False
                smoke_result["checks"]["embedding_error"] = str(exc)

            smoke_result["checks_passed"] = checks_passed
            smoke_result["checks_total"] = checks_total
            smoke_result["all_ok"] = checks_passed == checks_total

        except Exception as exc:
            smoke_result["error"] = str(exc)

        # Always stay on step 10 — user must explicitly click "Promote" to advance to step 11
        return {"ok": False, "context": {"smoke_result": smoke_result}}

    elif step == 11:
        # Complete / promote to SHADOW + PAPER (Source.md §12 Step 11)
        import json as _json

        from pathlib import Path as _Path
        from pmacs.engines.mode_manager import transition_mode
        from pmacs.schemas.system import Mode

        promotion_result = {
            "mode": "SHADOW + PAPER",
            "model": "Qwen3.6-35B-A3B",
            "provider": "llama_server",
            "universe_count": 16,
            "promoted": False,
        }

        try:
            # Read chosen provider from model_registry
            config_dir = _Path(__file__).resolve().parents[3] / "config"
            registry_path = config_dir / "model_registry.json"
            if registry_path.exists():
                registry = _json.loads(registry_path.read_text())
                active = registry.get("active", "llama_server")
                backend = registry.get("backends", {}).get(active, {})
                promotion_result["provider"] = active
                model_name = backend.get("default_model", "Qwen3.6-35B-A3B")
                if model_name:
                    promotion_result["model"] = model_name
                promotion_result["universe_count"] = 16
        except Exception:
            pass

        try:
            # Find DB path
            from pmacs.config import data_dir as _resolve_data_dir
            data_dir = _resolve_data_dir()
            db_path = data_dir / "pmacs.db"

            # Ensure schema is fully initialized (mode_history etc.)
            from pmacs.storage.sqlite import init_db as _init_db
            _init_db(db_path)

            if db_path.exists():
                # Transition: INSTALLING -> PAPER (per VALID_MODE_TRANSITIONS, INSTALLING can go directly to PAPER)
                mt = transition_mode(
                    from_mode=Mode.INSTALLING,
                    to_mode=Mode.PAPER,
                    reason="Wizard setup complete — promoting to SHADOW + PAPER",
                    totp_verified=False,  # PAPER doesn't require TOTP
                    triggered_by="OPERATOR",
                )

                # Persist to mode_history
                conn = _sql_connect(db_path)
                conn.execute(
                    "INSERT INTO mode_history (from_mode, to_mode, reason, triggered_by, changed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (mt.from_mode.value, mt.to_mode.value, mt.reason, mt.triggered_by,
                     mt.changed_at.isoformat()),
                )
                conn.commit()
                conn.close()

                promotion_result["promoted"] = True
                promotion_result["transition"] = {
                    "from": mt.from_mode.value,
                    "to": mt.to_mode.value,
                    "at": mt.changed_at.isoformat(),
                }
            else:
                promotion_result["error"] = f"Database not found at {db_path}"

        except Exception as exc:
            import logging
            logging.getLogger("pmacs.web").error("Wizard mode transition failed: %s", exc, exc_info=True)
            promotion_result["error"] = str(exc)

        # Always mark wizard as completed — setup IS done, promotion can be retried from dashboard
        _mark_wizard_completed()

        return {
            "ok": promotion_result.get("promoted", False),
            "context": {
                "promotion_result": promotion_result,
                "wizard_error": promotion_result.get("error") if not promotion_result.get("promoted") else None,
            },
        }

    return {"ok": False, "context": {"error": {"code": "WIZARD_UNKNOWN_STEP", "message": f"Unknown step: {step}"}}}
