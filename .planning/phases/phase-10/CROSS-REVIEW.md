# Phase 10 Cross-Review: Plan vs Spec vs Implementation

**Reviewer:** Claude (gsd-code-reviewer)
**Date:** 2026-05-26
**Scope:** PLAN.md, 10-REVIEW.md, SUMMARY.md, adapter.py, wizard.py, service.py, catastrophe_net.py, sse_publisher.py, dead_letter.py, alpaca_paper_adapter.py, tgp_enroll.py, verify_llm.py, verify_data.py, spec/Architecture.md, spec/Source.md

---

## Plan-Spec Alignment (score: 3/5)

**Strengths:**
- BrokerAdapter ABC matches Architecture.md S4.1 (adapter behind ABC, only one file imports alpaca SDK).
- Catastrophe-net stop at 15% matches Architecture.md S16.7 (broker gets only catastrophe-net, not tight stops).
- Dead-letter backoff schedule `[1, 5, 30, 300, 3600, 86400]` matches Architecture.md S14.1 exactly.
- SSE Last-Event-ID resume is a reasonable interpretation of Architecture.md S4.4 (SSE fan-out).

**Gaps:**
1. **Wizard step count is wrong.** Source.md S12 defines 11 steps (1-11, with a "Step 4.5" for embedding). The implementation has 12 steps because it splits Step 10 into two separate steps (step10_llm_provider.html + step10_totp.html), then adds step11_complete.html. The spec's Step 9 is TOTP, Step 10 is smoke-test cycle, Step 11 is promote. The implementation has no smoke-test cycle step at all. Instead it has a "LLM Provider" step (not in spec) at step 10. The PLAN.md correctly listed 11 steps with TOTP at step 10, but the implementation diverged.

2. **Missing smoke-test cycle.** Source.md S12 Step 10 specifies a full synthetic pipeline run before promotion. This is absent from the implementation. The wizard jumps from TOTP enrollment directly to promotion without validating the system end-to-end.

3. **Spec says "11 dots" in progress strip.** Implementation has 12 steps, so the progress strip shows 12 dots. This contradicts Source.md S12.3 ("progress strip at the top showing 11 dots").

4. **Step 4 (keychain) does not test credentials.** Source.md S12 Step 4 says "The wizard tests each credential against a small read query before accepting." The implementation at wizard.py:220-232 uses `keyring.set_password()` but never validates the credentials work. The verify_data step (step 7) does connectivity checks, but the spec says credential validation should happen at step 4.

5. **Step 4.5 (embedding model) uses wrong model name.** Spec says `BAAI/bge-base-en-v1.5` (768-dim). The implementation at wizard.py:241 uses `all-MiniLM-L6-v2` (384-dim). This is a different model with different dimensionality. Downstream Qdrant collections configured for 768-dim would break.

---

## Exit Test Coverage (score: 3/5)

The PLAN.md defines 8 verification items. Assessment of each:

| # | Exit Test Item | Status | Notes |
|---|---|---|---|
| 1 | AlpacaPaperAdapter submits LIMIT BUY | PASS | Adapter correctly builds LimitOrderRequest. MARKET_ON_OPEN support added beyond spec. |
| 2 | Fill received and paper ledger updated | PARTIAL | MockAdapter fill works; AlpacaPaperAdapter fill path is untested in CI (requires real API). No test verifies paper ledger update after fill. |
| 3 | Catastrophe-net stop at 15% below entry | PASS | compute_catastrophe_stop uses CATASTROPHE_NET_PCT constant. Service._place_catastrophe_stop correctly calls adapter.place_stop_order. |
| 4 | Wizard steps 1-11 render in browser | FAIL | 12 steps exist, not 11. No smoke-test cycle. Progress strip shows 12 dots. Step numbering is off from spec. |
| 5 | Dead-letter entries persist to SQLite | PASS | DeadLetterStore uses canonical_json, proper backoff, 6-attempt max, EXHAUSTED event. |
| 6 | SSE client reconnects with Last-Event-ID | PASS | Ring buffer stores (event_id, frame) tuples. get_events_since filters eid > last_id. |
| 7 | All 9 Ollama JSON schemas validate | PASS | 9 schemas in schemas_json/ directory with additionalProperties: false. |
| 8 | Full test suite green | PARTIAL | SUMMARY.md claims complete, but wizard step count divergence and embedding model mismatch are functional bugs that tests would not catch. |

