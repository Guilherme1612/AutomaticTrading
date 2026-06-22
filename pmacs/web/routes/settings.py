"""Settings route — configuration management page."""

import difflib
import fcntl
import json as _json
from pathlib import Path as _Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()

# ---------------------------------------------------------------------------
# Inference config helpers
# ---------------------------------------------------------------------------

_REGISTRY_PATH = _Path(__file__).resolve().parents[3] / "config" / "model_registry.json"


def _load_registry() -> dict:
    if _REGISTRY_PATH.exists():
        return _json.loads(_REGISTRY_PATH.read_text())
    return {"backends": {}, "active": "llama_server"}


def _save_registry(registry: dict) -> None:
    """Atomic write with file locking to prevent concurrent clobber."""
    tmp_path = _REGISTRY_PATH.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            _json.dump(registry, f, indent=2)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    tmp_path.replace(_REGISTRY_PATH)


def _get_inference_state() -> dict:
    registry = _load_registry()
    active = registry.get("active", "llama_server")
    backends = registry.get("backends", {})
    backend = backends.get(active, {})
    api_key_ref = backend.get("api_key_ref", "")
    has_api_key = False
    if api_key_ref:
        try:
            import keyring
            has_api_key = bool(keyring.get_password("pmacs.credentials", api_key_ref))
        except Exception:
            pass

    local_providers = []
    cloud_providers = []
    for k, v in backends.items():
        prov_key_ref = v.get("api_key_ref", "")
        prov_has_key = False
        if prov_key_ref:
            try:
                import keyring
                prov_has_key = bool(keyring.get_password("pmacs.credentials", prov_key_ref))
            except Exception:
                pass
        entry = {
            "id": k,
            "model": v.get("default_model", ""),
            "structured_output": v.get("structured_output", ""),
            "base_url": v.get("base_url", ""),
            "needs_key": bool(prov_key_ref),
            "has_api_key": prov_has_key,
            "is_local": not bool(prov_key_ref),
        }
        if entry["is_local"]:
            local_providers.append(entry)
        else:
            cloud_providers.append(entry)

    # Determine if active provider is local or cloud
    active_is_local = not bool(api_key_ref)

    return {
        "active": active,
        "model": backend.get("default_model", ""),
        "structured_output": backend.get("structured_output", ""),
        "base_url": backend.get("base_url", ""),
        "api_key_ref": api_key_ref,
        "has_api_key": has_api_key,
        "providers": local_providers + cloud_providers,
        "local_providers": local_providers,
        "cloud_providers": cloud_providers,
        "active_is_local": active_is_local,
        "mode": "local" if active_is_local else "cloud",
    }


class NotificationLevelRequest(BaseModel):
    event: str
    level: str


class MutationActionRequest(BaseModel):
    candidate_id: str


class CostCapsRequest(BaseModel):
    daily_cap: float
    monthly_cap: float


class RiskConfigRequest(BaseModel):
    """§20.6 Risk thresholds — operator-confirmed (writes risk.toml)."""
    max_single_position_pct: float | None = None
    max_concurrent_positions: int | None = None
    daily_loss_pct: float | None = None
    rolling_5d_loss_pct: float | None = None
    reconciliation_tolerance_usd: float | None = None


class CrucibleConfigRequest(BaseModel):
    """§20.7 Crucible time budget — operator-confirmed (writes crucible.toml)."""
    seconds_per_attack: int | None = None
    max_cycles: int | None = None


class BrokersConfigRequest(BaseModel):
    """§20.3 Brokers — catastrophe-net stop % (writes risk.toml [pricing])."""
    catastrophe_net_stop_pct: float | None = None


class OperatorConfigRequest(BaseModel):
    """§20.12 Operator — per-trade approval toggle (writes risk.toml [operator])."""
    per_trade_approval: bool | None = None


@router.get("/settings")
async def settings_page(request: Request):
    """Render the settings configuration page."""
    cfg = get_config()

    try:
        config = data_layer.get_settings(cfg.config_dir)

        # Get mutation candidates and recent promotions
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            mutation_candidates = data_layer.get_mutation_candidates(db)
            recent_mutations = data_layer.get_recent_mutations(db)
            pricing_table = _get_pricing_table(db)
        finally:
            db.close()

        # Load saved notification levels
        notification_levels = data_layer.get_notification_levels(cfg.sqlite_path)

        # Load inference provider state
        inference = _get_inference_state()

        # §20 Settings expansion: risk / crucible / brokers / operator + persona display
        risk_cfg = _risk_section(cfg)
        crucible_cfg = _crucible_section(cfg)
        brokers = {"catastrophe_net_stop_pct": risk_cfg["catastrophe_net_stop_pct"]}
        operator_cfg = {"per_trade_approval": risk_cfg["per_trade_approval"]}
        personas = _persona_display(cfg)

        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "page": "settings",
                "mode": "SHADOW + PAPER",
                "config": config,
                "mutation_candidates": mutation_candidates,
                "recent_mutations": recent_mutations,
                "notification_levels": notification_levels,
                "inference": inference,
                "cost_state": _get_cost_state(cfg),
                "persona_costs": _get_persona_costs(cfg.duckdb_path),
                "pricing_table": pricing_table,
                "reconciliation": _get_reconciliation_status(cfg.duckdb_path),
                "risk_cfg": risk_cfg,
                "crucible_cfg": crucible_cfg,
                "brokers": brokers,
                "operator_cfg": operator_cfg,
                "personas": personas,
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "page": "settings",
                "mode": "SHADOW + PAPER",
                "error": data_layer.build_error_context("settings", exc),
            },
        )


