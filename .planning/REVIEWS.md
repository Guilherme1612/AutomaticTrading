# Independent Code Review: PMACS Phase Plans

**Reviewer:** Claude (Independent Review)
**Date:** 2026-05-08
**Artifacts Reviewed:**
- `spec/Source.md` — Vision and operator behavior
- `spec/Architecture.md` — Implementation specifics
- `spec/Agents.md` — LLM contracts
- `spec/Phases.md` — Build sequence
- `.planning/phases/phase-1/PLAN.md` — Phase 1 plan (GSD Phase 1 = PMACS Phases 1-2)
- `.planning/phases/phase-2/PLAN.md` — Phase 2 plan (GSD Phase 2 = PMACS Phases 3-4)
- `.planning/phases/phase-8/PLAN.md` — Phase 8 plan (GSD Phase 8 = PMACS Phase 15)

---

## Phase 1: Foundation + Data (GSD Phase 1, PMACS Phases 1-2)

### Dimension 1: Spec Compliance — 4/5

The plan accurately maps to Phases.md Section 2 (Phase 1 and Phase 2) exit tests. All four exit tests from Phase 1 and all four from Phase 2 are reproduced verbatim. The wave structure respects the layer dependency graph from Phases.md Section 4 (Layers 0-2). Schema files match Architecture.md Section 3 repo tree.

**Issues:**
- Task 2.1 lists 22 HoldingStates. Architecture.md Section 8.2 defines 22 states. Correct. However, the plan does not mention the `PHASE1_TIMEOUT` state explicitly in the enum listing, though it is present in Architecture.md Section 8.2. Minor omission in documentation but unlikely to cause an implementation gap since the schema file should be copied from spec.
- Task 2.4 mentions `pmacs/schemas/fundamental.py` but this file does not appear in the Architecture.md Section 3 repo tree. The repo tree shows `pmacs/schemas/fundamental.py` is not listed; fundamentals-related schemas may belong elsewhere. This could create a file that later needs relocation.
- The plan bundles PMACS Phases 1 and 2 into one GSD phase. This is consistent with the CLAUDE.md mapping table. However, the Phases.md spec says Phase 2 depends on Phase 1 being complete. The plan runs them as sequential waves within a single phase, which is acceptable IF the Phase 1 exit tests pass before Wave 5 begins. The plan does not explicitly gate Wave 5 on Phase 1 exit test passage.

### Dimension 2: Completeness — 4/5

**Covered:**
- All Pydantic models from Architecture.md Section 8 schemas (contracts, agents, trade, system, data, freshness, currency, arbitration, pricing, sizing, conviction, portfolio, queue, calibration, lessons, attribution, overrides, flywheel, failure, mutation, memory, stop_loss, reconciliation, sim, catalysts).
- Canonical JSON, audit chain, SQLite init, Keychain wrapper.
- State machine with full transition table.
- All 13 data sources listed in Architecture.md Section 6.1.
- Rate limiting, staleness, FX, corporate actions, universe management.
- Pre-commit hooks with anti-pattern grep checks.

**Missing or under-specified:**
- `pmacs/logsys/replay.py` appears in the Architecture.md Section 3 repo tree but is not listed in any task. This file is referenced for audit replay functionality. Should appear in Task 4.1 (debug logging system) or be explicitly deferred.
- `pmacs/data/resolution/` directory (catalyst_detector.py, earnings_resolver.py, fda_resolver.py, corroboration.py) appears in Architecture.md Section 3 but is not mentioned in any Phase 1 task. These are likely Phase 7+ scope but the plan does not call this out.
- The `notification.toml` config file from Architecture.md Section 3 is not in Task 1.2 (config files). Should be added or explicitly deferred.
- Task 3.3 (SQLite init) mentions `dead_letter` table but `pmacs/logsys/dead_letter.py` is not listed in Task 4.1. The table exists before the code that writes to it, which is fine structurally but should be noted.
- No mention of `pmacs/storage/indexes.py` from the repo tree. Likely a minor oversight since it would be created alongside SQLite init.

### Dimension 3: Security and Safety — 5/5

The plan thoroughly addresses security:

