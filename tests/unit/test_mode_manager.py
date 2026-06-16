"""Unit tests for mode_manager engine — valid transitions, TOTP gating.

Spec ref: Architecture.md §4.6, Source.md §1
"""
from __future__ import annotations

import pytest

from pmacs.engines.mode_manager import (
    can_transition,
    get_valid_transitions,
    requires_confirmation,
    transition_mode,
)
from pmacs.schemas.system import Mode


class TestCanTransition:
    """Test valid/invalid mode transition lookups."""

    def test_installing_to_shadow(self) -> None:
        assert can_transition(Mode.INSTALLING, Mode.SHADOW) is True

    def test_installing_to_paper(self) -> None:
        assert can_transition(Mode.INSTALLING, Mode.PAPER) is True

    def test_shadow_to_paper(self) -> None:
        assert can_transition(Mode.SHADOW, Mode.PAPER) is True

    def test_paper_to_paper_validated(self) -> None:
        assert can_transition(Mode.PAPER, Mode.PAPER_VALIDATED) is True

    def test_paper_to_shadow_demotion(self) -> None:
        assert can_transition(Mode.PAPER, Mode.SHADOW) is True

    def test_paper_validated_to_live_early(self) -> None:
        assert can_transition(Mode.PAPER_VALIDATED, Mode.LIVE_EARLY) is True

    def test_paper_validated_to_paper_demotion(self) -> None:
        assert can_transition(Mode.PAPER_VALIDATED, Mode.PAPER) is True

    def test_live_early_to_live_standard(self) -> None:
        assert can_transition(Mode.LIVE_EARLY, Mode.LIVE_STANDARD) is True

    def test_live_early_to_paper_validated_demotion(self) -> None:
        assert can_transition(Mode.LIVE_EARLY, Mode.PAPER_VALIDATED) is True

    def test_live_standard_to_live_expanded(self) -> None:
        assert can_transition(Mode.LIVE_STANDARD, Mode.LIVE_EXPANDED) is True

    def test_live_standard_to_live_early_demotion(self) -> None:
        assert can_transition(Mode.LIVE_STANDARD, Mode.LIVE_EARLY) is True

    def test_live_expanded_to_live_standard(self) -> None:
        assert can_transition(Mode.LIVE_EXPANDED, Mode.LIVE_STANDARD) is True

    def test_invalid_installing_to_live(self) -> None:
        assert can_transition(Mode.INSTALLING, Mode.LIVE_EARLY) is False

    def test_invalid_shadow_to_live(self) -> None:
        assert can_transition(Mode.SHADOW, Mode.LIVE_STANDARD) is False

    def test_invalid_paper_to_live_early(self) -> None:
        assert can_transition(Mode.PAPER, Mode.LIVE_EARLY) is False

    def test_invalid_same_mode(self) -> None:
        assert can_transition(Mode.PAPER, Mode.PAPER) is False

    def test_invalid_reverse_ladder_jump(self) -> None:
        assert can_transition(Mode.LIVE_EXPANDED, Mode.PAPER) is False