@router.post("/api/settings/notifications")
async def save_notification_level(req: NotificationLevelRequest):
    """Save a notification level preference for a specific event.

    Accepts JSON {event: string, level: string}.
    Writes to SQLite settings table with key 'notif.{event}'.
    Kill switch and audit chain failure levels are non-disableable.
    """
    # Enforce non-disableable events
    if req.event in ("kill_switch_engaged", "audit_chain_failure"):
        return JSONResponse(
            {"ok": False, "error": f"'{req.event}' notification level cannot be changed"},
            status_code=403,
        )

    valid_levels = {"toast", "toast+sound", "modal", "none"}
    if req.level not in valid_levels:
        return JSONResponse(
            {"ok": False, "error": f"Invalid level. Must be one of: {', '.join(sorted(valid_levels))}"},
            status_code=400,
        )

    cfg = get_config()
    ok = data_layer.save_notification_level(cfg.sqlite_path, req.event, req.level)
    if ok:
        return JSONResponse({"ok": True, "event": req.event, "level": req.level})
    return JSONResponse({"ok": False, "error": "Failed to save"}, status_code=500)


@router.get("/api/settings/notifications")
async def get_notification_levels():
    """Return all saved notification levels as a JSON dict.

    Returns: {event_name: level_string, ...}
    """
    cfg = get_config()
    levels = data_layer.get_notification_levels(cfg.sqlite_path)
    return JSONResponse(levels)


# ---------------------------------------------------------------------------
# Inference API endpoints — switch LLM provider from Settings
# ---------------------------------------------------------------------------


class InferenceProviderRequest(BaseModel):
    provider: str


class InferenceApiKeyRequest(BaseModel):
    provider: str
    api_key: str


class InferenceModelRequest(BaseModel):
    provider: str
    model: str


@router.get("/api/settings/inference")
async def get_inference_config():
    """Return current inference provider configuration."""
    return JSONResponse(_get_inference_state())


@router.post("/api/settings/inference/provider")
async def set_inference_provider(req: InferenceProviderRequest):
    """Switch the active LLM provider in model_registry.json."""
    registry = _load_registry()
    backends = registry.get("backends", {})

    if req.provider not in backends:
        return JSONResponse(
            {"ok": False, "error": f"Unknown provider: {req.provider}"},
            status_code=400,
        )

    registry["active"] = req.provider
    _save_registry(registry)
    return JSONResponse({"ok": True, "active": req.provider})


@router.post("/api/settings/inference/api-key")
async def set_inference_api_key(req: InferenceApiKeyRequest):
    """Save an API key for a cloud provider to the system keychain."""
    registry = _load_registry()
    backend = registry.get("backends", {}).get(req.provider, {})
    api_key_ref = backend.get("api_key_ref", "")

    if not api_key_ref:
        return JSONResponse(
            {"ok": False, "error": f"Provider '{req.provider}' does not use API keys"},
            status_code=400,
        )

    if not req.api_key.strip():
        return JSONResponse(
            {"ok": False, "error": "API key cannot be empty"},
            status_code=400,
        )

    try:
        import keyring
        keyring.set_password("pmacs.credentials", api_key_ref, req.api_key.strip())
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "Failed to save API key to keychain"},
            status_code=500,
        )

    return JSONResponse({"ok": True, "provider": req.provider})


@router.post("/api/settings/inference/model")
async def set_inference_model(req: InferenceModelRequest):
    """Update the default model for a provider."""
    registry = _load_registry()
    backends = registry.get("backends", {})

    if req.provider not in backends:
        return JSONResponse(
            {"ok": False, "error": f"Unknown provider: {req.provider}"},
            status_code=400,
        )

    backends[req.provider]["default_model"] = req.model.strip()
    _save_registry(registry)
    return JSONResponse({"ok": True, "provider": req.provider, "model": req.model})