- **Audit chain integrity:** Task 3.2 specifies hash-chained writer with `fsync`, genesis entry, and tamper detection. Matches Architecture.md Section 5.1 exactly.
- **Canonical JSON:** Task 3.1 correctly specifies deterministic serialization with float rounding, NaN rejection, and sort_keys. Matches Architecture.md Section 5.1.
- **Keychain for secrets:** Task 3.4 explicitly uses macOS Keychain, never logs keys. Matches Architecture.md Section 1.3 and anti-pattern Section 16.
- **State machine enforcement:** Task 4.2 implements the single-point-of-mutation pattern. Pre-commit hooks (Task 4.3) grep for direct `holding.state =` outside state_machine.py. Matches Architecture.md Section 1.12 and anti-pattern Section 16.1.
- **Anti-pattern enforcement from Day 1:** Task 4.3 lists all seven anti-pattern grep checks from Architecture.md Section 16 and Phases.md Section 1.4.
- **FX convention:** Task 5.3 enforces `usd_per_eur` (not `eur_per_usd`). Matches anti-pattern Section 16.8.
- **Staleness without mutation:** Task 5.2 returns `FreshnessResult` without mutating packets. Matches anti-pattern Section 16.4.
- **Rate limiting via TokenBucket:** Task 5.1 uses the `BUCKETS["source"].acquire()` pattern. Matches anti-pattern Section 16.3.

The plan respects all Five Non-Negotiables at the foundation level (no LLM code yet, audit chain is structural, local-only by design, operator owns kill switch via TOTP stored in Keychain).

### Dimension 4: Test Coverage — 4/5

**Strengths:**
- Phase 1 exit tests are concrete and binary: schema compilation, audit chain integrity (100 appends + tamper detection), state machine transition coverage, config loading, anti-pattern grep.
- Phase 2 exit tests cover staleness enforcement, FX round-trip, data source integration (10/13 threshold), and rate limiting.
- Task 2.5 creates a dedicated schema compilation test that imports every model.
- Task 4.2 state machine test covers every valid and invalid transition.

**Gaps:**
- No unit test specified for canonical JSON edge cases (NaN rejection, float rounding consistency across platforms, datetime serialization). The audit chain test implicitly covers this but explicit edge-case tests for canonical_json itself would strengthen the guarantee.
- No test for Keychain integration (Task 3.4 mentions round-trip set/get but this is under "Verifies" not a formal test file). Keychain tests are hard to write without a test Keychain, but a test with mocking or a skip-if-no-keychain marker would be valuable.
- No test for error classifier (Task 4.1 creates `error_classifier.py` but no test file is listed for it). Error code validity is mentioned but not as a formal test.
- The rate-limiting test (Phase 2 exit test 4) says "20 rapid calls to Polygon complete without 429" but this requires real API access. No mock-based unit test for TokenBucket behavior is specified.

### Dimension 5: Dependency Ordering — 5/5

The wave structure is well-ordered:

- Wave 1 (scaffolding + config): no dependencies. Correct.
- Wave 2 (schemas): depends on Wave 1. Correct -- schemas import from constants and config.
- Wave 3 (canonical JSON + storage): depends on Wave 2. Correct -- audit writer uses schemas and canonical_json; SQLite init uses schema definitions.
- Wave 4 (logging + state machine + pre-commit): depends on Wave 3. Correct -- state machine emits audit events; logging uses canonical_json.
- Wave 5 (data layer): depends on Wave 4. Correct -- gateway uses config and logsys; staleness uses freshness schema.
- Wave 6 (data sources): depends on Wave 5. Correct -- each source uses gateway and staleness.

No circular dependencies detected. The dependency graph diagram in the plan matches the task descriptions.

### Dimension 6: Risk and Edge Cases — 4/5

**Identified risks:**
- Canonical JSON determinism across platforms (correctly flagged).
- Schema completeness requirement (all schemas compile even for unbuilt engines).
- API key management and Keychain reliability.
- Data source reliability with real API keys.

**Missing risk considerations:**
- **SQLite migration strategy:** Task 3.3 uses `CREATE TABLE IF NOT EXISTS`. No migration path for schema changes between phases. Architecture.md Section 3 references `ops/migrate.py` but the plan does not create it. Schema evolution will be needed as later phases add columns/tables.
- **Audit log file permissions:** The audit log is append-only, but the plan does not specify file permissions (should be 0644 owned by _pmacs_nervous group). This matters for multi-process access patterns.
- **pyproject.toml dependency versioning:** Task 1.1 lists `pydantic>=2.5` but does not pin other critical dependencies (httpx, cryptography, etc.). In a local-only system, unpinned deps are lower risk but should be noted.
- **Thread safety of TokenBucket:** The plan mentions thread safety in passing but does not call it out as a risk. The TokenBucket implementation in Architecture.md Section 6.4 uses threading.Lock, which is correct, but concurrent acquire() calls during high-throughput data fetching could become a bottleneck. Not a blocker, worth noting.

**Overall: 4.2/5**

**Critical Issues: 1**
1. Missing explicit Phase 1 exit test gate before Wave 5 starts. The spec requires Phase 1 complete before Phase 2 begins (Phases.md Section 1.3 "do not advance" rule). The plan runs both as waves in a single GSD phase. A checkpoint between Wave 4 and Wave 5 should be added.

