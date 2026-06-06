---
phase: 04-code-review-tests
reviewed: 2026-06-06T20:11:00Z
depth: standard
files_reviewed: 41
files_reviewed_list:
  - .gitignore
  - .pre-commit-config.yaml
  - MISSING.md
  - SECURITY.md
  - pmacs/storage/audit.py
  - pmacs/storage/duckdb.py
  - pmacs/storage/kuzu.py
  - pmacs/storage/qdrant.py
  - pmacs/storage/sqlite.py
  - pyproject.toml
  - spec/Phases.md
  - tests/accessibility/test_a11y.py
  - tests/e2e/test_agents_page.py
  - tests/e2e/test_dashboard_page.py
  - tests/e2e/test_dashboard_renders.py
  - tests/e2e/test_settings_page.py
  - tests/integration/test_cycle_skeleton.py
  - tests/integration/test_data_sources.py
  - tests/integration/test_gatekeeper.py
  - tests/integration/test_llm_call.py
  - tests/integration/test_paper_trade.py
  - tests/integration/test_phase10_exit.py
  - tests/integration/test_qdrant.py
  - tests/integration/test_schema_migration.py
  - tests/integration/test_storage_adapters.py
  - tests/integration/test_wizard.py
  - tests/performance/test_backup_restore.py
  - tests/property/test_probabilities.py
  - tests/unit/test_arbitration.py
  - tests/unit/test_backup_verify.py
  - tests/unit/test_broker_adapter.py
  - tests/unit/test_conviction.py
  - tests/unit/test_crucible_memo.py
  - tests/unit/test_gatekeeper.py
  - tests/unit/test_kill_switch.py
  - tests/unit/test_promotion_gates.py
  - tests/unit/test_schemas.py
  - tests/unit/test_spec_consistency.py
  - tests/unit/test_stop_poller.py
  - tests/unit/test_web_data.py
  - tests/unit/test_web_routes.py
findings:
  critical: 2
  warning: 7
  info: 6
  total: 15
status: issues_found
---

# Phase 4: Code Review Report

**Reviewed:** 2026-06-06T20:11:00Z
**Depth:** standard
**Files Reviewed:** 41
**Status:** issues_found

## Summary

Reviewed 41 files across storage layer, test suite, security documentation, and build configuration. The diff represents significant progress: MISSING.md shows 59 items resolved (MISSING: 38 -> 0), storage adapters gained real-mode operation with graceful degradation, and test coverage expanded massively (+3956/-1169 lines).

Two Critical findings in the storage layer: the audit log rotation has a file descriptor management bug that can lose audit entries (hash chain integrity at risk), and the Qdrant adapter uses module-level global singletons shared across instances (cross-instance state contamination). Seven Warnings span test isolation issues, a flaky test pattern, and a missing pre-commit anti-pattern check. The SECURITY.md is thorough and well-structured with accurate pass/fail assessments.

## Critical Issues

### CR-01: Audit log rotation truncates file without closing the held file descriptor

**File:** `pmacs/storage/audit.py:56-90`
**Issue:** The `_maybe_rotate()` method is called inside `_open()` on the first `append()`. The rotation reads the entire file into gzip (lines 84-86), then truncates via `self._path.write_text("")` (line 89). However, it does NOT close or nullify `self._fd` before truncation. If the file descriptor was open from a prior call cycle (open -> close -> re-open path), the stale fd's file position is invalid after truncation. More critically, between the gzip read and the truncation, another `append()` call in a concurrent context could write a line that is lost -- not in the gzip, not in the truncated file. This TOCTOU race can break the hash chain, violating Non-Negotiable #3.

The fix must ensure: (1) `_fd` is closed and nulled before rotation reads the file, and (2) `_open()` re-opens after rotation since the file was truncated.

**Fix:**
```python
def _maybe_rotate(self) -> None:
    """Rotate log file if it exceeds MAX_LOG_BYTES."""
    # Close any existing fd before touching the file
    if self._fd is not None:
        self._fd.flush()
        self._fd.close()
        self._fd = None
    if not self._path.exists():
        return
    try:
        size = self._path.stat().st_size
    except OSError:
        return
    if size < MAX_LOG_BYTES:
        return
    # ... rotation logic unchanged after this point ...
```

### CR-02: Storage adapters share module-level global singletons across all instances

