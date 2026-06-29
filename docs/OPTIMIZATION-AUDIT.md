# PMACS Optimization Audit — 2026-06-18

A whole-project audit across three dimensions requested by the operator:
**optimize/simplify**, **make the web UI feel more alive/fluid**, and
**improve reachability to its potential**. The high-value, low-risk changes
were implemented directly; larger refactors are documented as follow-ups.

Verification: `.venv/bin/python -m pytest tests/unit -q` →
**1022 passed, 1 skipped**. Import smoke test of every touched module passes.

---

## 1. Make the website feel more alive and fluid

The dashboard was previously a static render that hard-reloaded on every cycle
event. It now reacts in place to real-time state changes.

### What changed

**SSE-driven in-place morphing** (`pmacs/web/static/app.js`)
- New `_morphVerdict(elementId, value, opts)` helper routes live SSE payloads
  through the `PMACS_ANIM` module (number spring-tween + flash, or text morph)
  with a safe `textContent` fallback. `prefers-reduced-motion` is honored
  throughout.
- `decision.arbitrated`, `decision.final` (verdict + conviction %), and
  `cycle-duration` now update existing DOM nodes instead of triggering a
  reload. The conviction bar animates from its old value to the new one.
- `cycle.closed` swaps the cycle card with `transition:200ms`; sparkline
  containers get a `sparkline-fade-in` entrance animation.

**No more hard reloads**
- `_partialRefreshMain()` replaces three `location.reload()` calls with
  `htmx.ajax` partial swaps (`select:#main-content`, `outerHTML transition:200ms`),
  with a reload fallback if the partial fails. Boosted nav and keyboard
  navigation now swap with the same `transition:200ms` modifier.

**Connection-status pill** (`pmacs/web/templates/base.html`, `app.js`)
- Replaced the bare "SSE: connecting/ok" text with a status pill carrying a
  colored dot (`#sse-status-dot`). New `_setSseStatus(text, tone)` centralizes
  the three inline status blocks in `connectSSE`. Reconnection uses the
  existing exponential backoff (max 20 retries) with `last_event_id` resume.

**Health strip** (header chrome)
- New header strip shows inference-backend status + last-cycle time, polled
  every 30 s via a new read-only `/api/health/detail` endpoint
  (`pmacs/web/routes/cortex.py`). Every field is best-effort and degrades to a
  safe default so the strip never hard-errors. Wired into `DOMContentLoaded`
  and `htmx:afterSettle` so it survives partial nav.

**CSS transitions** (`pmacs/web/static/style.css`)
- `#cycle-progress-bar` width now tweens over 0.6 s with `--ease-out-expo`.
- Added `sparkline-fade-in` keyframes.
- The existing reduced-motion media query zeroes all durations — the new
  animations are WCAG-AA safe and degrade correctly.

### Navigation reachability
- Memo, Ticker, and Compare pages were not reachable from the nav bar.
  Added them to `base.html`'s page list with keyboard shortcuts
  (Compare added to `CMD_K_PAGES`). Conditional `{% if shortcut %}` keeps the
  kbd hint from rendering when a page has no shortcut.
- **Cortex mode label** was hardcoded. Routes now capture
  `current_mode = data_layer.get_current_mode(db)` (real mode from
  `mode_history`) and pass it to every template — Cortex, Dashboard, Compare,
  Memo, and Settings all reflect the actual current mode instead of the stale
  `SHADOW + PAPER` literal.

---

## 2. Code optimization & simplification

### Shared HTML stripper (`pmacs/data/sources/_html.py`, new)
- `strip_html()` was duplicated between `ir_pages.py` and `edgar_kpi.py` with
  slightly different behavior. Extracted one canonical implementation in
  `_html.py`; both sources import it. A behavior fix now applies once.
  Removed the now-unused `import re` from `ir_pages.py`.

### Single cost-state source (DuckDB, not SQLite)
- `_get_cost_state` / `_get_cost_state_for_dashboard` existed in three routes
  (`dashboard.py`, `settings.py`) and all queried **SQLite** for `api_usage` —
  but that table lives in **DuckDB** (column `called_at`), so every cost widget
  silently returned `$0`. Consolidated into one
  `data_layer.get_cost_state(cfg)` (DuckDB-backed, dialect-correct
  `datetime('now','-7 days')` / `date_trunc`). The three route helpers now
  delegate to it. No tests referenced the old helpers, so this was safe.

