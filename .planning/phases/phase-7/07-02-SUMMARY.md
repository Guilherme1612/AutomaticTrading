# Plan 07-02 Summary — t-CDF accuracy, enum, constants, stress tests

## Status: COMPLETE

## Changes

### Task 1: Improve t-CDF accuracy with scipy fallback + stress tests
- `pmacs/mutation/stat_test.py` — Added scipy-optional import:
  - `_HAS_SCIPY` flag, `_t_cdf()` dispatches to scipy when available
  - Renamed old body to `_t_cdf_lentz()` (Lentz continued-fraction fallback)
- `tests/unit/test_stat_test.py` — Added 9 new tests:
  - `TestStatTestAccuracy` — scipy comparison (skipif no scipy), CDF accuracy across df/t ranges
  - `TestStatTestNumericalStability` — 5 stress tests: large values (1e10), small values (1e-10), mixed scale, large-t/small-p, single outlier

### Task 2: Migrate dimensions to MutationDimension enum + centralize constants
- `pmacs/mutation/candidate_generator.py`:
  - Replaced `ACTIVATION_THRESHOLD = 50` with `from pmacs.constants import MUTATION_ACTIVATION_CYCLES`
  - Replaced all string dimension literals ("prompts", "source_weights", "thresholds") with `MutationDimension` enum values
- `pmacs/mutation/daemon.py`:
  - Replaced `ACTIVATION_CYCLE_THRESHOLD = 50` with `from pmacs.constants import MUTATION_ACTIVATION_CYCLES`
- `pmacs/mutation/promotion.py`:
  - Replaced `PROBATION_CYCLES = 30` with `from pmacs.constants import MUTATION_PROBATION_CYCLES`
- `pmacs/mutation/rollback.py`:
  - Replaced `AUTO_ROLLBACK_WINDOW = 50` with `from pmacs.constants import MUTATION_AUTO_ROLLBACK_WINDOW`
- `tests/unit/test_mutation.py`:
  - Removed stale `ACTIVATION_THRESHOLD` and `AUTO_ROLLBACK_WINDOW` imports

## Test Results
- 314 passed, 0 failures (full suite excluding pre-existing fastapi/hypothesis import errors)
- 20 stat_test tests (including 9 new stress/accuracy tests)
- 44 mutation unit tests
- 28 integration tests
