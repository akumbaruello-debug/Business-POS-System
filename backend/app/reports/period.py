"""F.1 — Period alias resolver.

Translates the OpenAPI ``Period`` query parameter (and the optional
``from`` / ``to`` ISO timestamps) into a concrete ``[from_iso, to_iso]``
window. Server-local time per API-Architecture §4.7 line 327 ("The
server is the source of 'now' for any time-based logic").

Alias semantics (matches ``API-Architecture §4.5/§4.7/§12.1``):

================== ============================================
Alias               Resolved window
================== ============================================
``today``           00:00:00 (server) → 23:59:59.999999 (server)
``this_week``       Monday 00:00:00 (server) → Sunday 23:59:59.999999
``this_month``      1st 00:00:00 (server) → last day 23:59:59.999999
``this_year``       Jan 1 00:00:00 (server) → Dec 31 23:59:59.999999
``custom``          Passthrough of explicit ``from`` / ``to``
================== ============================================

Default when no ``period``, no ``from``, no ``to`` is supplied:
``this_month`` (matches the dashboard default at API-Architecture
§12.2 line 1110).

The ``compare_to`` resolver returns a window of the same length as
the primary window, offset backwards (mirrored length — per the
comparison shape in API-Architecture §12.1 line 1092-1103).

No DB access; pure functions over ``datetime`` so they're cheap and
trivially testable.

.. note::

   Inclusivity is enforced at the SQL boundary (``:from_iso <= date
   AND date <= :to_iso``), so the resolver returns the inclusive
   window bounds. The route layer formats the returned datetimes back
   to ISO strings before binding.

ruff: S608 ignored — pure date math, no SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

__all__ = [
    "DEFAULT_PERIOD",
    "format_iso",
    "resolve_compare_to",
    "resolve_period",
    "to_utc_aware",
]

DEFAULT_PERIOD: Final[str] = "this_month"
_VALID_PERIODS: Final[frozenset[str]] = frozenset(
    {"today", "this_week", "this_month", "this_year", "custom"}
)


def _start_of_day(d: datetime) -> datetime:
    """Return ``d`` at 00:00:00.000000 in ``d``'s timezone."""
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _end_of_day(d: datetime) -> datetime:
    """Return ``d`` at 23:59:59.999999 in ``d``'s timezone."""
    return d.replace(hour=23, minute=59, second=59, microsecond=999999)


def _start_of_month(d: datetime) -> datetime:
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _end_of_month(d: datetime) -> datetime:
    """Last day of the month at 23:59:59.999999.

    Pure datetime math (no ``calendar`` import needed): advance to the
    first of next month, subtract one microsecond, then zero-out the
    microsecond overflow back to 999999.
    """
    if d.month == 12:
        next_first = d.replace(year=d.year + 1, month=1, day=1)
    else:
        next_first = d.replace(month=d.month + 1, day=1)
    last = next_first - timedelta(microseconds=1)
    return last.replace(microsecond=999999)


def _start_of_year(d: datetime) -> datetime:
    return d.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _end_of_year(d: datetime) -> datetime:
    return d.replace(
        month=12, day=31, hour=23, minute=59, second=59, microsecond=999999
    )


def _start_of_week(d: datetime) -> datetime:
    """Monday 00:00:00 of the ISO week containing ``d``.

    ``weekday()``: Monday=0, Sunday=6. ISO weeks start on Monday.
    """
    return _start_of_day(d - timedelta(days=d.weekday()))