**Recommendations (prioritized):**
1. Add an explicit checkpoint between Wave 4 and Wave 5: run Phase 1 exit tests before proceeding to data layer work.
2. Add `pmacs/logsys/replay.py` stub to Task 4.1 (or explicitly defer to a later phase).
3. Add `notification.toml` to Task 1.2 config file list.
4. Add a unit test file for canonical_json edge cases (NaN, Inf, float rounding, datetime).
5. Add a mock-based TokenBucket unit test independent of API access.
6. Consider creating `ops/migrate.py` stub in this phase, even if it only handles the initial schema.
7. Remove `pmacs/schemas/fundamental.py` from Task 2.4 or confirm its spec justification.

---

## Phase 2: Inference + Processes (GSD Phase 2, PMACS Phases 3-4)

### Dimension 1: Spec Compliance — 4/5

The plan correctly maps to PMACS Phase 3 (inference backend) and Phase 4 (core processes). Exit tests are reproduced from Phases.md Section 2 with high fidelity. The process inventory (8 processes) matches Architecture.md Section 4.1. The TOTP implementation aligns with Architecture.md Section 13 (kill switch disengage) and Section 18 (security model).

**Issues:**
- Task 3.3 (Kill switch) lists 10 triggers from Architecture.md Section 13.1. The plan enumerates them but Architecture.md Section 13 is not fully readable in the excerpt I reviewed. The plan should cite the specific section numbers for each trigger to ensure nothing is missed during implementation.
- Task 5.1 (FastAPI app) mentions "6 streams" for SSE. Architecture.md Section 4.4 lists exactly 6 streams (cycle, agent, decision, trade, mutation, system). Correct.
- The plan creates `pmacs/nervous/auth.py` (Task 5.3) with single-session enforcement. Architecture.md Section 4.5.1 specifies 256-bit tokens, HttpOnly, SameSite=Strict, 24h expiry. The plan mentions all of these. Correct.
- Task 1.1 (PersonaRunner) mentions "three-layer validation pipeline: grammar-constrained output -> Pydantic model_validate -> sanity validator." This matches Agents.md Section 3 exactly. Correct.

**Deviation:**
- Task 1.3 creates a "test grammar" with fields `direction`, `confidence`, `reasoning`. This is a minimal test grammar, not the actual persona grammar structure (which requires `p_up`, `p_flat`, `p_down`, `evidence_ids`). This is fine for testing infrastructure but the test should verify that the three-layer pipeline works with a schema that closely resembles real persona output, not just a toy schema.

### Dimension 2: Completeness — 3/5

**Covered:**
- PersonaRunner base class with retry logic.
- Base sanity validator.
- Test grammar and GBNF infrastructure.
- Model integrity checker.
- llama-server startup script.
- LLM integration test.
- TOTP implementation.
- Ed25519 signing.
- Cortex daemon with heartbeat, kill switch, boot detector, crash loop detector, self-check, clock/disk monitors.
- Execution service stub.
- Nervous orchestrator stub, API with SSE, auth, checkpoint.
- All 8 launchd plist files.
- Install scripts (launchd + pf rules).

**Missing:**
- **`pmacs/cortex/sleep_watch.py`** is listed in Architecture.md Section 3 repo tree and detailed in Section 4.6 but is not in any task. Sleep/wake detection for cycle resume is a specified feature. This is a real gap.
- **`pmacs/cortex/drift.py`** (cross-cycle drift monitoring) appears in Architecture.md Section 3 but has no task. Likely lower priority but should be acknowledged.
- **`pmacs/cortex/flywheel_monitor.py`** appears in Architecture.md Section 3 but has no task. This monitors flywheel health. Should at least be a stub.
- **`pmacs/storage/indexes.py`** from repo tree is not mentioned.
- **`pmacs/engines/memory.py`** (antipattern checker, referenced in Agents.md Section 4) is not created in this phase. It is listed in PMACS Phase 5 scope per Phases.md Section 2, but the Gatekeeper (which depends on it) is also Phase 5. This is consistent but the plan does not call out the dependency.
- No integration test for the **pf rules** (Phase 4 exit test 6: "llama-server process cannot reach external IP"). Task 6.2 creates the install script but no test verifies the actual blocking behavior.
- The plan creates a stub execution service but does not create `pmacs/execution/alpaca_adapter.py` stub or `pmacs/execution/catastrophe_net.py` stub. These are in Architecture.md Section 3. Even stubs should exist for import validation.

### Dimension 3: Security and Safety — 4/5

