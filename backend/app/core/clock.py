"""Clock abstraction.

Why an abstraction? Two reasons:

1. **Testability.** Tests can inject a fixed clock to make
   ``updated_at`` / ``expires_at`` deterministic, and to assert
   "exactly 15 minutes" without sleeping.
2. **Consistency.** All time reads in the codebase go through a single
   point, which makes it trivial to switch the time source later
   (e.g. for distributed tracing).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """A minimal clock interface — return the current UTC instant."""

    def now(self) -> datetime: ...


class SystemClock:
    """Default clock — returns the real wall-clock UTC time."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


def utcnow() -> datetime:
    """Convenience: the system clock's current UTC time."""
    return SystemClock().now()


# A module-level holder so tests can swap the clock without DI plumbing.
# (We don't use lru_cache here because we want the override to be sticky.)
_active_clock: Clock = SystemClock()


def get_clock() -> Clock:
    """Return the currently active clock (defaults to SystemClock)."""
    return _active_clock


def set_clock(clock: Clock) -> Clock:
    """Replace the active clock. Returns the previous clock.

    Intended for tests only.
    """
    global _active_clock
    previous = _active_clock
    _active_clock = clock
    return previous


def reset_clock() -> None:
    """Reset the clock back to SystemClock. Tests call this in teardown."""
    global _active_clock
    _active_clock = SystemClock()