**File:** `pmacs/storage/qdrant.py:20-23,64-108`, `pmacs/storage/duckdb.py:17,43-64`
**Issue:** Module-level globals (`_qdrant_available`, `_qdrant_client`, `_duckdb_available`) are shared across ALL adapter instances in the same process. If instance A triggers an `ImportError` and sets `_duckdb_available = False`, every subsequent DuckDBAdapter instance is permanently blocked from connecting -- even with different parameters. Similarly, `_qdrant_client` is declared `global` on line 66 of `qdrant.py` but only assigned to `self._client` on line 89, creating confusion about which reference is authoritative. The process-wide poison flag is a latent bug in long-running processes like pmacs-nervous: if any early code path triggers a failed connection, the adapter is stuck in stub mode for the rest of the process lifetime.

The same pattern exists in `pmacs/storage/kuzu.py` (global `_kuzu`, `_kuzu_available`).

**Fix:** Move availability flags and client references to instance attributes:
```python
class QdrantAdapter:
    def __init__(self, url=None, path=None):
        self.url = url
        self._path = path
        self._client = None
        self._collections_created = False
        self._available = None  # instance-level, not global

    def _ensure_client(self) -> bool:
        if self._available is False:
            return False
        if self._client is not None:
            return True
        # ... connect, set self._available and self._client ...
```

## Warnings

### WR-01: Wizard test writes to and resets real config/model_registry.json

**File:** `tests/integration/test_wizard.py:100-166`
**Issue:** `test_step_10_llm_provider_*` tests POST to the wizard endpoint, which writes to the real `config/model_registry.json` on disk. The `_reset_model_registry()` cleanup method (lines 100-159) only runs if the test body succeeds -- if the test crashes or is interrupted by Ctrl-C, the config file is left in a modified state. Additionally, the hardcoded "default" config (lines 107-158) will drift from the actual file over time as backends are added. This is a test isolation failure that can corrupt the developer's working config.

**Fix:** Use `monkeypatch` to redirect the config file path to `tmp_path`, or read the current file before the test and restore it in teardown:
```python
def test_step_10_llm_provider_local_default(self, client, tmp_path, monkeypatch):
    # Redirect registry writes to temp dir
    monkeypatch.setattr("pmacs.web.routes.wizard.CONFIG_DIR", tmp_path)
    form_data = {"provider": "llama_server"}
    resp = client.post("/wizard/step/10", data=form_data)
    assert resp.status_code == 200
```

### WR-02: E2E dashboard tests use overly permissive "or Welcome to PMACS" fallback

**File:** `tests/e2e/test_dashboard_page.py:24-165`
**Issue:** Nearly every assertion in `TestDashboardPage` has been relaxed to accept `or "Welcome to PMACS" in resp.text`. This means a regression that replaces the full dashboard with the welcome page would pass every test. The tests no longer verify that the dashboard renders correctly when data exists -- they only verify the page returns 200 with *some* content. The `_has_data()` helper (line 24) exists but is never used in assertions.

**Fix:** Split into two test classes: one that seeds a fresh empty DB and verifies the welcome state, and another that seeds data via fixtures and verifies the full dashboard renders with expected elements. Use the existing `_has_data()` helper to branch assertions.

### WR-03: Accessibility test uses `time.sleep(1)` for server readiness -- flaky on slow CI

**File:** `tests/accessibility/test_a11y.py:371`
**Issue:** The `base_url` fixture starts a uvicorn server in a daemon thread and then does `time.sleep(1)` to wait for readiness. On slow CI machines, 1 second may not be enough. No health check is performed to confirm the server is actually accepting connections.

**Fix:** Poll the health endpoint in a loop with a timeout:
```python
import httpx
for _ in range(20):
    try:
        httpx.get(f"{server_url}/", timeout=0.5)
        break
    except (httpx.ConnectError, httpx.TimeoutException):
        time.sleep(0.25)
else:
    pytest.skip("Server did not start within 5s")
```

### WR-04: Pre-commit hook for `no-json-dumps-audit` only checks audit.py

**File:** `.pre-commit-config.yaml:24-29`
**Issue:** The `no-json-dumps-audit` hook only checks `pmacs/storage/audit.py` for `json.dumps`. The anti-pattern (Architecture.md S16) prohibits `json.dumps(payload)` for audit payloads anywhere in the codebase. A developer could add `json.dumps` to an audit call in `orchestrator.py` or `state_machine.py` and the hook would not catch it.

**Fix:** Broaden the grep to check all `pmacs/` files for audit-context `json.dumps`:
```yaml
entry: 'bash -c "grep -rn ''json\.dumps.*payload'' --include=''*.py'' pmacs/ | grep -v ''canonical_json'' | grep -v ''dead_letter'' | grep -v ''# '' && exit 1 || exit 0"'
```

### WR-05: SQLite budget_state migration seeds "today" with current date -- becomes stale