@router.post("/api/settings/inference/test")
async def test_inference_connection():
    """Test the active LLM provider with a minimal prompt."""
    state = _get_inference_state()
    active = state["active"]

    import httpx
    backend = _load_registry()["backends"].get(active, {})
    api_key_ref = backend.get("api_key_ref", "")
    is_local = not bool(api_key_ref)

    if active == "llama_server":
        url = "http://127.0.0.1:8080/health"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return JSONResponse({"ok": True, "message": "llama-server healthy"})
                return JSONResponse({"ok": False, "error": f"HTTP {resp.status_code}"}, status_code=502)
        except Exception as exc:
            import logging
            logging.getLogger("pmacs.web").error("Inference test failed: %s", exc, exc_info=True)
            return JSONResponse({"ok": False, "error": "Connection test failed"}, status_code=502)

    if active == "ollama":
        # Ollama is local — test via its /api/tags endpoint
        url = backend.get("url", "http://127.0.0.1:11434")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url}/api/tags")
                if resp.status_code == 200:
                    return JSONResponse({"ok": True, "message": "Ollama connected"})
                return JSONResponse({"ok": False, "error": f"HTTP {resp.status_code}"}, status_code=502)
        except Exception as exc:
            import logging
            logging.getLogger("pmacs.web").error("Inference test failed: %s", exc, exc_info=True)
            return JSONResponse({"ok": False, "error": "Ollama not reachable — is it running?"}, status_code=502)

    # Cloud provider test
    base_url = backend.get("base_url", "").rstrip("/")
    model = backend.get("default_model", "gpt-4o")

    api_key = ""
    if api_key_ref:
        try:
            import keyring
            api_key = keyring.get_password("pmacs.credentials", api_key_ref) or ""
        except Exception:
            pass

    if not api_key:
        return JSONResponse({"ok": False, "error": "API key not found in keychain"}, status_code=403)

    structured_output = backend.get("structured_output", "")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if structured_output == "json_schema":
                # OpenAI-compatible (OpenRouter, OpenAI)
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Respond with OK"}],
                        "max_tokens": 5,
                        "temperature": 0.1,
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
            elif structured_output == "tool_use":
                # Anthropic
                resp = await client.post(
                    f"{base_url}/v1/messages",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Respond with OK"}],
                        "max_tokens": 5,
                    },
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                )
            else:
                return JSONResponse({"ok": False, "error": f"Unknown output type: {structured_output}"}, status_code=400)

            if resp.status_code == 200:
                return JSONResponse({"ok": True, "message": f"{active} connection successful"})
            return JSONResponse(
                {"ok": False, "error": f"Provider returned HTTP {resp.status_code}"},
                status_code=502,
            )
    except Exception as exc:
        # Sanitize error message to prevent API key leakage (Architecture.md §16)
        err_msg = str(exc)
        for secret_keyword in ("key", "token", "bearer", "authorization", "api_key", "apikey"):
            if secret_keyword.lower() in err_msg.lower():
                err_msg = "Connection failed — check provider settings and API key"
                break
        return JSONResponse({"ok": False, "error": err_msg}, status_code=502)


# ---------------------------------------------------------------------------
# Mutation API endpoints (Source.md §6 — operator-confirmed promote/reject)
# ---------------------------------------------------------------------------


@router.post("/api/mutation/promote")
async def mutation_promote(req: MutationActionRequest):
    """Promote a mutation candidate to production.

    Requires an explicit operator action (single-operator, loopback-only; no
    second-factor gate). Updates candidate status to 'approved' and records the
    promotion.
    """
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute(
            "UPDATE mutation_proposals SET status = 'approved' "
            "WHERE id = ? AND status = 'PROPOSED'",
            (req.candidate_id,),
        )
        db.commit()
        if cursor.rowcount == 0:
            return JSONResponse(
                {"ok": False, "error": "Candidate not found or not pending"},
                status_code=404,
            )
        # Log the promotion
        db.execute(
            "INSERT INTO mutation_log (candidate_id, dimension, target, promoted_at, status) "
            "SELECT id, dimension, target, datetime('now'), 'promoted' "
            "FROM mutation_proposals WHERE id = ?",
            (req.candidate_id,),
        )
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True, "candidate_id": req.candidate_id, "action": "promoted"})


@router.post("/api/mutation/reject")
async def mutation_reject(req: MutationActionRequest):
    """Reject a mutation candidate. Requires an explicit operator action."""
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute(
            "UPDATE mutation_proposals SET status = 'rejected' "
            "WHERE id = ? AND status IN ('PROPOSED', 'approved')",
            (req.candidate_id,),
        )
        db.commit()
        if cursor.rowcount == 0:
            return JSONResponse(
                {"ok": False, "error": "Candidate not found"},
                status_code=404,
            )
    finally:
        db.close()
    return JSONResponse({"ok": True, "candidate_id": req.candidate_id, "action": "rejected"})


