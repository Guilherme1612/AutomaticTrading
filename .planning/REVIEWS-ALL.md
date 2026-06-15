---
scope: all uncommitted changes (47 files, +2084/-1049)
reviewers: [codex/gpt-5.5]
reviewed_at: 2026-06-12T17:16:00Z
skipped: claude (self - running inside Claude Code)
unavailable: gemini, opencode, qwen, cursor
---

# Cross-AI Code Review — Uncommitted Changes

## Codex Review (GPT-5.5)

**Summary**

This change set improves evidence discipline, determinism, UI clarity, SSE reliability, cloud-backend accessibility, and memo richness. The LLM-facing changes move in the right direction by anchoring outputs to evidence and adding Python-side probability snapping/extraction, but there are brittle parsing choices, inconsistent comments, and places where LLM-derived or regex-derived data could be displayed as if authoritative. Overall, this is a useful product/UI and analysis-quality iteration with a few medium-severity items to address.

**Strengths**

- Evidence anchoring and determinism are much stronger in prompts and schemas: probability grid snapping, data-availability confidence rules, and explicit "do not fabricate" constraints reduce LLM variance.
- Python remains the source of arbitrated probabilities, conviction, EV multiple, and verdict fields in memo generation, which respects the "LLMs do narrative, Python does math" boundary.
- SSE publisher changes are well-motivated: timestamp-based IDs plus `loop.call_soon_threadsafe()` address stale `Last-Event-ID` and worker-thread queue wakeup issues.
- API-key error sanitization in `settings.py` is a good security hardening step.
- Universe template adds CSRF headers for several write operations, which is a positive direction.
- Memo generation now preserves engine-authoritative values in stored memo JSON, improving auditability and making memo pages less dependent on LLM text.
- Data fetch timeout and socket timeout handling reduce cycle hangs from external libraries.

**Concerns**

- **MEDIUM: Cloud backends are now allowed, but local-only assumptions remain partially encoded.** Some UI and comments were updated, but architecture/security controls for cloud inference are not visible here: telemetry, prompt leakage, API key handling, model provenance, and audit distinction between local/cloud need explicit treatment.
- **MEDIUM: JSON extraction via regex is brittle.** `_call_openai_compatible()` extracts `r'\{[\s\S]*\}'`, which can capture too much if reasoning contains braces before/after the JSON. Prefer a balanced JSON extraction parser or strict response validation with retry.
- **MEDIUM: Probability snapping before sum validation can silently distort distributions.** Snapping `p_up`, `p_flat`, and `p_down` independently and allowing `+-0.03` tolerance means accepted outputs may not sum exactly to 1. Downstream code may assume exact normalization.
- **MEDIUM: Comment drift in `_arbitrate()`.** It says `_CLAMP_THRESHOLD=0.50`, but code uses `0.25`. This is minor technically but dangerous in a risk engine because reviewers will misunderstand calibration behavior.
- **MEDIUM: KPI extraction may promote weak data to authoritative UI.** Regex scans LLM analysis and `[KNOWLEDGE]` text, not only structured evidence. Memo UI may present extracted KPIs as concrete facts even when they came from model narrative.
- **MEDIUM: Data-dependent agent gating only runs when `fundamentals` is truthy.** If `fundamentals == ""`, `required_markers and fundamentals` is false, so insider/short-interest agents still call the LLM despite missing required data.
- **LOW/MEDIUM: Static ticker knowledge is stale-risky.** Hard-coded company metrics like market share, profiles, customer count, and margins can age quickly and may be mistaken for evidence.
- **LOW: `model_registry.json` loses trailing newline.** Minor hygiene issue.
**Security Review**

CSRF coverage is incomplete based on the diff. Universe writes gained CSRF headers, but other write paths need server-side CSRF verification confirmed. Client-side headers alone are not protection unless every write endpoint validates them.

XSS risk looks mostly controlled in templates because Jinja autoescaping and `escapeHtml()` are used in many dynamic JS insertions. However, memo fields, agent analysis, evidence strings, and KPI values are LLM/data-source derived and displayed broadly. Avoid any future use of `|safe` on those fields, and validate that `showToast()` escapes content.

API-key leakage handling improved in the connection-test route, but cloud backend enablement increases prompt/data exfiltration risk by design. Audit logs should record provider/model and whether the request left the machine.

**Architecture Review**

The LLM/math/signing boundaries are mostly preserved. LLM outputs are parsed, constrained, and then Python performs arbitration, conviction, and final engine-authoritative probability fields. No LLM trade signing introduced.

Hash-chaining is not materially addressed in this diff. New stateful actions and memo fields should ensure audit events still include `prev_sha256`; the diff does not show new audit coverage for cloud provider switching, memo generation changes, or mutation actions beyond existing logs.

Cloud backends being allowed is a changed constraint. That needs an explicit policy layer: allowed providers, redaction rules, prompt retention assumptions, cost tracking, and audit labels.

**Suggestions**

- Add server-side CSRF validation tests for every POST/PUT/DELETE route, especially settings, cortex, universe, and mutation routes.
- Fix `_arbitrate()` comment to match `_CLAMP_THRESHOLD=0.25`.
- Change data-dependent gate to run even when `fundamentals` is empty.
- Normalize probabilities after snapping so stored/returned values sum exactly to 1.0.
- Replace greedy JSON regex extraction with balanced JSON object extraction or a strict decoder scan.
- Split KPI extraction into `source=evidence|agent|knowledge` and only render evidence-derived values as hard KPIs.
- Add tests for missing-fundamentals agent neutralization, probability normalization, SSE cross-thread publish, and cloud error sanitization.
- Add audit events for provider/model changes and API mode activation.

**Risk Assessment**

**MEDIUM risk**. The analysis-quality and UI changes are broadly useful. The main concerns are around brittle JSON parsing, probability normalization gaps, and incomplete CSRF coverage. These are fixable without architectural changes.

---

## Consensus Summary

### Agreed Strengths
- Evidence anchoring and determinism improvements are well-designed
- LLM/math/signing boundaries remain intact
- SSE reliability fixes are well-motivated
- API-key error sanitization is good hardening

### Top Concerns (by severity)
1. **MEDIUM** — Cloud backend enablement lacks explicit security/audit policy
2. **MEDIUM** — Brittle JSON regex extraction in LLM response parsing
3. **MEDIUM** — Probability snapping may break normalization invariant
4. **MEDIUM** — Comment drift in `_arbitrate()` threshold constant
5. **MEDIUM** — Data-dependent agent gating bypassed on empty string

### Actionable Items
| # | Severity | Issue | File(s) |
|---|----------|-------|---------|
| 1 | MEDIUM | Fix `_arbitrate()` comment drift (0.50 vs 0.25) | `pmacs/agents/base.py` |
| 2 | MEDIUM | Normalize probabilities after snapping to sum=1.0 | `pmacs/schemas/personas.py` |
| 3 | MEDIUM | Replace greedy JSON regex with balanced extraction | `pmacs/agents/base.py` |
| 4 | MEDIUM | Fix data-dependent agent gating for empty string | `pmacs/agents/base.py` |
| 5 | MEDIUM | Source-tag KPI extraction (evidence vs narrative) | `pmacs/web/routes/pipeline.py` |
| 6 | LOW | Add trailing newline to model_registry.json | `config/model_registry.json` |
