"""Debug route — event stream viewer."""

from fastapi import APIRouter, Request

from pmacs.web.templating import templates
from pmacs.web.config import get_config
from pmacs.web import data as data_layer

router = APIRouter()


@router.get("/debug")
async def debug_page(request: Request):
    """Render the debug event stream page."""
    cfg = get_config()

    try:
        # Parse filter params from query string
        level = request.query_params.get("level")
        event = request.query_params.get("event")
        cycle_id = request.query_params.get("cycle_id")

        filters = {}
        if level:
            filters["level"] = level
        if event:
            filters["event"] = event
        if cycle_id:
            filters["cycle_id"] = cycle_id

        raw_events = data_layer.get_debug_events(cfg.debug_log_path, filters or None)

        # Map to template-expected field names
        events = [
            {
                "level": e.get("level", "INFO"),
                "timestamp": e.get("ts", ""),
                "stream": e.get("event", ""),
                "message": e.get("msg", ""),
                "detail": e.get("payload", ""),
                "error_code": e.get("error_code", ""),
                "spec_ref": e.get("spec_ref", ""),
                "cycle_id": e.get("cycle_id", ""),
                "event_id": e.get("event_id", e.get("ts", "")),
                "raw_payload": e.get("payload", ""),
            }
            for e in raw_events
        ]

        # When no live debug events exist, pre-populate from audit log
        recent_events: list[dict] = []
        if not events:
            raw_audit = data_layer.get_recent_audit_entries(cfg.audit_path, limit=50)
            recent_events = []
            for e in raw_audit:
                event_type = e.get("event", "")
                msg = e.get("msg", event_type)
                payload = e.get("payload", "")
                # Enrich DECISION events: parse payload to show ticker + verdict
                if event_type == "DECISION" and payload:
                    try:
                        import json as _json
                        p = _json.loads(payload) if isinstance(payload, str) else payload
                        ticker = p.get("ticker", "")
                        verdict = p.get("verdict", "")
                        conviction = p.get("conviction")
                        if ticker and verdict:
                            pct = f" {round(conviction * 100):.0f}%" if conviction is not None else ""
                            msg = f"{ticker} → {verdict}{pct}"
                    except Exception:
                        pass
                recent_events.append({
                    "level": e.get("level", "INFO"),
                    "timestamp": e.get("ts", ""),
                    "stream": event_type,
                    "message": msg,
                    "detail": payload,
                    "error_code": "",
                    "spec_ref": "",
                    "cycle_id": e.get("cycle_id", ""),
                    "event_id": e.get("ts", ""),
                    "raw_payload": payload,
                })

        return templates.TemplateResponse(
            request=request,
            name="debug.html",
            context={
                "page": "debug",
                "mode": "SHADOW + PAPER",
                "events": events,
                "recent_events": recent_events,
                "filter_chips": [
                    "ALL",
                    "CYCLE",
                    "TRADE",
                    "AUDIT",
                    "ERROR",
                    "WARN",
                    "KILL_SWITCH",
                    "MUTATION",
                ],
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request=request,
            name="debug.html",
            context={
                "page": "debug",
                "error": data_layer.build_error_context("debug", exc),
            },
        )
