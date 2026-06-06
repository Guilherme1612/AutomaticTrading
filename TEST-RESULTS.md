# PMACS Test Suite Results

**Date**: 2026-05-31
**Duration**: 464.25s (7m 44s)
**Runner**: `uv run pytest tests/ --tb=no -q`

## Summary

| Metric    | Count |
|-----------|-------|
| Total     | 2173  |
| Passed    | 2162  |
| Failed    | 2     |
| Skipped   | 9     |
| Warnings  | 2     |

**Pass rate**: 99.5%

## Failures (2)

### 1. `tests/integration/test_wizard.py::TestWizardStatusRoute::test_status_returns_json`

- **Assert**: `data["total_steps"] == 12` got `11`
- **Cause**: Wizard step count mismatch. Test expects 12 total steps but the wizard reports 11. Likely a step was added or removed without updating the test.

### 2. `tests/integration/test_wizard.py::TestVerifyLLMStep::test_verify_llm_fails_when_server_down`

- **Assert**: `"not running" in result["message"].lower() or "install" in result["message"].lower()`
- **Actual message**: `"Binary found at /opt/homebrew/bin/llama-server, but nothing is listening on 127.0.0.1:8080."`
- **Cause**: Test expects the error message to contain "not running" or "install", but the actual message says "nothing is listening on". The verifier finds the binary but detects no listener, producing a different wording than the test anticipates.

## Warnings (2)

1. `DeprecationWarning` in `websockets.legacy` (websockets 14.0 deprecation)
2. `RuntimeWarning` in `test_stop_poller.py` — unawaited coroutine in `AsyncMockMixin._execute_mock_call`

## Skipped Tests (9)

Skipped tests are spread across the suite (appeared early in test output). No failures hidden behind skips.
