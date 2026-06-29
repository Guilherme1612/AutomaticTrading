"""Unit test pinning spec/Phases.md Phase 16 exit test #7 regression contract.

The `_log_call_billing` hook in `pmacs/nervous/orchestrator.py` (line 3806)
must be **observability-only** — it must NOT modify the persona's
`DirectionalProbability` (or any other output field).

The hook reads `runner._last_call_usage` (a side-channel attribute set on
`PersonaRunner.__init__` at base.py:94) and writes to DuckDB + SQLite.
This test pins the surface-level invariant that the billing layer and the
persona-output layer are decoupled:

1. `_last_call_usage` is an instance attribute on PersonaRunner, set in __init__.
2. `_last_call_usage` is NOT a field on any persona output Pydantic model
   (DirectionalProbability, PersonaOutput, the per-persona Output models).
3. `DirectionalProbability` is `frozen=True` (model_config), so even if
   someone tried to mutate it after construction, Pydantic would raise.

Together these three invariants make it structurally impossible for the
billing hook to mutate persona output: the billing layer touches a separate
side-channel attribute, and the output model is frozen.
"""

from __future__ import annotations

import inspect

from pmacs.agents.base import PersonaRunner
from pmacs.schemas.agents import DirectionalProbability, PersonaOutput


def test_last_call_usage_is_side_channel_on_runner_not_in_output():
    """Phase 16 exit test #7 — billing is observability-only.

    Pins the structural invariant: `_last_call_usage` lives on the runner
    (side-channel) and is NOT a field on the persona output schema.
    """
    # 1. _last_call_usage is initialized in PersonaRunner.__init__
    init_src = inspect.getsource(PersonaRunner.__init__)
    assert "_last_call_usage" in init_src, (
        "_last_call_usage must be initialized in PersonaRunner.__init__ "
        "(base.py:94). If removed, _log_call_billing loses its data source."
    )

    # 2. The field name does NOT appear on DirectionalProbability
    dp_fields = set(DirectionalProbability.model_fields.keys())
    assert "_last_call_usage" not in dp_fields, (
        "_last_call_usage leaked into DirectionalProbability schema — "
        "billing side-channel must not be part of persona output."
    )
    assert "last_call_usage" not in dp_fields

    # 3. The field name does NOT appear on PersonaOutput base class
    po_fields = set(PersonaOutput.model_fields.keys())
    assert "_last_call_usage" not in po_fields
    assert "last_call_usage" not in po_fields

    # 4. Sanity: the canonical fields ARE present (catches accidental schema rewrites)
    for required in ("persona", "ticker", "p_up", "p_flat", "p_down"):
        assert required in dp_fields, f"Required field {required!r} missing from DirectionalProbability"
    for required in ("persona", "ticker", "cycle_id", "raw_output"):
        assert required in po_fields, f"Required field {required!r} missing from PersonaOutput"


def test_directional_probability_is_frozen():
    """Pin the second invariant: DirectionalProbability is frozen, so even
    a bug introducing a side-channel mutation would raise ValidationError."""
    # model_config is on the class (Pydantic v2 ConfigDict is a TypedDict)
    cfg = getattr(DirectionalProbability, "model_config", None)
    assert cfg is not None, "DirectionalProbability must define model_config"
    assert cfg.get("frozen") is True, (
        "DirectionalProbability must be frozen=True so _log_call_billing "
        "cannot mutate persona output even by accident."
    )


def test_per_persona_output_schemas_are_frozen_and_clean():
    """Every persona output schema inherits the same guarantees."""
    from pmacs.schemas.personas import (
        AuditorOutput,
        BullAdvocateOutput,
        BearAdvocateOutput,
        CatalystSummarizerOutput,
        CrucibleOutput,
        ForensicsOutput,
        GrowthHunterOutput,
        InsiderActivityOutput,
        MacroRegimeOutput,
        MemoWriterOutput,
        MoatAnalystOutput,
        ShortInterestOutput,
        ValuationAgentOutput,
    )

    all_persona_outputs = [
        MacroRegimeOutput,
        CatalystSummarizerOutput,
        MoatAnalystOutput,
        GrowthHunterOutput,
        InsiderActivityOutput,
        ShortInterestOutput,
        ForensicsOutput,
        CrucibleOutput,
        MemoWriterOutput,
        BullAdvocateOutput,
        BearAdvocateOutput,
        AuditorOutput,
        ValuationAgentOutput,
    ]

    for model in all_persona_outputs:
        cfg = getattr(model, "model_config", None)
        # At minimum, no _last_call_usage field on any of them
        fields = set(model.model_fields.keys())
        assert "_last_call_usage" not in fields, (
            f"{model.__name__} leaks _last_call_usage — billing side-channel "
            f"must not appear on any persona output schema."
        )
        assert "last_call_usage" not in fields
        # And the schema is frozen (same invariant as DirectionalProbability).
        # Pydantic v2's ConfigDict is a TypedDict, so just dict-style access.
        if cfg is not None:
            assert cfg.get("frozen") is True, (
                f"{model.__name__} must be frozen=True to prevent mutation."
            )
