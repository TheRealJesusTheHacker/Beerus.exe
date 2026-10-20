"""Progress events for a wipe run.

The engine reports what it is actually doing as immutable events.
Callers (CLI today, HUD later) render them; they never invent state
of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EventKind(Enum):
    SCOPE_ANNOUNCED = "scope_announced"
    STEP_STARTED = "step_started"
    PROGRESS = "progress"
    STEP_COMPLETED = "step_completed"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUSED = "refused"


@dataclass(frozen=True)
class ProgressEvent:
    """One honest progress event.

    ``percent`` is ONLY ever derived from the real byte counters on
    this event (``bytes_done`` / ``bytes_total``). It is never
    estimated from elapsed time, never interpolated, and is ``None``
    whenever there is no real total to measure against.
    """

    kind: EventKind
    message: str
    step_label: str | None = None
    bytes_done: int = 0
    bytes_total: int = 0

    @property
    def percent(self) -> float | None:
        if self.bytes_total <= 0:
            return None
        return round(100.0 * self.bytes_done / self.bytes_total, 1)