**Strengths:**
- TOTP implementation uses stdlib only (hmac + hashlib.sha1), no external deps. Correct for RFC 6238.
- Ed25519 signing with strict file permissions (0600). Key stored in Keychain. Matches Architecture.md Section 18.
- Kill switch engagement requires NO TOTP (the safer option). Disengagement requires TOTP + typed reason. Matches Source.md Section 21.6 and Architecture.md Section 13.
- pf rules block inference process from internet. Matches Architecture.md Section 4.1 and Non-Negotiable 4.
- UDS socket for execution with proper ACL (0660, owned by _pmacs_exec). Matches Architecture.md Section 4.3.
- Session token is 256-bit, HttpOnly, SameSite=Strict. Single active session. Matches Architecture.md Section 4.5.1.

**Issues:**
- Task 4.1 (Execution service stub) receives signed TradePlans but does not specify what happens if an UNSIGNED plan arrives. The spec says Ed25519 verification is required. The stub should REJECT unsigned/tampered plans, not just log them. The "Verifies" line says "send tampered plan -> rejected" which is correct, but the task description should be more explicit about rejection behavior.
- Task 3.3 (Kill switch) persists state in SQLite `kill_switch` singleton table. This is correct. But the plan does not mention that the kill switch state must survive process restarts and be checked on Cortex startup. If Cortex crashes and restarts, an engaged kill switch must remain engaged. The SQLite persistence handles this, but it should be called out.
- Task 1.5 (start_inference.sh) stores PID at `/var/db/pmacs/inference.pid`. The plan does not specify PID file permissions or cleanup on crash. A stale PID file could prevent restart.
- Task 5.3 (Auth) mentions "new session invalidates old." This is correct for single-operator but the plan does not mention the security implication: if the operator's session is hijacked (unlikely on localhost but still), the old session is invalidated, which means the operator themselves would notice. This is actually a security feature, not a bug. No action needed.

### Dimension 4: Test Coverage — 3/5

**Covered:**
- LLM integration test (Task 1.6): GBNF-constrained output, Pydantic validation, audit logging, grammar value demonstration, model integrity.
- Kill switch test (Task 3.3): engage/disengage with TOTP, trigger verification.
- Crash loop test (Task 3.5): 5 restarts in 60s triggers BROKEN_CRASH_LOOP.
- Signing test (Task 2.2): sign/verify/tamper-detect.
- Heartbeat test (Task 6.3): process heartbeats and stale detection.
- Cycle stub test (Task 5.2): open/close with SSE emission.

**Gaps:**
- **No test for boot detector logic.** Task 3.4 has a "Verifies" line but no formal test file. Boot detection controls when cycles run; getting it wrong means missed cycles or excessive cycles. This should have at least a unit test with mocked timestamps.
- **No test for sleep/wake detection** (and the feature itself is missing from tasks, as noted above).
- **No test for checkpoint/resume** (Task 5.4 has "Verifies" but no test file listed). Cycle resume after sleep/wake is a critical reliability feature.
- **No test for TOTP window edge cases.** Task 2.1 verifies generate/verify but does not specify tests for boundary conditions (expired code, code from next window, replayed code).
- **No test for SSE reconnection with Last-Event-ID.** Task 5.1 mentions this feature but no test verifies it works.
- **pf rules test is missing.** Phase 4 exit test 6 requires verifying that the inference process cannot reach external IP. No test file is listed.

### Dimension 5: Dependency Ordering — 4/5

The wave ordering is generally correct:

- Wave 1 (Inference) is independent. Correct.
- Wave 2 (Crypto: TOTP + Ed25519) is independent of Wave 1. Correct.
- Wave 3 (Cortex) depends on Wave 2 (TOTP for kill switch disengage). Correct.
- Wave 4 (Execution) depends on Wave 2 (Ed25519 signing). Correct. But it also depends on Wave 3 implicitly -- the execution service is monitored by Cortex. However, the stub execution service does not need Cortex monitoring to function, so this is acceptable.
- Wave 5 (Nervous) depends on Waves 3 and 4. The plan shows dependencies on Cortex (for kill switch checks) and Execution (for trade plan submission). Correct.
- Wave 6 (Ops) depends on all previous waves. Correct.

**Issue:**
- Wave 1 (Inference) and Wave 2 (Crypto) are shown as parallel, but the dependency diagram arrows suggest Wave 2 feeds into Wave 3. This is correct. However, Wave 1 also feeds into Wave 3 (Cortex needs to know about inference health). The diagram does not show this dependency clearly. In practice, the Cortex daemon (Wave 3) monitors the inference process (Wave 1), so Wave 1 should complete before Wave 3 starts. The diagram shows Wave 1 pointing to Wave 3 via a dashed line, which is confusing.