class TestTransitionMode:
    """Test mode transition execution with TOTP gating."""

    def test_valid_transition_without_totp(self) -> None:
        """Non-LIVE transitions work without TOTP."""
        result = transition_mode(
            Mode.INSTALLING, Mode.SHADOW, reason="Wizard complete"
        )
        assert result.from_mode == Mode.INSTALLING
        assert result.to_mode == Mode.SHADOW
        assert result.reason == "Wizard complete"
        assert result.operator_confirmed is False

    def test_valid_paper_to_shadow_demotion(self) -> None:
        result = transition_mode(
            Mode.PAPER, Mode.SHADOW, reason="Poor performance"
        )
        assert result.to_mode == Mode.SHADOW

    def test_invalid_transition_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid mode transition"):
            transition_mode(Mode.INSTALLING, Mode.LIVE_EARLY, reason="jump")

    def test_live_early_requires_confirmation(self) -> None:
        with pytest.raises(ValueError, match="operator confirmation"):
            transition_mode(
                Mode.PAPER_VALIDATED, Mode.LIVE_EARLY, reason="promotion"
            )

    def test_paper_validated_requires_confirmation(self) -> None:
        """Spec: PAPER → PAPER_VALIDATED requires TOTP."""
        with pytest.raises(ValueError, match="operator confirmation"):
            transition_mode(
                Mode.PAPER, Mode.PAPER_VALIDATED, reason="promotion"
            )

    def test_paper_validated_with_totp_succeeds(self) -> None:
        """PAPER → PAPER_VALIDATED with TOTP succeeds."""
        result = transition_mode(
            Mode.PAPER,
            Mode.PAPER_VALIDATED,
            reason="met criteria",
            operator_confirmed=True,
        )
        assert result.to_mode == Mode.PAPER_VALIDATED
        assert result.operator_confirmed is True

    def test_live_standard_requires_confirmation(self) -> None:
        with pytest.raises(ValueError, match="operator confirmation"):
            transition_mode(
                Mode.LIVE_EARLY, Mode.LIVE_STANDARD, reason="promotion"
            )

    def test_live_expanded_requires_confirmation(self) -> None:
        with pytest.raises(ValueError, match="operator confirmation"):
            transition_mode(
                Mode.LIVE_STANDARD, Mode.LIVE_EXPANDED, reason="promotion"
            )

    def test_live_early_with_totp_succeeds(self) -> None:
        result = transition_mode(
            Mode.PAPER_VALIDATED,
            Mode.LIVE_EARLY,
            reason="promotion",
            operator_confirmed=True,
        )
        assert result.to_mode == Mode.LIVE_EARLY
        assert result.operator_confirmed is True

    def test_live_standard_with_totp_succeeds(self) -> None:
        result = transition_mode(
            Mode.LIVE_EARLY,
            Mode.LIVE_STANDARD,
            reason="promotion",
            operator_confirmed=True,
        )
        assert result.to_mode == Mode.LIVE_STANDARD

    def test_live_expanded_with_totp_succeeds(self) -> None:
        result = transition_mode(
            Mode.LIVE_STANDARD,
            Mode.LIVE_EXPANDED,
            reason="promotion",
            operator_confirmed=True,
        )
        assert result.to_mode == Mode.LIVE_EXPANDED

    def test_auto_demotion_triggered_by(self) -> None:
        result = transition_mode(
            Mode.PAPER,
            Mode.SHADOW,
            reason="kill switch",
            triggered_by="AUTO_DEMOTION",
        )
        assert result.triggered_by == "AUTO_DEMOTION"

    def test_demotion_from_live_does_not_require_totp(self) -> None:
        """Demotion from LIVE modes (backwards) does not require TOTP."""
        result = transition_mode(
            Mode.LIVE_EARLY,
            Mode.PAPER_VALIDATED,
            reason="poor performance",
        )
        assert result.to_mode == Mode.PAPER_VALIDATED
        assert result.operator_confirmed is False


class TestRequiresTotp:
    """Test TOTP requirement check."""

    def test_live_early_requires_confirmation(self) -> None:
        assert requires_confirmation(Mode.LIVE_EARLY) is True

    def test_live_standard_requires_confirmation(self) -> None:
        assert requires_confirmation(Mode.LIVE_STANDARD) is True

    def test_live_expanded_requires_confirmation(self) -> None:
        assert requires_confirmation(Mode.LIVE_EXPANDED) is True

    def test_paper_does_not_require_totp(self) -> None:
        assert requires_confirmation(Mode.PAPER) is False

    def test_shadow_does_not_require_totp(self) -> None:
        assert requires_confirmation(Mode.SHADOW) is False

    def test_installing_does_not_require_totp(self) -> None:
        assert requires_confirmation(Mode.INSTALLING) is False

    def test_paper_validated_requires_confirmation(self) -> None:
        """Spec: PAPER → PAPER_VALIDATED requires TOTP (Source.md line 147)."""
        assert requires_confirmation(Mode.PAPER_VALIDATED) is True


class TestGetValidTransitions:
    """Test listing valid transitions from a mode."""

    def test_installing_transitions(self) -> None:
        transitions = get_valid_transitions(Mode.INSTALLING)
        assert Mode.SHADOW in transitions
        assert Mode.PAPER in transitions

    def test_shadow_transitions(self) -> None:
        transitions = get_valid_transitions(Mode.SHADOW)
        assert transitions == [Mode.PAPER]

    def test_paper_transitions(self) -> None:
        transitions = get_valid_transitions(Mode.PAPER)
        assert Mode.PAPER_VALIDATED in transitions
        assert Mode.SHADOW in transitions

    def test_live_expanded_transitions(self) -> None:
        transitions = get_valid_transitions(Mode.LIVE_EXPANDED)
        assert transitions == [Mode.LIVE_STANDARD]