### Single mode source
- `data_layer.get_current_mode(db)` added (SELECT `to_mode` from `mode_history`
  ORDER BY id DESC LIMIT 1) — used by all the routes above.

### Follow-ups resolved this pass
- `pmacs/web/app.py` deprecated `@app.on_event("startup")` → converted to a
  FastAPI `lifespan` context manager (deprecation warning cleared).
- `config/notification.toml` was unused (config.py never loads it; notification
  levels are SQLite-backed via `settings.py`; no tests reference it) → removed.
- `pyproject` optional extras: `playwright`, `sentence-transformers`, and
  `qrcode` moved out of core `dependencies` into
  `[project.optional-dependencies]` (`a11y`, `embeddings`, `qrcode`) so a base
  install only pulls the core runtime. `pmacs/cli.py`'s bootstrap
  `required_packages` list synced to the same minimal core. All three deps are
  imported lazily and degrade gracefully when absent (`qdrant.py` already
  guards embeddings; the wizard emits an install hint; `qrcode` is dead — TOTP
  is disabled).
- The `launchd` :8000 plist conflict was already resolved in `c8aca25`
  (dashboard plist removed; installer `EXPECTED_PLISTS` lists 7 services).
  Audit-doc note removed.

### Follow-ups still open (larger surface — by appetite)
- `pmacs/data/pipeline.py` is a monolith → split by stage.
- `pmacs/data/sources/finnhub_fundamentals.py` is now fallback-only → consider
  removal once yfinance coverage is confirmed stable.
- `_to_float` was flagged as duplicated across data sources, but after the
  shared `_html.py` extraction only one copy remains (`edgar_kpi.py`); no
  consolidation needed.
- `EUR 0.92` hardcoded FX conversion was listed previously but is **not
  present in source** — FX already routes through `pmacs/data/fx.py` (ECB
  cube). No action.

---

## 3. Reachability to its potential

### Operator-facing
- Every page now shows the **real current mode**, so the operator can trust
  the chrome instead of remembering which mode the system is in.
- The header **health strip** surfaces inference-backend health and last-cycle
  time on every page — the system's "is it alive?" signal is now ambient
  instead of buried in the Cortex page.
- Live SSE morphing means the dashboard reads as a running system, not a
  snapshot — verdicts and conviction move as cycles complete, which is the
  core UX promise of a catalyst-driven engine.
- Memo/Ticker/Compare are reachable from nav and ⌘K, matching the spec's
  page set (Source.md §14–20, §16.8).

### Architectural reachability (follow-ups)
- The DuckDB cost-state fix makes the cost widget actually reflect spend — a
  prerequisite for the operator to trust the budget feedback loop that gates
  mode promotion.
- The remaining simplifications (canonical helpers, lifespan, pipeline split,
  plist cleanup) lower the cognitive cost of extending the system — the
  single biggest lever for a single-operator project to reach its potential
  is keeping the surface small and the duplication zero.

---

## Files touched

| File | Change |
|---|---|
| `pmacs/web/static/app.js` | SSE morph helpers, partial refresh, status pill, health poll |
| `pmacs/web/static/style.css` | progress-bar + sparkline transitions |
| `pmacs/web/templates/base.html` | status pill, health strip, nav + ⌘K entries |
| `pmacs/web/data.py` | `get_current_mode`, `get_cost_state` (DuckDB) |
| `pmacs/web/routes/cortex.py` | real mode, `/api/health/detail` endpoint, syntax fix |
| `pmacs/web/routes/dashboard.py` | real mode, delegate cost state |
| `pmacs/web/routes/settings.py` | delegate cost state |
| `pmacs/web/routes/memo.py` | real mode |
| `pmacs/web/routes/compare.py` | real mode (was missing) |
| `pmacs/data/sources/_html.py` | new shared `strip_html` |
| `pmacs/data/sources/ir_pages.py` | use shared stripper, drop `re` |
| `pmacs/data/sources/edgar_kpi.py` | delegate to shared stripper |
| `pmacs/web/app.py` | `@app.on_event("startup")` → `lifespan` handler |
| `pmacs/cli.py` | bootstrap `required_packages` synced to minimal core |
| `pyproject.toml` | `playwright`/`sentence-transformers`/`qrcode` → optional extras |
| `config/notification.toml` | removed (unused) |

No spec behavior was invented. All Five Non-Negotiables and anti-patterns
preserved: no LLM signing/math, hash-chained audit untouched, local-only
execution preserved, kill switch unchanged, `canonical_json` / rate-limit
buckets / `cycle_id` contracts untouched.