### Dimension 6: Risk and Edge Cases — 4/5

**Identified risks (from plan):**
- GGUF model availability (Qwen3.6-35B may not be available yet). Correctly flagged.
- TOTP timing edge cases. Correctly flagged.
- UDS permissions. Correctly flagged.
- launchd user creation requires admin. Correctly flagged.
- pf rules require root. Correctly flagged.

**Missing risks:**
- **llama-server startup time:** The Qwen3.6-35B model at UD-Q4_K_XL quantization is ~21GB. Loading this into RAM on an M1 Max takes 30-60 seconds. The health check loop in Task 1.5 should account for this extended startup time, not just check for HTTP response.
- **Port conflicts:** Three processes bind to localhost ports (:8080, :8000, :8001). The plan does not specify what happens if these ports are already in use. Should add port-conflict detection to startup scripts.
- **Process startup ordering:** Architecture.md Section 4.1 specifies boot order (inference=1, cortex=2, ..., dashboard=7). The launchd plists in Task 6.1 must encode this ordering. launchd does not natively support dependency ordering; the plan should specify how dependency ordering is achieved (e.g., each process checks for its dependencies before starting).
- **Crash loop false positives:** The crash loop detector triggers at 5 restarts in 60s. During development, restarts are frequent. The plan should specify that crash loop detection can be disabled in development mode or via config.
- **Cortex self-check deadlock:** If both Cortex and self-check are in the same launchd plist group, a Cortex crash that triggers self-check could create a restart loop. The plan correctly separates them as independent plists, but the risk of cascading restarts during system instability is not discussed.

**Overall: 3.7/5**

**Critical Issues: 2**
1. **`pmacs/cortex/sleep_watch.py` is missing entirely.** Sleep/wake detection for cycle resume is specified in Architecture.md Section 4.6 and is essential for the "close lid mid-cycle, resume on wake" behavior described in Source.md Section 22. Without it, cycles lose progress on sleep.
2. **Five test files are referenced in exit tests but not created as tasks** (boot detector, checkpoint/resume, TOTP edge cases, SSE reconnection, pf rules). This means exit tests cannot actually pass without additional undocumented work.

**Recommendations (prioritized):**
1. Add `pmacs/cortex/sleep_watch.py` task to Wave 3. This is specified in Architecture.md Section 4.6 and is required for the day-in-the-life narrative.
2. Add `pmacs/cortex/drift.py` and `pmacs/cortex/flywheel_monitor.py` stubs to Wave 3.
3. Add formal test files for: boot detector, checkpoint/resume, TOTP window boundaries, SSE Last-Event-ID reconnection, pf rule effectiveness.
4. Add `pmacs/execution/alpaca_adapter.py` and `pmacs/execution/catastrophe_net.py` stubs to Wave 4 for import validation.
5. Make the test grammar (Task 1.3) more closely resemble real persona output schema (include p_up/p_flat/p_down/evidence_ids) to better test the three-layer pipeline.
6. Specify process startup ordering mechanism in launchd plists or startup scripts.
7. Add a config flag to disable crash loop detection in development mode.
8. Add port-conflict detection to startup scripts.

---

## Phase 8: Polish / LIVE-READY (GSD Phase 8, PMACS Phase 15)

### Dimension 1: Spec Compliance — 3/5

The plan maps to PMACS Phase 15 in Phases.md Section 2. The exit tests reference the Phase 15 exit tests from Phases.md but with some divergence:

**Matched:**
- Exit test 1: 8 operator workflows in <= 3 clicks (matches Phases.md).
- Exit test 2: 16-ticker cycle <= 3 hours (matches Phases.md).
- Exit test 3: RAM < 50GB (matches Phases.md).
- Exit test 4: Audit chain after 100+ cycles (matches Phases.md).
- Exit test 5: spec_consistency.py passes (matches Phases.md).
- Exit test 6: Backup + restore (matches Phases.md).
- Exit test 7: Accessibility zero critical (matches Phases.md).
- Exit test 8: Toasts/modals/shortcuts (matches Phases.md).