@router.post("/api/mutation/rollback")
async def mutation_rollback(req: MutationActionRequest):
    """Rollback a promoted mutation.

    Reverts the candidate to 'rolled_back' status and records in mutation_log.
    Requires an explicit operator action.
    """
    cfg = get_config()
    db = data_layer.get_readwrite_db(cfg.sqlite_path)
    try:
        cursor = db.execute(
            "UPDATE mutation_proposals SET status = 'rolled_back' "
            "WHERE id = ? AND status = 'approved'",
            (req.candidate_id,),
        )
        db.commit()
        if cursor.rowcount == 0:
            return JSONResponse(
                {"ok": False, "error": "Candidate not found or not approved"},
                status_code=404,
            )
        db.execute(
            "UPDATE mutation_log SET rolled_back_at = datetime('now'), status = 'rolled_back' "
            "WHERE candidate_id = ? AND status = 'promoted'",
            (req.candidate_id,),
        )
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True, "candidate_id": req.candidate_id, "action": "rolled_back"})


@router.get("/api/mutation/{candidate_id}/diff")
async def mutation_diff(candidate_id: str):
    """Generate side-by-side HTML diff for a mutation candidate (Source.md §13.3).

    Returns JSON with diff_rows, unified, baseline text, and candidate text.
    """
    cfg = get_config()
    db = data_layer.get_readonly_db(cfg.sqlite_path)
    try:
        row = db.execute(
            "SELECT baseline_value, candidate_value FROM mutation_proposals "
            "WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    finally:
        db.close()

    if not row:
        return JSONResponse(
            {"ok": False, "error": "Candidate not found"},
            status_code=404,
        )

    baseline = row[0] or ""
    candidate = row[1] or ""

    # Generate unified diff (safe — no HTML injection risk)
    baseline_lines = baseline.splitlines(keepends=True)
    candidate_lines = candidate.splitlines(keepends=True)

    unified = difflib.unified_diff(
        baseline_lines, candidate_lines,
        fromfile="baseline", tofile="candidate", lineterm="",
    )
    unified_text = "\n".join(unified)

    # Build a safe side-by-side representation (escaped HTML table)
    import html as html_module
    b_display = [html_module.escape(l.rstrip("\n\r")) for l in baseline_lines]
    c_display = [html_module.escape(l.rstrip("\n\r")) for l in candidate_lines]

    # Pair lines for side-by-side display
    max_len = max(len(b_display), len(c_display))
    b_padded = b_display + [""] * (max_len - len(b_display))
    c_padded = c_display + [""] * (max_len - len(c_display))

    diff_rows = []
    for b_line, c_line in zip(b_padded, c_padded):
        css_class = "diff-eq"
        if b_line != c_line:
            css_class = "diff-changed" if b_line and c_line else ("diff-del" if b_line else "diff-add")
        diff_rows.append({"baseline": b_line, "candidate": c_line, "class": css_class})

    return JSONResponse({
        "ok": True,
        "diff_rows": diff_rows,
        "unified": unified_text,
        "baseline": baseline,
        "candidate": candidate,
    })


# ---------------------------------------------------------------------------
# Cost & Budget API endpoints (PRD Phase 16)
# ---------------------------------------------------------------------------


def _get_cost_state(cfg) -> dict:
    """Get current budget period state for the cost widget and settings.

    Delegates to the shared ``data_layer.get_cost_state`` (DuckDB-backed).
    """
    return data_layer.get_cost_state(cfg)


def _get_persona_costs(duckdb_path: str) -> list[dict]:
    """Get per-persona cost breakdown from DuckDB."""
    try:
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter(db_path=_Path(duckdb_path))
        try:
            rows = adapter.execute(
                """SELECT persona,
                          COUNT(*) as call_count,
                          SUM(prompt_tokens) as prompt_tokens,
                          SUM(completion_tokens) as completion_tokens,
                          SUM(body_cost_usd) as cost_usd
                   FROM api_usage
                   WHERE called_at >= datetime('now', '-30 days')
                   GROUP BY persona
                   ORDER BY cost_usd DESC"""
            )
            return [
                {
                    "persona": r["persona"],
                    "call_count": int(r["call_count"] or 0),
                    "prompt_tokens": int(r["prompt_tokens"] or 0),
                    "completion_tokens": int(r["completion_tokens"] or 0),
                    "cost_usd": float(r["cost_usd"] or 0.0),
                }
                for r in rows
            ]
        finally:
            adapter.close()
    except Exception:
        return []


def _get_pricing_table(db) -> list[dict]:
    """Get cached pricing table from SQLite."""
    try:
        rows = db.execute(
            "SELECT model_id, input_price_per_token, output_price_per_token, "
            "cached_input_price_per_token, fetched_at FROM pricing_table "
            "ORDER BY model_id"
        ).fetchall()
        return [
            {
                "model_id": r[0],
                "input_price_per_token": float(r[1] or 0),
                "output_price_per_token": float(r[2] or 0),
                "cached_input_price_per_token": float(r[3]) if r[3] is not None else None,
                "fetched_at": r[4] or "",
            }
            for r in rows
        ]
    except Exception:
        return []


def _get_reconciliation_status(duckdb_path: str) -> dict:
    """Get reconciliation status from DuckDB api_usage table.

    Compares body_cost_usd vs actual_cost_usd for reconciled rows.
    """
    try:
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter(db_path=_Path(duckdb_path))
        try:
            rows = adapter.execute(
                "SELECT COUNT(*) as cnt, "
                "COALESCE(MAX(called_at), '--') as last_at, "
                "COALESCE(SUM(actual_cost_usd - body_cost_usd), 0) as delta "
                "FROM api_usage WHERE actual_cost_usd IS NOT NULL"
            )
            row = rows[0] if rows else None
            count = int(row["cnt"]) if row else 0
            last_at = str(row["last_at"]) if row else "--"
            delta = float(row["delta"]) if row and row["delta"] else 0.0

            # Determine staleness
            import datetime
            status = "no_data"
            status_label = "No Data"
            if count > 0 and last_at != "--":
                try:
                    last_dt = datetime.datetime.fromisoformat(last_at)
                    age = datetime.datetime.now(datetime.timezone.utc) - last_dt
                    if age.days < 1:
                        status = "current"
                        status_label = "Current"
                    elif age.days < 3:
                        status = "stale"
                        status_label = "Stale"
                    else:
                        status = "very_stale"
                        status_label = "Very Stale"
                except (ValueError, TypeError):
                    status = "unknown"
                    status_label = "Unknown"

            return {
                "status": status,
                "status_label": status_label,
                "last_reconciled_at": last_at[:19] if len(last_at) > 19 else last_at,
                "delta_total": delta,
                "records_reconciled": count,
            }
        finally:
            adapter.close()
    except Exception:
        return {
            "status": "no_data",
            "status_label": "No Data",
            "last_reconciled_at": "--",
            "delta_total": 0.0,
            "records_reconciled": 0,
        }


@router.get("/api/settings/cost")
async def get_cost_settings():
    """Return current budget state, pricing table, and reconciliation status."""
    cfg = get_config()
    cost_state = _get_cost_state(cfg)
    db = data_layer.get_readonly_db(cfg.sqlite_path)
    try:
        pricing = _get_pricing_table(db)
        reconciliation = _get_reconciliation_status(cfg.duckdb_path)
    finally:
        db.close()
    return JSONResponse({
        "ok": True,
        "cost_state": cost_state,
        "pricing_table": pricing,
        "reconciliation": reconciliation,
    })


@router.post("/api/settings/cost/caps")
async def save_cost_caps(req: CostCapsRequest):
    """Update budget caps. Writes to risk.toml billing section."""
    if req.daily_cap <= 0 or req.monthly_cap <= 0:
        return JSONResponse(
            {"ok": False, "error": "Caps must be greater than zero"},
            status_code=400,
        )

    cfg = get_config()
    risk_path = _Path(cfg.config_dir) / "risk.toml"

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    # Read existing risk.toml
    risk_cfg = {}
    if risk_path.exists():
        try:
            with open(risk_path, "rb") as f:
                risk_cfg = tomllib.load(f)
        except Exception:
            pass

    # Update billing section
    if "billing" not in risk_cfg:
        risk_cfg["billing"] = {}
    risk_cfg["billing"]["daily_cap_usd"] = req.daily_cap
    risk_cfg["billing"]["monthly_cap_usd"] = req.monthly_cap

    # Atomic write — _write_toml_atomic prefers tomli_w and falls back to a
    # flat-section serializer (tomli_w is not a declared dependency; without the
    # fallback this route 500s on ImportError). risk.toml is flat tables of scalars.
    try:
        _write_toml_atomic(risk_path, risk_cfg)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "error": f"Failed to write config: {exc}"},
            status_code=500,
        )

    return JSONResponse({
        "ok": True,
        "daily_cap": req.daily_cap,
        "monthly_cap": req.monthly_cap,
    })