**Missing exit tests:**
- No test that Ed25519 signature verification rejects forged payloads end-to-end with real adapter.
- No test for the wizard promotion path (step 12 / transition_mode).
- No test for IBKRLiveAdapter import guard in create_adapter (it imports `pmacs.execution.ibkr_adapter` which may not exist).

---

## Implementation Quality (score: 4/5)

**Architecture:**
- BrokerAdapter ABC is clean. Five methods, all async, well-documented docstrings.
- Factory pattern (`create_adapter`) correctly routes by mode. LIVE modes import IBKRLiveAdapter (deferred import, not on critical path).
- AlpacaPaperAdapter wraps sync SDK with `asyncio.to_thread` -- correct pattern for event-loop compatibility.
- MARKET_ON_OPEN support with graceful OPG fallback is well-implemented.
- Only `alpaca_paper.py` imports the SDK, matching Architecture.md S4.1.

**Execution Service:**
- Signature verification happens before adapter submission (Non-Negotiable #1 preserved).
- MockAdapter backward compat maintained (adapter=None defaults to MockAdapter).
- Catastrophe-net stop placed after fill, failure logged but does not block position recording.
- Error code `CATASTROPHE_NET_FAILED` is now in VALID_ERROR_CODES (fix confirmed).
- Event type `CATASTROPHE_NET_PLACED` is now in SYSTEM_EVENT_TYPES (fix confirmed).

**Dead Letter Queue:**
- Uses `canonical_json` (fix confirmed from M3 in review).
- Parameterized SQL throughout (no injection risk).
- Backoff logic is sound: process_next checks retry_count and elapsed time.

**SSE Publisher:**
- Thread-safe with threading.Lock.
- Ring buffer with correct `eid > last_id` filtering.
- Queue-full clients auto-unsubscribed.

**Security:**
- TOTP uses `hmac.compare_digest` (timing-safe).
- TOTP secret stored via macOS Keychain with file-based fallback (0600 permissions).
- `_secret` prefixed with underscore in return dict (convention, not rendered in template).
- Credentials stored via `keyring` library (not env vars or config files).

**Weaknesses:**
1. **cancel_catastrophe_net is async, callers must await.** This was flagged in the original review as H5. The function is now correctly `async def` and uses `await broker.cancel_order(order_id)` at line 113. This fix is confirmed.
2. **Wizard state in cookies, not SQLite.** Line 41 reads `pmacs_wizard_step` cookie. Comment says "production: SQLite" but implementation uses cookies. Clients can manipulate cookies to skip steps. This was flagged as L2 in the review and remains unfixed.
3. **TOTP secret file fallback is weaker than keychain.** File at `~/.pmacs/totp_secret` with 0600 is acceptable for non-macOS but less secure than a keychain. The implementation properly returns False on failure, and the wizard step checks this (wizard.py:417 returns `result.get("ok", False)`).
4. **execute_exit computes qty as float.** At catastrophe_net.py:186, `exit_order["qty"] = holding.position_size_usd / holding.entry_price_usd` produces a float. But D3 says "quantity stays int" and TradePlan.quantity is `int`. This qty should be `int(...)`.

---

## Gaps & Risks

### Critical

1. **Wizard step count: 12 vs spec's 11.** The STEP_TEMPLATES dict at wizard.py:16-29 maps steps 1-12 to templates, but step 10 maps to TWO templates (step10_llm_provider.html and step10_totp.html -- both listed as key 10 and 11 in the dict). Wait -- actually looking closer: keys 10 and 11 in the dict are step10_llm_provider.html and step10_totp.html, and key 12 is step11_complete.html. This means step 10 in the backend dispatch (wizard.py:356-411) is "LLM Provider selection" and step 11 is "TOTP enrollment" and step 12 is "Promote". The spec says step 9 is TOTP, step 10 is smoke-test, step 11 is promote. The implementation adds an extra step (LLM Provider) and drops smoke-test. **This is a spec compliance violation.**

2. **Missing smoke-test cycle step.** Source.md S12 Step 10 requires running a full synthetic pipeline before promotion. This is a safety gate -- it verifies all 5 stores are writable, the audit chain validates, and the kill-switch trigger works. Without it, the wizard promotes to PAPER without ever testing the system.

3. **Embedding model mismatch.** Wizard step 5 checks for `all-MiniLM-L6-v2` (384-dim) while the spec requires `BAAI/bge-base-en-v1.5` (768-dim). If Qdrant collections are initialized expecting 768-dim vectors, the system will fail at runtime when the embedding model produces 384-dim vectors.

### Warning

4. **IBKRLiveAdapter import in create_adapter may fail at runtime.** At adapter.py:159, `from pmacs.execution.ibkr_adapter import IBKRLiveAdapter` is inside a conditional but will raise ImportError if the file does not exist. This is a deferred import so it only fails when someone selects LIVE mode, but there is no graceful fallback or informative error message.

5. **Wizard promotion does not set mode in SQLite config table.** Step 12 at wizard.py:469 inserts into `mode_history` but does not update the current mode in a config/settings table. If the system reads current mode from a different source on startup, it may boot as INSTALLING despite the wizard having promoted.

6. **execute_exit qty is float, spec says int.** catastrophe_net.py:186 computes `position_size_usd / entry_price_usd` which produces a float. TradePlan.quantity is `Field(ge=1)` with type `int`. This will either fail Pydantic validation or produce incorrect order quantities.

### Info

7. **Cookie-based wizard state allows step skipping.** An operator could manually set `pmacs_wizard_step=12` and skip directly to promotion. Low risk on a single-operator local system, but violates the spec's "each step blocks until passed" requirement.

8. **No wizard re-entry/reset implementation.** Source.md S12.2 specifies `pmacs wizard --reset` which wipes all state and requires TOTP confirmation. This CLI command is not implemented in the wizard route or CLI.

---

## Recommendations

1. **Fix wizard step count to match spec.** Remove the LLM Provider step (or merge it with Step 2 inference detection). Add the smoke-test cycle step. Ensure TOTAL_STEPS=11 and progress strip shows 11 dots. This is a spec compliance issue.

2. **Implement smoke-test cycle step.** Source.md S12 Step 10 is a safety gate. Without it, the system promotes to PAPER without verifying the full pipeline works. Use the existing synthetic fixtures from tests/e2e/ to run one cycle.

3. **Fix embedding model name.** Change `all-MiniLM-L6-v2` to `BAAI/bge-base-en-v1.5` at wizard.py:241. Verify the dimensionality matches Qdrant collection configuration.

4. **Fix execute_exit qty to int.** Add `int(round(...))` at catastrophe_net.py:186 to comply with D3 (quantity stays int).

5. **Guard IBKRLiveAdapter import.** Wrap the import in try/except ImportError with a clear error message: "IBKR adapter not yet implemented. LIVE mode is not available."

6. **Persist wizard mode in config table.** After promotion in step 12, update the mode in a persistent config table that the system reads on startup. Do not rely on mode_history alone.

7. **Implement SQLite-backed wizard state.** Replace cookie-based state with SQLite checkpointing. The code comment acknowledges this. It should be done before production use.

8. **Add credential validation to step 4.** After storing credentials via keyring, make a lightweight API call to each source to verify the credential works. The spec explicitly requires this.

---

## Overall Score: 3/5

**Rationale:** The broker adapter infrastructure (ABC + factory + AlpacaPaperAdapter) is well-architected and matches the spec. The execution service correctly wires adapter, signature verification, and catastrophe-net stops. SSE resume and dead-letter queue are solid. The two critical review findings (C1, C2) have been fixed. However, the wizard implementation has significant spec drift: 12 steps instead of 11, missing smoke-test cycle, wrong embedding model, and no credential validation at step 4. These are not cosmetic -- the missing smoke-test cycle means the system can be promoted to PAPER without ever running a pipeline. The embedding model mismatch will cause runtime failures in vector search. These gaps pull the overall score down from what would otherwise be a 4.

**What works well:** Broker adapter architecture, catastrophe-net wiring, dead-letter persistence, SSE resume, TOTP enrollment, error code fixes.

**What needs work:** Wizard spec compliance (step count, smoke-test, embedding model, credential validation), wizard state persistence, exit test coverage for promotion path.

---

_Reviewed: 2026-05-26T12:40:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