**Divergences:**
- The plan title says "Phase 8" but it covers PMACS Phase 15, which is the final polish phase. This naming is consistent with GSD mapping (CLAUDE.md: GSD Phase 8 = PMACS Phase 15). Correct.
- The plan does NOT include "Copy for Claude Code button on every debug event" which is explicitly listed in Phases.md Section 2 (Phase 15 deliverables). Wave 3 Task 3.8 adds it to the Debug page, but it should be on EVERY debug event in the system, not just the Debug page.
- The plan does NOT include "Cycle compare feature" as a standalone deliverable. Wave 3 Task 3.6 covers it (Agents page cycle compare from Source.md Section 15.9), but this is an Agents-page-specific feature. The spec mentions it as a Phase 15 deliverable generically.
- Missing from the plan: "All empty states, loading states, error states per Source.md Section 13.4" is listed as a Phase 15 deliverable. Wave 2 Task 2.3 creates the component templates, and Wave 3 integrates them, but the plan does not verify that ALL pages have ALL states implemented.
- Missing from the plan: "Notification policy implementation (Source.md Section 13.5)" is listed as a Phase 15 deliverable. Wave 2 Task 2.4 covers it, but the Phase 15 spec entry is more comprehensive than the task description.

### Dimension 2: Completeness — 3/5

**Covered:**
- Ops tools: spec_consistency.py, audit_chain_verify.py, backup_verify.py.
- UI foundation: HTMX, D3, SSE wiring, state components, notifications, Cmd-K palette, keyboard shortcuts.
- Page polish: Dashboard sparklines + time-window, mutation summary, Agents progress bars + Sankey + Math view + cycle compare, Pipeline kanban, Debug copy button.
- Accessibility: aria-labels, reduced-motion, keyboard navigation.
- Performance profiling scripts.
- Documentation (operator runbook).

**Missing:**
- **`ops/verify_isolation.py`** from Architecture.md Section 3 repo tree. This is the runtime process isolation audit tool. Not mentioned anywhere in the plan. This is important for verifying the security model (Non-Negotiable 1: LLMs never sign trades).
- **"Copy for Claude Code" button on every debug event.** Task 3.8 adds it to the Debug page. But Source.md Section 13.4 specifies this button on EVERY error state across ALL pages, not just the Debug page. The plan only adds it to the Debug page expanded events.
- **Dashboard page: portfolio summary card, mode/cycle status card, risk metrics row, active positions table, recent decisions feed, system health card.** The plan focuses on sparklines and mutation summary but does not itemize all 8 sections of the Dashboard spec (Source.md Section 14.1-14.8). Many of these were presumably built in Phase 10 (Dashboard phase), but the Phase 15 plan should verify they all meet the polish standard.
- **Agents page: persona card drawer expansion** (Source.md Section 15.4 describes click-to-expand drawer with full memo, evidence citations, raw output, track record, rolling Brier). The plan adds progress bars and Sankey but does not mention the drawer interaction polish.
- **Pipeline page: single-ticker detail drawer** (Source.md Section 16.6 describes a 60% viewport width drawer with full memo, per-persona memos, historical decisions, position lineage, failure history). Task 3.7 mentions "card detail drawer with failure history" but the full spec has many more sections.
- **Universe page: add ticker modal, bulk actions, index overlay toggle.** Not mentioned in the polish plan at all. Presumably built in Phase 10, but no verification of polish quality.
- **Cortex page: all 6 panels** (audit chain, cross-DB, process status, disk/clock/network, kill switch, model integrity). Not mentioned. Presumably Phase 10, but kill switch panel polish (engage/disengage UX) is critical for operator safety.
- **Settings page: all 13 sections.** Not mentioned. Settings is the most complex page (Source.md Section 20.1-20.13) and contains the Mutation Engine panel, persona management, and all TOTP-gated writes.

### Dimension 3: Security and Safety — 3/5

**Strengths:**
- The plan does not introduce any new security-sensitive code paths. Phase 15 is polish, not new features.
- Cmd-K palette includes "engage kill switch" action (Task 2.5), which is the safer direction.
- Keyboard shortcut Cmd-Shift-K for kill switch engagement with confirmation modal (Task 2.6). This matches Source.md Section 13.6.
- TOTP modal system (Wave 2) is reused across all gated actions.

**Issues:**
- Task 2.1 loads HTMX and D3 from CDN (`https://unpkg.com/htmx.org`, `https://d3js.org/d3.v7.min.js`). **This violates Non-Negotiable 4 (local-only execution).** External CDN requests from the dashboard process create network egress and a dependency on external services. These scripts should be vendored locally. The dashboard process is supposed to be loopback-only with NO egress (Architecture.md Section 4.1).
- Task 2.5 (Cmd-K palette) includes an API endpoint `GET /api/search?q=`. The plan does not specify authentication requirements for this endpoint. If unauthenticated, it could leak ticker names, cycle IDs, and error codes. Should require session token.
- Task 2.4 (notifications) maps SSE events to UI notifications. Kill switch and audit chain failure modals are marked "non-disableable." Correct. But the plan does not specify what happens to pending notifications when the operator is not viewing the dashboard. Since PMACS is local-only and single-operator, this is lower risk, but a notification backlog could accumulate.
- The plan does not verify that the existing pf rules (installed in Phase 4) still block inference egress. The performance profiling scripts (Task 4.4) run network operations to verify connectivity but do not verify that the inference process CANNOT reach external IPs.