@router.get("/api/settings/cost/personas")
async def get_persona_cost_breakdown():
    """Return per-persona cost breakdown from DuckDB."""
    cfg = get_config()
    persona_costs = _get_persona_costs(cfg.duckdb_path)
    return JSONResponse({"ok": True, "personas": persona_costs})


@router.post("/api/settings/cost/refresh-pricing")
async def refresh_pricing_table():
    """Refresh pricing table from OpenRouter for all cached models."""
    cfg = get_config()
    try:
        from pmacs.billing.pricing import refresh_pricing_table as do_refresh
        db = data_layer.get_readwrite_db(cfg.sqlite_path)
        try:
            # Ensure pricing_table exists
            db.execute(
                """CREATE TABLE IF NOT EXISTS pricing_table (
                    model_id TEXT PRIMARY KEY,
                    input_price_per_token REAL NOT NULL,
                    output_price_per_token REAL NOT NULL,
                    cached_input_price_per_token REAL,
                    per_request_fee REAL DEFAULT 0.0,
                    fetched_at TEXT NOT NULL,
                    source TEXT DEFAULT 'openrouter'
                )"""
            )
            db.commit()
            do_refresh(db)
        finally:
            db.close()
        rows_count = 0
        try:
            db2 = data_layer.get_readonly_db(cfg.sqlite_path)
            row = db2.execute("SELECT COUNT(*) FROM pricing_table").fetchone()
            rows_count = int(row[0]) if row else 0
            db2.close()
        except Exception:
            pass
        return JSONResponse({"ok": True, "models_refreshed": rows_count})
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("Pricing refresh failed: %s", exc, exc_info=True)
        return JSONResponse(
            {"ok": False, "error": "Failed to refresh pricing table"},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# Reset Progress — wipe trading data, keep configuration
# ---------------------------------------------------------------------------


@router.post("/api/settings/reset-progress")
async def reset_progress():
    """Reset all trading progress: positions, decisions, memos, cycles, portfolio.

    Preserves: settings, universe, agent config, inference config, budget caps,
    notification preferences, pricing table, mutation proposals, API usage/cost data.
    """
    import logging
    from datetime import datetime, timezone

    log = logging.getLogger("pmacs.web")
    cfg = get_config()

    try:
        db = data_layer.get_readwrite_db(cfg.sqlite_path)
        try:
            # Clear trading data tables
            db.execute("DELETE FROM holdings")
            db.execute("DELETE FROM decisions")
            db.execute("DELETE FROM memos")
            db.execute("DELETE FROM cycles")
            db.execute("DELETE FROM paper_account")
            db.execute("DELETE FROM universe")

            # Reset portfolio to $5,000
            db.execute(
                "INSERT INTO paper_account (snapshot_at, cash_usd, positions_value_usd, total_value_usd) "
                "VALUES (?, 5000.00, 0.00, 5000.00)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            db.commit()
        finally:
            db.close()

        # Clear in-memory caches so the UI reflects the reset immediately
        from pmacs.web.routes.pipeline import _clear_cycle_caches
        _clear_cycle_caches()

        log.info("Progress reset: holdings, decisions, memos, cycles cleared; portfolio reset to $5,000")
        return JSONResponse({"ok": True})

    except Exception as exc:
        log.error("Progress reset failed: %s", exc, exc_info=True)
        return JSONResponse(
            {"ok": False, "error": "Reset failed — check server logs"},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# §20 Settings expansion — Risk / Crucible / Brokers / Operator config surfaces
# (Source.md §20.3/§20.6/§20.7/§20.12). Each operator-confirmed change writes the
# relevant TOML atomically (flocked), audit-logs the change, and is gated by the
# typed-confirm modal client-side (see test_operator_confirm_gates.py).
# Mirrors the save_cost_caps pattern (settings.py:707).
# ---------------------------------------------------------------------------


def _read_toml(path: _Path) -> dict:
    """Read a TOML file, returning {} if missing/invalid."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _write_toml_atomic(path: _Path, data: dict) -> None:
    """Atomic flocked TOML write (mirrors save_cost_caps).

    Prefers ``tomli_w`` when installed; falls back to a minimal flat-section
    serializer for the {section: {key: scalar}} shape that risk.toml/crucible.toml
    use (so the route works without an extra dependency — tomli_w is not declared
    in pyproject and the existing cost-caps route is latent-broken without it).
    """
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "wb") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            try:
                import tomli_w
                tomli_w.dump(data, f)
            except ImportError:
                f.write(_dump_toml_flat(data))
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    tmp_path.replace(path)


def _toml_scalar(v) -> str:
    """Serialize a scalar to its TOML representation."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    # Fallback — quote anything else as a string.
    return f'"{str(v)}"'


def _dump_toml_flat(data: dict) -> bytes:
    """Minimal TOML serializer for {section: {key: scalar}} + top-level scalars.

    Covers risk.toml / crucible.toml exactly (flat tables of scalars). Not a
    general TOML serializer — nested tables/arrays are out of scope.
    """
    out: list[str] = []
    for section, kv in data.items():
        if isinstance(kv, dict):
            out.append(f"[{section}]")
            for k, v in kv.items():
                out.append(f"{k} = {_toml_scalar(v)}")
            out.append("")
        else:
            # top-level scalar
            out.append(f"{section} = {_toml_scalar(kv)}")
    return ("\n".join(out) + "\n").encode("utf-8")


def _audit_config_change(cfg, event: str, payload: dict) -> None:
    """Append a hash-chained audit entry for a config change (Non-Negotiable #3)."""
    try:
        from pmacs.storage.audit import AuditWriter
        writer = AuditWriter(cfg.audit_path)
        try:
            writer.append(event, payload, cycle_id="settings")
        finally:
            writer.close()
    except Exception:
        # Audit failure must not silently pass; log to stderr but don't crash the response.
        import logging
        logging.getLogger("pmacs.web").warning("audit log write failed for %s", event, exc_info=True)


def _risk_section(cfg) -> dict:
    """Current §20.6 Risk + §20.3 catastrophe-net values from risk.toml."""
    risk_path = _Path(cfg.config_dir) / "risk.toml"
    data = _read_toml(risk_path)
    pos = data.get("position", {})
    ks = data.get("kill_switch", {})
    pr = data.get("pricing", {})
    op = data.get("operator", {})
    return {
        "max_single_position_pct": pos.get("max_single_position_pct", 0.20),
        "max_concurrent_positions": pos.get("max_concurrent_positions", 5),
        "daily_loss_pct": ks.get("daily_loss_pct", 0.05),
        "rolling_5d_loss_pct": ks.get("rolling_5d_loss_pct", 0.10),
        "reconciliation_tolerance_usd": ks.get("reconciliation_tolerance_usd", 100.0),
        "catastrophe_net_stop_pct": pr.get("default_stop_loss_pct", 0.15),
        "per_trade_approval": op.get("per_trade_approval", False),
    }


def _crucible_section(cfg) -> dict:
    """Current §20.7 Crucible time-budget values from crucible.toml."""
    data = _read_toml(_Path(cfg.config_dir) / "crucible.toml")
    tb = data.get("time_budget", {})
    return {
        "seconds_per_attack": tb.get("seconds_per_attack", 90),
        "max_cycles": tb.get("max_cycles", 2),
    }


def _persona_display(cfg) -> list[dict]:
    """§20.9 — read-only persona roster with rolling Brier (persona_ticker_affinity).

    Enable/disable is a Mutation Engine concern (§20.9 "Propose mutation"), not a
    direct toggle here — so this surface is read-only display + a link to #mutations.
    """
    roster = [
        {"id": "catalyst_summarizer", "name": "Catalyst Summarizer", "role": "Event classification"},
        {"id": "growth_hunter", "name": "Growth Hunter", "role": "Fundamental growth"},
        {"id": "moat_analyst", "name": "Moat Analyst", "role": "Competitive moat"},
        {"id": "macro_regime", "name": "Macro Regime", "role": "Macro environment"},
        {"id": "insider_activity", "name": "Insider Activity", "role": "Insider signals"},
        {"id": "short_interest", "name": "Short Interest", "role": "Short thesis"},
        {"id": "forensics", "name": "Forensics", "role": "Accounting quality"},
        {"id": "crucible", "name": "Crucible", "role": "Adversarial testing"},
        {"id": "gatekeeper", "name": "Gatekeeper", "role": "Final gate check"},
    ]
    # Avg rolling Brier per persona across tickers (DuckDB persona_ticker_affinity).
    brier: dict[str, float] = {}
    try:
        from pmacs.storage.duckdb import DuckDBAdapter
        adapter = DuckDBAdapter(db_path=_Path(cfg.duckdb_path))
        try:
            rows = adapter.execute(
                "SELECT persona, AVG(avg_brier) as brier FROM persona_ticker_affinity GROUP BY persona"
            )
            brier = {r["persona"]: float(r["brier"]) for r in rows if r["brier"] is not None}
        finally:
            adapter.close()
    except Exception:
        pass
    out = []
    for p in roster:
        out.append({**p, "brier": brier.get(p["id"])})
    return out


@router.get("/api/settings/risk")
async def get_risk_config():
    """§20.6 — current risk thresholds (read-only JSON for the Settings card)."""
    return JSONResponse({"ok": True, "risk": _risk_section(get_config())})


@router.post("/api/settings/risk")
async def save_risk_config(req: RiskConfigRequest):
    """§20.6 — operator-confirmed risk thresholds. Writes risk.toml [position]/
    [kill_switch], audit-logs the change. Client-side typed-confirm gate required
    (test_operator_confirm_gates.py)."""
    cfg = get_config()
    risk_path = _Path(cfg.config_dir) / "risk.toml"
    data = _read_toml(risk_path)

    changes: dict[str, object] = {}
    if "position" not in data:
        data["position"] = {}
    if "kill_switch" not in data:
        data["kill_switch"] = {}

    if req.max_single_position_pct is not None:
        v = float(req.max_single_position_pct)
        if not 0 < v <= 1.0:
            return JSONResponse({"ok": False, "error": "max_single_position_pct must be in (0, 1.0]"}, status_code=400)
        data["position"]["max_single_position_pct"] = v
        changes["max_single_position_pct"] = v
    if req.max_concurrent_positions is not None:
        v = int(req.max_concurrent_positions)
        if not 1 <= v <= 20:
            return JSONResponse({"ok": False, "error": "max_concurrent_positions must be 1..20"}, status_code=400)
        data["position"]["max_concurrent_positions"] = v
        changes["max_concurrent_positions"] = v
    if req.daily_loss_pct is not None:
        v = float(req.daily_loss_pct)
        if not 0 < v <= 1.0:
            return JSONResponse({"ok": False, "error": "daily_loss_pct must be in (0, 1.0]"}, status_code=400)
        data["kill_switch"]["daily_loss_pct"] = v
        changes["daily_loss_pct"] = v
    if req.rolling_5d_loss_pct is not None:
        v = float(req.rolling_5d_loss_pct)
        if not 0 < v <= 1.0:
            return JSONResponse({"ok": False, "error": "rolling_5d_loss_pct must be in (0, 1.0]"}, status_code=400)
        data["kill_switch"]["rolling_5d_loss_pct"] = v
        changes["rolling_5d_loss_pct"] = v
    if req.reconciliation_tolerance_usd is not None:
        v = float(req.reconciliation_tolerance_usd)
        if v < 0:
            return JSONResponse({"ok": False, "error": "reconciliation_tolerance_usd must be >= 0"}, status_code=400)
        data["kill_switch"]["reconciliation_tolerance_usd"] = v
        changes["reconciliation_tolerance_usd"] = v

    if not changes:
        return JSONResponse({"ok": False, "error": "No fields supplied"}, status_code=400)

    try:
        _write_toml_atomic(risk_path, data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed to write risk.toml: {exc}"}, status_code=500)

    _audit_config_change(cfg, "settings_risk_change", {"changes": changes})
    return JSONResponse({"ok": True, "changes": changes})


@router.post("/api/settings/crucible")
async def save_crucible_config(req: CrucibleConfigRequest):
    """§20.7 — operator-confirmed Crucible time budget. Writes crucible.toml
    [time_budget], audit-logs. Typed-confirm gated."""
    cfg = get_config()
    path = _Path(cfg.config_dir) / "crucible.toml"
    data = _read_toml(path)
    if "time_budget" not in data:
        data["time_budget"] = {}

    changes: dict[str, object] = {}
    if req.seconds_per_attack is not None:
        v = int(req.seconds_per_attack)
        if not 10 <= v <= 600:
            return JSONResponse({"ok": False, "error": "seconds_per_attack must be 10..600"}, status_code=400)
        data["time_budget"]["seconds_per_attack"] = v
        changes["seconds_per_attack"] = v
    if req.max_cycles is not None:
        v = int(req.max_cycles)
        if not 1 <= v <= 5:
            return JSONResponse({"ok": False, "error": "max_cycles must be 1..5"}, status_code=400)
        data["time_budget"]["max_cycles"] = v
        changes["max_cycles"] = v

    if not changes:
        return JSONResponse({"ok": False, "error": "No fields supplied"}, status_code=400)
    try:
        _write_toml_atomic(path, data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed to write crucible.toml: {exc}"}, status_code=500)

    _audit_config_change(cfg, "settings_crucible_change", {"changes": changes})
    return JSONResponse({"ok": True, "changes": changes})


@router.post("/api/settings/brokers")
async def save_brokers_config(req: BrokersConfigRequest):
    """§20.3 — operator-confirmed catastrophe-net stop %. Writes risk.toml
    [pricing] default_stop_loss_pct (broker-side catastrophe net), audit-logs."""
    cfg = get_config()
    risk_path = _Path(cfg.config_dir) / "risk.toml"
    data = _read_toml(risk_path)
    if "pricing" not in data:
        data["pricing"] = {}

    changes: dict[str, object] = {}
    if req.catastrophe_net_stop_pct is not None:
        v = float(req.catastrophe_net_stop_pct)
        # Catastrophe net is a wide stop (15% default); allow 1..50%.
        if not 0.01 <= v <= 0.50:
            return JSONResponse({"ok": False, "error": "catastrophe_net_stop_pct must be in [0.01, 0.50]"}, status_code=400)
        data["pricing"]["default_stop_loss_pct"] = v
        changes["catastrophe_net_stop_pct"] = v

    if not changes:
        return JSONResponse({"ok": False, "error": "No fields supplied"}, status_code=400)
    try:
        _write_toml_atomic(risk_path, data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed to write risk.toml: {exc}"}, status_code=500)

    _audit_config_change(cfg, "settings_brokers_change", {"changes": changes})
    return JSONResponse({"ok": True, "changes": changes})


@router.post("/api/settings/operator")
async def save_operator_config(req: OperatorConfigRequest):
    """§20.12 — operator-confirmed per-trade approval toggle. Writes risk.toml
    [operator] per_trade_approval, audit-logs. Note: enforcement is wired in the
    execution phase; this persists the operator's configured preference."""
    cfg = get_config()
    risk_path = _Path(cfg.config_dir) / "risk.toml"
    data = _read_toml(risk_path)
    if "operator" not in data:
        data["operator"] = {}

    changes: dict[str, object] = {}
    if req.per_trade_approval is not None:
        data["operator"]["per_trade_approval"] = bool(req.per_trade_approval)
        changes["per_trade_approval"] = bool(req.per_trade_approval)

    if not changes:
        return JSONResponse({"ok": False, "error": "No fields supplied"}, status_code=400)
    try:
        _write_toml_atomic(risk_path, data)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"Failed to write risk.toml: {exc}"}, status_code=500)

    _audit_config_change(cfg, "settings_operator_change", {"changes": changes})
    return JSONResponse({"ok": True, "changes": changes})
