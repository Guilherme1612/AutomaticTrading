"""Compatibility re-export — moved to pmacs.cortex.stop_loss_daemon (Architecture.md §3)."""

from pmacs.cortex.stop_loss_daemon import *  # noqa: F401,F403
from pmacs.cortex.stop_loss_daemon import (  # noqa: F401
    _write_stop_trigger,
    check_holding,
    is_rth,
)