### Dimension 4: Test Coverage — 3/5

**Covered:**
- Unit tests for ops tools (spec_consistency, audit_chain_verify, backup_verify).
- Exit test verification table with wave mapping and evidence type.
- "824+ existing tests still pass" regression check.

**Gaps:**
- **No accessibility test automation.** Task 4.1-4.3 implement accessibility features but the only verification is "axe-core scan + manual checklist" in exit test 7. The plan itself notes "axe-core scan requires headless browser (Playwright). If unavailable, manual checklist suffices." For a Phase 15 exit test, relying on manual accessibility verification is insufficient. axe-core integration should be required, not optional.
- **No performance test rigor.** Task 4.4 creates profiling scripts but notes "can't fully validate throughput/RAM without M1 Max + model loaded." The Phase 15 exit test requires a 16-ticker cycle to complete in <= 3 hours. If the test cannot be run during this phase, the exit test cannot actually pass. The plan should specify how to run a synthetic or reduced performance test.
- **No E2E test for operator workflows.** Exit test 1 requires "all 8 workflows from Source.md Section 21 complete in <= 3 clicks." The evidence column says "Manual walkthrough checklist." For a production-ready system, at least a subset of these workflows should have automated E2E tests (e.g., add ticker, override SKIP, review mutation candidate).
- **No test for notification delivery.** Task 2.4 implements the notification system but no test verifies that SSE events actually produce the correct toast/modal behavior.

### Dimension 5: Dependency Ordering — 4/5

Wave ordering is logical:
- Wave 1 (Ops tools): independent. Can run in parallel with other waves. Correct.
- Wave 2 (UI foundation): depends on existing Phase 10 dashboard code. Correct -- HTMX/D3/SSE must be added to the base template before page-specific polish.
- Wave 3 (Page polish): depends on Wave 2. Correct -- each page task uses the infrastructure from Wave 2.
- Wave 4 (Accessibility + perf + docs): depends on Wave 3. Correct -- accessibility audit requires finished pages.

**Issue:**
- The plan says "Waves are sequential (each depends on previous)." But Wave 1 (Ops tools) is explicitly independent of Waves 2-4. The plan also says "Wave 1 tasks are fully parallelizable." This is a minor inconsistency in the execution strategy description. Wave 1 could run concurrently with Wave 2, not before it.

### Dimension 6: Risk and Edge Cases — 3/5

**Identified risks (from plan):**
- No real data yet (UI uses fixtures). Correctly flagged.
- Performance tests are verification tools, not actual benchmarks. Correctly flagged.
- axe-core requires headless browser. Correctly flagged.
- D3 Sankey complexity. Correctly flagged.

**Missing risks:**
- **HTMX/D3 CDN dependency is a security violation and a reliability risk.** If unpkg.com or d3js.org are down, the dashboard does not load. For a system that monitors live positions with real money at stake in LIVE modes, this is unacceptable. This is the highest-severity risk in the entire review.
- **Fixture data staleness.** The plan acknowledges "no real data yet" but does not specify what happens when the system goes live. UI components built against fixture data schemas may break against real data shapes. There should be a fixture-to-real-data migration test.
- **Sankey diagram performance with 7+ evidence sources and 9 personas.** The D3 Sankey rendering could be computationally expensive with the full evidence flow. The plan notes complexity but does not benchmark or set a frame-rate target.
- **Cmd-K palette search performance.** Fuzzy search across all tickers, pages, actions, and audit entries could be slow if the database is large. No indexing strategy is specified.
- **`docs/operator_runbook.md` accuracy.** The plan creates the runbook but does not specify how to keep it in sync with the system. Documentation drift is a common failure mode in long-running systems.

**Overall: 3.2/5**

**Critical Issues: 2**
1. **CDN-loaded HTMX and D3 scripts violate the local-only non-negotiable (Non-Negotiable 4).** These must be vendored locally. The dashboard process has zero egress per Architecture.md Section 4.1. Loading external scripts creates both a security and reliability vulnerability.
2. **The plan covers only a fraction of the page-specific polish needed.** The spec defines 7 pages with 50+ individual UI elements. The plan addresses sparkle-level features for 3-4 pages but does not methodically verify that all 7 pages meet the Source.md spec. This risks shipping with incomplete pages.

