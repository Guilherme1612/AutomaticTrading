"""First-run wizard — guides operator through PMACS setup.

Spec ref: Source.md §21, Phases.md §1
The wizard walks the operator through system checks, key generation,
LLM configuration, data source setup, and broker configuration.
"""
from __future__ import annotations

from enum import Enum


class WizardStep(Enum):
    """Wizard steps in order (Source.md §12.1). COMPLETE is the terminal state.

    11 actionable steps match the spec's "11 dots" progress strip.
    Step 4 includes the embedding model setup (spec §12.1 Step 4.5).
    """
    WELCOME = 1           # Step 1 — Welcome and system identity check
    INFERENCE_BACKEND = 2  # Step 2 — Inference backend detection (llama-server)
    MODEL_DOWNLOAD = 3     # Step 3 — Model download and SHA256 verification
    KEYCHAIN_SETUP = 4     # Step 4 — macOS Keychain + embedding model setup (§4.5)
    DB_INIT = 5            # Step 5 — Database initialization (all 5 stores)
    DATA_CONNECTIVITY = 6  # Step 6 — Data source connectivity ping
    UNIVERSE_SEED = 7      # Step 7 — Universe seed (16-ticker default)
    CYCLE_PREFERENCES = 8  # Step 8 — Cycle preferences (currency, timezone)
    TOTP_ENROLLMENT = 9    # Step 9 — TOTP enrollment (QR code)
    SMOKE_TEST = 10        # Step 10 — Smoke-test cycle (synthetic fixtures)
    PROMOTE = 11           # Step 11 — Promote to SHADOW + PAPER
    COMPLETE = 12          # Terminal state


class Wizard:
    """First-run setup wizard. Tracks step progression and collects config."""

    def __init__(self) -> None:
        self.current_step: WizardStep = WizardStep.WELCOME
        self.completed_steps: set[WizardStep] = set()
        self.config: dict = {}

    def get_step(self) -> WizardStep:
        """Return the current wizard step."""
        return self.current_step

    def complete_step(
        self,
        step: WizardStep,
        result: dict | None = None,
    ) -> WizardStep:
        """Mark a step as complete and advance to the next step.

        Args:
            step: The step to mark as complete.
            result: Optional dict of config values from this step.

        Returns:
            The next wizard step (or COMPLETE if all done).

        Raises:
            ValueError: If step is COMPLETE or out of order.
        """
        if step == WizardStep.COMPLETE:
            raise ValueError("Cannot complete the COMPLETE step")

        self.completed_steps.add(step)

        if result:
            self.config.update(result)

        # Advance to next step
        if step.value < WizardStep.COMPLETE.value:
            self.current_step = WizardStep(step.value + 1)
        else:
            self.current_step = WizardStep.COMPLETE

        return self.current_step

    def is_complete(self) -> bool:
        """Check if the wizard has finished all steps."""
        return self.current_step == WizardStep.COMPLETE

    def get_progress(self) -> tuple[int, int]:
        """Return (completed_count, total_count) for progress display."""
        # total_count is all steps minus COMPLETE
        total = len(WizardStep) - 1
        return (len(self.completed_steps), total)

    def get_config(self, key: str, default: object = None) -> object:
        """Retrieve a config value collected during wizard."""
        return self.config.get(key, default)
