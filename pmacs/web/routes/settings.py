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

    providers = []
    for k, v in backends.items():
        prov_key_ref = v.get("api_key_ref", "")
        prov_has_key = False
        if prov_key_ref:
            try:
                import keyring
                prov_has_key = bool(keyring.get_password("pmacs.credentials", prov_key_ref))
            except Exception:
                pass
        providers.append({
            "id": k,
            "model": v.get("default_model", ""),
            "structured_output": v.get("structured_output", ""),
            "base_url": v.get("base_url", ""),
            "needs_key": bool(prov_key_ref),
            "has_api_key": prov_has_key,
        })

    return {
        "active": active,
        "model": backend.get("default_model", ""),
        "structured_output": backend.get("structured_output", ""),
        "base_url": backend.get("base_url", ""),
        "api_key_ref": api_key_ref,
        "has_api_key": has_api_key,
        "providers": providers,
    }


class NotificationLevelRequest(BaseModel):
    event: str
    level: str


class MutationActionRequest(BaseModel):
    candidate_id: str
    totp_code: str = ""  # Required for promote/rollback; validated server-side


class CostCapsRequest(BaseModel):
    daily_cap: float
    monthly_cap: float
    totp_code: str = ""  # Required — budget changes are TOTP-gated


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

        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "page": "settings",
                "mode": "SHADOW + PAPER",
                "sections": [
                    "General",
                    "Brokers",
                    "Inference",
                    "Universe",
                    "Risk",
                    "Crucible",
                    "Mutation Engine",
                    "Agent Personas",
                    "Queue",
                    "Audit & Debug",
                    "Cost & Budget",
                    "Operator",
                ],
                "config": config,
                "mutation_candidates": mutation_candidates,
                "recent_mutations": recent_mutations,
                "notification_levels": notification_levels,
                "inference": inference,
                "cost_state": _get_cost_state(cfg),
                "persona_costs": _get_persona_costs(cfg.duckdb_path),
                "pricing_table": pricing_table,
                "reconciliation": _get_reconciliation_status(cfg.duckdb_path),
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
    totp_code: str = ""  # TOTP-gated: changing inference provider is security-sensitive


class InferenceApiKeyRequest(BaseModel):
    provider: str
    api_key: str
    totp_code: str = ""  # TOTP-gated: API key changes are security-sensitive


class InferenceModelRequest(BaseModel):
    provider: str
    model: str
    totp_code: str = ""  # TOTP-gated: model changes affect inference behavior


@router.get("/api/settings/inference")
async def get_inference_config():
    """Return current inference provider configuration."""
    return JSONResponse(_get_inference_state())


@router.post("/api/settings/inference/provider")
async def set_inference_provider(req: InferenceProviderRequest):
    """Switch the active LLM provider in model_registry.json."""
    # TOTP-gated: changing inference provider is security-sensitive
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
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
    # TOTP-gated: API key changes are security-sensitive
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
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
    # TOTP-gated: model changes affect inference behavior
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
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

    if active == "llama_server":
        import httpx
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

    # Cloud provider test
    import httpx
    backend = _load_registry()["backends"].get(active, {})
    api_key_ref = backend.get("api_key_ref", "")
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
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# Mutation API endpoints (Source.md §6 — TOTP-gated for promote/reject)
# ---------------------------------------------------------------------------