**Recommendations (prioritized):**
1. **Vendor HTMX and D3 locally.** Replace CDN URLs with local copies in `pmacs/web/static/vendor/`. This is non-negotiable for a system with zero egress.
2. Create a page-by-page checklist mapping every Source.md Section 14-20 UI element to an implementation status. Verify that ALL elements are either built (Phase 10) or polished (this phase).
3. Add `ops/verify_isolation.py` task to Wave 1 (runtime process isolation audit).
4. Add `GET /api/search` endpoint authentication requirement to Task 2.5.
5. Make axe-core testing mandatory for exit test 7, not optional. Add Playwright to dev dependencies.
6. Add "Copy for Claude Code" button to all error states across all pages, not just Debug page.
7. Add an E2E test for at least 3 of the 8 operator workflows (add ticker, engage/disengage kill switch, review mutation candidate).
8. Specify a frame-rate target for Sankey rendering (e.g., 30fps during animation).
9. Add fixture-to-real-data schema validation test.
10. Add a documentation freshness check to CI (grep for version numbers, date references).

---

## Cross-Phase Concerns

### 1. Phase boundary gate enforcement

Phases.md Section 1.3 states "do not advance" if the exit test fails. The GSD mapping bundles multiple PMACS phases into single GSD phases (e.g., PMACS 1+2 = GSD 1). Neither Phase 1 nor Phase 2 plan has an explicit gate between their internal PMACS sub-phases. This risks starting PMACS Phase 2 data work before PMACS Phase 1 schemas are fully validated.

**Recommendation:** Add an explicit checkpoint in GSD Phase 1 between Wave 4 and Wave 5 (PMACS Phase 1 exit tests must pass before PMACS Phase 2 work begins).

### 2. Test coverage gaps compound across phases

Phase 1 has 5 test gaps, Phase 2 has 5 test gaps. These are mostly "Verifies" lines without formal test files. By Phase 8, the plan notes "824 tests pass" but many exit tests from earlier phases may be passing on manual verification, not automated CI. For a trading system, this is risky.

**Recommendation:** Every "Verifies" line in every task should map to a named test file. Add a test-inventory task to Phase 8 that validates all exit tests from all phases have automated coverage.

### 3. Sleep/wake is specified but never built

Architecture.md Section 4.6 specifies sleep/wake detection. Source.md Section 22 depends on it (lid-close mid-cycle, resume on wake). The feature is missing from Phase 2 and never appears in Phase 8. Without it, a mid-cycle laptop sleep loses all cycle progress.

**Recommendation:** Add sleep/wake detection to Phase 2 Wave 3 as a required task, not an optional stub.

### 4. CDN dependency in the web layer

Phase 8 loads HTMX and D3 from CDN. This violates Non-Negotiable 4 (local-only execution). The dashboard process has zero egress per Architecture.md Section 4.1. This must be fixed before the plan is executed.

**Recommendation:** Vendor all JavaScript dependencies locally.

### 5. Missing ops/verify_isolation.py

This tool is specified in Architecture.md Section 3 and verifies process isolation at runtime (Non-Negotiable 1: LLMs never sign trades). It is not in any phase plan. Without it, process isolation violations (e.g., dashboard writing to SQLite, mutation engine writing to production config) cannot be detected automatically.

**Recommendation:** Add to Phase 2 Wave 6 (ops scripts) or Phase 8 Wave 1 (ops tools).

### 6. Schema migration strategy

Phase 1 creates SQLite tables with `CREATE TABLE IF NOT EXISTS`. Later phases add columns and tables. No migration tool exists. `ops/migrate.py` is in the repo tree but no plan creates it.

**Recommendation:** Add `ops/migrate.py` stub to Phase 1, and ensure every subsequent phase includes a migration task for schema changes.

### 7. Phase 8 scope may be underestimated

Phase 15 in Phases.md has 16 deliverables with an estimated 7-10 day duration. The Phase 8 plan has 25 tasks across 4 waves. This is ambitious. The plan covers the highlights but omits significant portions of the page specs (especially Universe, Cortex, and Settings pages). The risk is that Phase 8 ships incomplete.

**Recommendation:** Create a comprehensive Source.md Section 14-20 checklist before starting Phase 8. Identify which elements are already built (Phase 10) and which need polish. Expand the plan to cover ALL gaps.

### 8. Performance testing feasibility

Phase 8 exit test 2 requires a 16-ticker cycle in <= 3 hours. The plan acknowledges this cannot be verified without the M1 Max running the full model. This is a circular dependency: the performance test requires the system to be fully operational, but the system is not "production-quality" until the performance test passes.

**Recommendation:** Define a reduced performance test that runs on smaller models or fewer tickers with extrapolation. Or accept that the 3-hour target is verified empirically during PAPER mode, not as a Phase 15 gate.

---

*End of review.*