def to_utc_aware(value: str) -> datetime:
    """Parse an ISO-8601 string into a tz-aware ``datetime``.

    If the string has no offset, assume UTC (the application
    convention; matches ``app.cash_movements.repo._parse_iso``).
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def resolve_period(
    *,
    period: str | None,
    from_iso: str | None,
    to_iso: str | None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Resolve the report's primary ``[from, to]`` window.

    Resolution order (mirrors the dashboard default — explicit
    ``from``/``to`` override the alias):

    1. If ``period`` is set (and is one of the valid aliases), use it.
       ``from``/``to`` are ignored unless ``period == 'custom'``.
    2. If ``from``/``to`` are both set, use them (passthrough).
    3. If only one of ``from``/``to`` is set, raise ``ValueError``
       (caller maps to 400 ``validation_failed``).
    4. Otherwise, default to ``this_month``.

    The returned tuple is ``(start_inclusive, end_inclusive)`` — both
    bounds are tz-aware datetimes. The route layer converts them back
    to ISO strings before binding.
    """
    anchor = now or datetime.now().astimezone()

    # Explicit alias always wins (caller contract).
    if period is not None:
        if period not in _VALID_PERIODS:
            raise ValueError(
                f"invalid period '{period}'; "
                f"expected one of: {sorted(_VALID_PERIODS)}"
            )
        if period == "today":
            return (_start_of_day(anchor), _end_of_day(anchor))
        if period == "this_week":
            start = _start_of_week(anchor)
            end = start + timedelta(days=7) - timedelta(microseconds=1)
            return (start, end)
        if period == "this_month":
            return (_start_of_month(anchor), _end_of_month(anchor))
        if period == "this_year":
            return (_start_of_year(anchor), _end_of_year(anchor))
        # period == "custom" → fall through to from/to
        if from_iso is None and to_iso is None:
            raise ValueError(
                "period=custom requires explicit from and to"
            )

    if from_iso is not None and to_iso is not None:
        return (to_utc_aware(from_iso), to_utc_aware(to_iso))
    if from_iso is not None or to_iso is not None:
        raise ValueError("from and to must both be supplied (or neither)")

    # No period, no from/to → default to this_month.
    return (_start_of_month(anchor), _end_of_month(anchor))


def resolve_compare_to(
    *,
    compare_to: str | None,
    primary_from: datetime,
    primary_to: datetime,
) -> tuple[datetime, datetime]:
    """Resolve the comparison window.

    Per API-Architecture §12.1 line 1092-1103, ``compare_to`` accepts:

    * A period alias (``today``, ``this_week``, ``this_month``,
      ``this_year``) — resolved as if the alias were a primary
      request, then the length is mirrored to match the primary
      window.
    * A ``from,to`` pair (comma-separated ISO timestamps).

    Mirrored length: ``primary_to - primary_from``. The comparison
    window ends exactly one microsecond before ``primary_from`` and
    starts ``length`` earlier.

    The route layer passes the *raw* primary ISO pair as the basis for
    the length; this function is called with the resolved primary
    window.
    """
    if compare_to is None:
        raise ValueError("compare_to is required")

    length = primary_to - primary_from
    if length.total_seconds() < 0:
        raise ValueError("primary window has negative length")

    if compare_to in _VALID_PERIODS:
        # Reuse the period resolver but anchored to the period that
        # just ended (offset by the primary window length so the
        # comparison window ends right before the primary starts).
        anchor = primary_from - timedelta(microseconds=1)
        if compare_to == "today":
            start, end = _start_of_day(anchor), _end_of_day(anchor)
        elif compare_to == "this_week":
            start = _start_of_week(anchor)
            end = start + timedelta(days=7) - timedelta(microseconds=1)
        elif compare_to == "this_month":
            start, end = _start_of_month(anchor), _end_of_month(anchor)
        elif compare_to == "this_year":
            start, end = _start_of_year(anchor), _end_of_year(anchor)
        else:  # compare_to == "custom" → caller must supply explicit pair
            raise ValueError("compare_to=custom requires explicit from,to pair")
        return (start, end)

    # Explicit "from,to" pair.
    if "," in compare_to:
        raw_from, raw_to = compare_to.split(",", 1)
        return (to_utc_aware(raw_from.strip()), to_utc_aware(raw_to.strip()))

    raise ValueError(
        f"invalid compare_to '{compare_to}'; expected period alias or 'from,to'"
    )


def format_iso(value: datetime) -> str:
    """Format a tz-aware ``datetime`` as ISO-8601 with offset.

    Used to format the resolved period bounds for SQL binding and for
    the response's ``comparison.previous_period`` echo.
    """
    if value.tzinfo is None:
        # Defensive: an unz-aware datetime would be silently naive in
        # the SQL bind, which is the most common cause of off-by-TZ
        # reports. Refuse rather than risk it.
        raise ValueError("format_iso requires a tz-aware datetime")
    return value.isoformat()