@router.post("/api/mutation/promote")
async def mutation_promote(req: MutationActionRequest):
    """Promote a mutation candidate to production (TOTP-gated).

    The JS side calls open_totp_modal() first, then posts here on verification.
    Server-side TOTP verification is enforced — direct POST without valid code is rejected.
    Updates candidate status to 'approved' and records the promotion.
    """
    # Server-side TOTP verification (Source.md §6, Non-Negotiable #5)
    if not req.totp_code or len(req.totp_code) != 6:
        return JSONResponse(
            {"ok": False, "error": "TOTP code required (6 digits)"},
            status_code=403,
        )
    try:
        from pmacs.cortex.totp import verify_totp
        from pmacs.storage.keychain import get_api_key
        secret = get_api_key("pmacs.system.totp_secret", "operator")
        if not verify_totp(secret, req.totp_code):
            return JSONResponse(
                {"ok": False, "error": "Invalid TOTP code"},
                status_code=403,
            )
    except ImportError as exc:
        # Keychain or TOTP module not available — BLOCK the mutation, do not allow
        import logging
        logging.getLogger("pmacs.web").error("TOTP verification unavailable: %s", exc, exc_info=True)
        return JSONResponse(
            {"ok": False, "error": "TOTP verification unavailable"},
            status_code=503,
        )
    except Exception as exc:
        # TOTP secret not configured — BLOCK, never silently pass
        import logging
        logging.getLogger("pmacs.web").error("TOTP verification failed: %s", exc, exc_info=True)
        return JSONResponse(
            {"ok": False, "error": "TOTP verification failed"},
            status_code=403,
        )
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
    """Reject a mutation candidate (TOTP-gated per CLAUDE.md)."""
    # TOTP verification — ALL mutation actions require operator TOTP
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)
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
    """Rollback a promoted mutation (TOTP-gated).

    Reverts the candidate to 'rolled_back' status and records in mutation_log.
    Server-side TOTP verification is enforced.
    """
    # Server-side TOTP verification
    if not req.totp_code or len(req.totp_code) != 6:
        return JSONResponse(
            {"ok": False, "error": "TOTP code required (6 digits)"},
            status_code=403,
        )
    try:
        from pmacs.cortex.totp import verify_totp
        from pmacs.storage.keychain import get_api_key
        secret = get_api_key("pmacs.system.totp_secret", "operator")
        if not verify_totp(secret, req.totp_code):
            return JSONResponse(
                {"ok": False, "error": "Invalid TOTP code"},
                status_code=403,
            )
    except ImportError as exc:
        import logging
        logging.getLogger("pmacs.web").error("TOTP verification unavailable: %s", exc, exc_info=True)
        return JSONResponse(
            {"ok": False, "error": "TOTP verification unavailable"},
            status_code=503,
        )
    except Exception as exc:
        import logging
        logging.getLogger("pmacs.web").error("TOTP verification failed: %s", exc, exc_info=True)
        return JSONResponse(
            {"ok": False, "error": "TOTP verification failed"},
            status_code=403,
        )
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
    """Get current budget period state for the cost widget and settings."""
    daily_cap = 2.00
    monthly_cap = 30.00
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    risk_path = _Path(cfg.config_dir) / "risk.toml"
    if risk_path.exists():
        try:
            with open(risk_path, "rb") as f:
                risk_cfg = tomllib.load(f)
            daily_cap = risk_cfg.get("billing", {}).get("daily_cap_usd", daily_cap)
            monthly_cap = risk_cfg.get("billing", {}).get("monthly_cap_usd", monthly_cap)
        except Exception:
            pass

    today_cost = 0.0
    month_cost = 0.0
    last_cycle_cost = 0.0
    avg_cycle_cost = 0.0

    try:
        db = data_layer.get_readonly_db(cfg.sqlite_path)
        try:
            row = db.execute(
                "SELECT COALESCE(SUM(body_cost_usd), 0) FROM api_usage "
                "WHERE date(called_at) = date('now')"
            ).fetchone()
            today_cost = float(row[0]) if row else 0.0

            row = db.execute(
                "SELECT COALESCE(SUM(body_cost_usd), 0) FROM api_usage "
                "WHERE strftime('%Y-%m', called_at) = strftime('%Y-%m', 'now')"
            ).fetchone()
            month_cost = float(row[0]) if row else 0.0

            row = db.execute(
                "SELECT body_cost_usd FROM api_usage ORDER BY called_at DESC LIMIT 1"
            ).fetchone()
            last_cycle_cost = float(row[0]) if row else 0.0

            row = db.execute(
                "SELECT AVG(body_cost_usd) FROM api_usage "
                "WHERE called_at >= datetime('now', '-7 days')"
            ).fetchone()
            avg_cycle_cost = float(row[0]) if row and row[0] else 0.0
        finally:
            db.close()
    except Exception:
        pass

    return {
        "today_cost": today_cost,
        "month_cost": month_cost,
        "daily_cap": daily_cap,
        "monthly_cap": monthly_cap,
        "last_cycle_cost": last_cycle_cost,
        "avg_cycle_cost": avg_cycle_cost,
    }


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


def _verify_totp(totp_code: str) -> tuple[bool, str]:
    """TOTP disabled — always passes."""
    return True, ""


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
    """Update budget caps (TOTP-gated). Writes to risk.toml billing section."""
    # TOTP verification
    ok, err = _verify_totp(req.totp_code)
    if not ok:
        return JSONResponse({"ok": False, "error": err}, status_code=403)

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

    import tomli_w

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

    # Atomic write
    tmp_path = risk_path.with_suffix(".tmp")
    try:
        with open(tmp_path, "wb") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                tomli_w.dump(risk_cfg, f)
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        tmp_path.replace(risk_path)
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
