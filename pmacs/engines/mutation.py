"""Mutation engine entry point (Architecture.md §10).

Implementation lives in pmacs.mutation.*. This module re-exports
the public API for spec-compliant import paths.
"""

from pmacs.mutation.daemon import MutationDaemon
from pmacs.mutation.candidate_generator import generate_candidates
from pmacs.mutation.rollback import execute_rollback, regression_detected
from pmacs.mutation.promotion import operator_promote
from pmacs.mutation.stat_test import welch_t_test

__all__ = [
    "MutationDaemon",
    "generate_candidates",
    "execute_rollback",
    "regression_detected",
    "operator_promote",
    "welch_t_test",
]
