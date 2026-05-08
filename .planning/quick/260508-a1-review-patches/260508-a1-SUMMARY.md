# Plan 260508-a1: Phase 1 HIGH-Priority Review Patches Summary

**Status:** Complete
**Date:** 2026-05-08

## One-liner

Applied 7 HIGH-priority review patches: property tests, audit integration tests, keychain secret scrubbing, debug_log cycle_id enforcement, FX staleness calendar, currency validator hardening, schema boundary tests.

## Files Created

| File | Description |
|------|-------------|
| `tests/property/test_probabilities.py` | 6 hypothesis-based property tests for probability sums, FX round-trip, freshness immutability, conviction thresholds, arbitrated constraints |
| `tests/integration/test_audit_writing.py` | 5 integration tests verifying state machine transitions emit valid hash-chained audit log entries |

## Files Modified

| File | Patch |
|------|-------|
| `pmacs/storage/keychain.py` | Added `_scrub_secrets()`, `rotate_api_key()`, scrubbed error messages, error code constants, error recovery docstrings |
| `pmacs/logsys/debug_log.py` | Added `SYSTEM_EVENT_TYPES` frozenset (42 event types), enforce `cycle_id` on all non-system events (Architecture.md §5.2) |
| `pmacs/data/fx.py` | Added `_is_ecb_business_day()`, `_last_ecb_business_day()`, `is_rate_stale()` with ECB holiday calendar and Easter computation |
| `pmacs/schemas/currency.py` | Strengthened `_no_eur_per_usd_field` validator: `self.model_fields` -> `self.__class__.model_fields` (deprecation fix), added `model_dump()` belt-and-suspenders check |
| `tests/unit/test_schemas.py` | Added `TestSchemaBoundary` class (import layer isolation), `test_eur_per_usd_field_rejected` in TestCurrency |
| `tests/unit/test_fx.py` | Added `TestEcbStaleness` class with 4 staleness tests (Friday/Saturday, Friday/Sunday, Monday/Wednesday, business day) |

## Test Results

- **786 passed, 11 skipped, 0 failures** (unit + property + integration)
- E2E dashboard tests (8 failures, 8 errors) are pre-existing -- require running server, unrelated to patches
- Baseline was 714 tests; net gain of 72 tests from new property/integration/unit tests

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Critical] Expanded SYSTEM_EVENT_TYPES beyond plan's 5 entries**
- **Found during:** Task 2b (debug_log.py patch)
- **Issue:** Plan listed only 5 event types in SYSTEM_EVENT_TYPES, but source code has 42 distinct system-level events that legitimately call `log_debug()` without `cycle_id` (cortex daemon, boot detector, self-check, kill switch, disk monitor, clock monitor, crash loop detector, model integrity)
- **Fix:** Included all 42 event types actually used in source code. Omitting them would have broken all cortex/boot/health monitoring at runtime.
- **Files modified:** `pmacs/logsys/debug_log.py`
- **Commit:** task 2 commit

**2. [Rule 1 - Bug] Fixed test_eur_per_usd_field_rejected test design**
- **Found during:** Task 3a (test_schemas.py patch)
- **Issue:** Plan specified testing `FxRate.model_validate()` with an extra `eur_per_usd` field, expecting `ValueError`. But Pydantic ignores extra fields by default -- they are never stored in `model_fields` or `model_dump()`, so the validator cannot catch them at `model_validate()` time. The validator is designed to catch field *declarations* in subclasses, not arbitrary input data.
- **Fix:** Rewrote test to verify the actual guard behavior: `eur_per_usd` is not in `model_fields` or `model_dump()`, and the property correctly returns the inverse of `usd_per_eur`.
- **Files modified:** `tests/unit/test_schemas.py`
- **Commit:** task 3 commit

**3. [Rule 1 - Bug] Fixed deprecated `self.model_fields` access in currency.py**
- **Found during:** Task 1 test run (hypothesis tests triggered deprecation warning)
- **Issue:** Pydantic V2.11 deprecates accessing `model_fields` on instances (should use `self.__class__.model_fields`)
- **Fix:** Changed `self.model_fields` to `self.__class__.model_fields` in the validator
- **Files modified:** `pmacs/schemas/currency.py`
- **Commit:** task 2 commit

**4. [Rule 3 - Blocking] Force-added `pmacs/data/fx.py` to git**
- **Found during:** Task 2 commit
- **Issue:** `.gitignore` ignores `pmacs/data` directory. `git add` refused the file.
- **Fix:** Used `git add -f` to force-add the patched file
- **Commit:** task 2 commit

## Commits

1. `test(260508-a1): add property tests and audit integration tests` -- 2 new test files
2. `feat(260508-a1): patch keychain, debug_log, fx, and currency modules` -- 4 source patches
3. `test(260508-a1): patch test_schemas and test_fx with boundary and staleness tests` -- 2 test patches

## Risk Notes - Post-Verification

1. `debug_log.py` cycle_id enforcement: **No breakage.** All source code callers already pass `cycle_id` for cycle-scoped events. System events (42 types) are whitelisted in `SYSTEM_EVENT_TYPES`.
2. `keychain.py` patches: **No breakage.** Additive only. Existing `get_api_key`/`set_api_key` behavior preserved. No tests assert on exact error strings.
3. `currency.py` validator hardening: **No breakage.** `model_dump()` check on frozen model with `eur_per_usd` as `@property` correctly does NOT trigger (property not in dump).

## Self-Check: PASSED

All 9 created/modified files verified present. All 3 commits verified in git log (faccfab, dbc7edb, 32240fa).
