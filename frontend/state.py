"""In-memory pipeline running state.

Resets on server restart. One pipeline at a time enforced by is_running() check.
"""

from enum import StrEnum

# ---------------------------------------------------------------------------
# Pipeline running flag
# ---------------------------------------------------------------------------

_pipeline_running: bool = False


def is_running() -> bool:
    return _pipeline_running


def set_running(value: bool) -> None:
    global _pipeline_running
    _pipeline_running = value


# ---------------------------------------------------------------------------
# Feed status enum
# ---------------------------------------------------------------------------


class FeedStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    RUNNING = "running"
    ERROR = "error"
    DONE = "done"