**File:** `pmacs/storage/sqlite.py:407-422`
**Issue:** The migration seeds `budget_state` with rows for "today" and "this_month" using the date at migration time. If the database is opened on a subsequent day, the "today" row has yesterday's date. The `period_start` column being stale could cause budget accounting errors if the engine compares it against the current date to determine the active period.

**Fix:** Add logic to refresh stale period rows on init:
```python
# Refresh "today" period_start if stale
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
row = conn.execute("SELECT period_start FROM budget_state WHERE period = 'today'").fetchone()
if row and row[0] != today:
    conn.execute(
        "UPDATE budget_state SET period_start = ?, updated_at = ? WHERE period = 'today'",
        [today, datetime.now(timezone.utc).isoformat()],
    )
```

### WR-06: Spec consistency test weakened -- no longer verifies section titles

**File:** `tests/unit/test_spec_consistency.py:62-70`
**Issue:** The old `TestParseSectionNumbers` tests verified exact section number-to-title mapping (e.g., `{14: "Page: Dashboard"}`). The new `TestParseSections` only checks that keys exist (`assert "14" in result`), losing the title validation. A section could be renamed or reordered without detection.

**Fix:** If `parse_sections` returns section titles, verify them. If not, at minimum verify that the expected section count matches:
```python
def test_extracts_numbered_sections(self):
    text = "## 14. Page: Dashboard\nSome content\n## 15. Page: Agents\n"
    result = parse_sections(text)
    assert "14" in result
    assert "15" in result
    assert len(result) == 2  # Catch missing or extra sections
```

### WR-07: Backup verify test removed `do_e2e` import and replaced with manual round-trip

**File:** `tests/unit/test_backup_verify.py:74-77, 224-237`
**Issue:** The test removed the import of `do_e2e` and replaced it with a manual backup-wipe-restore-verify sequence. This is actually a better test pattern (more explicit), but the `do_e2e` function in `ops/backup_verify.py` is now untested. If `do_e2e` has a bug, it will only be caught in production, not in CI.

**Fix:** Add a dedicated test for `do_e2e` that verifies the full automated round-trip, or remove the `do_e2e` function from `ops/backup_verify.py` if it is no longer used anywhere.

## Info

### IN-01: .gitignore has duplicate `data/` entry

**File:** `.gitignore:29, 45`
**Issue:** `data/` is listed twice (line 29 and line 45). Functionally harmless but confusing.
**Fix:** Remove the duplicate entry at line 45 or merge the two sections.

### IN-02: pyproject.toml has duplicate pytest dependencies with conflicting version pins

**File:** `pyproject.toml:29, 56-57`
**Issue:** `pytest` appears in both `[project.optional-dependencies] dev` (line 29, `>=8.0`) and `[dependency-groups] dev` (line 56, `>=9.0.3`). Same for `pytest-asyncio` (`>=0.23` vs `>=1.3.0`). This causes version confusion depending on which resolution mechanism is used.
**Fix:** Pick one location and remove the other. Remove the `[dependency-groups]` section if not using a tool that supports PEP 735.

### IN-03: KuzuDB changes are only noqa comment additions

**File:** `pmacs/storage/kuzu.py:248-557`
**Issue:** All 5 KuzuDB changes add `# noqa: ...` comments to `pass` statements in exception handlers. Good practice, but the underlying bare `except ... pass` pattern remains, which could silently swallow important errors.
**Fix:** Consider logging at DEBUG level instead of bare `pass` for observability.

### IN-04: spec/Phases.md changes are whitespace-only

**File:** `spec/Phases.md:602-657`
**Issue:** All 6 changed lines are trailing whitespace removal. No functional content changes.
**Fix:** No action needed.

### IN-05: Test migration from real APIs to mocks is a significant quality improvement

**File:** `tests/integration/test_data_sources.py`, `tests/integration/test_llm_call.py`
**Issue:** (Positive observation.) The test suite migrated from requiring real API keys and a running llama-server to fully mocked HTTP layers. This removes CI dependency on external services and makes tests deterministic. 69 mock-based data source tests replace the previous 10/13 real-API approach. The LLM tests now mock the three-layer pipeline (grammar -> Pydantic -> sanity) with controlled outputs.
**Fix:** N/A.

### IN-06: SECURITY.md is comprehensive and accurate

**File:** `SECURITY.md`
**Issue:** (Positive observation.) The security audit is thorough: 25 findings with concrete evidence, correct remediation advice, and accurate status tracking. Three previously reported findings are correctly marked as FIXED with verification details. The HIGH finding (SEC-HIGH-02: OpenRouter cloud routing) is accurately identified as a config violation of Non-Negotiable #4. The anti-pattern compliance table (15/15 PASS) is well-evidenced.
**Fix:** N/A.

---

_Reviewed: 2026-06-06T20:11:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
