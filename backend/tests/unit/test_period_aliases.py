"""Unit tests: period alias window math (app.reports.period).

Covers resolve_period / resolve_compare_to window lengths for every
supported alias. Regression guard for the this_week end bound bug
where the window extended 7 days past the anchor instead of ending at
the anchor's own Sunday.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.reports.period import (
    format_iso,
    resolve_compare_to,
    resolve_period,
)

# Fixed, tz-aware anchors spanning week positions (Monday..Sunday).
_MON = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)   # Monday
_SUN = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)  # Sunday
_WED = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)   # Wednesday


def _length_days(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 86400.0


@pytest.mark.parametrize(
    ("anchor", "expect_start", "expect_end"),
    [
        # Monday anchor: same day is start of week.
        (_MON, datetime(2026, 9, 7, 0, 0, 0, tzinfo=timezone.utc),
         datetime(2026, 9, 13, 23, 59, 59, 999999, tzinfo=timezone.utc)),
        # Sunday anchor: week already ending on this Sunday.
        (_SUN, datetime(2026, 9, 7, 0, 0, 0, tzinfo=timezone.utc),
         datetime(2026, 9, 13, 23, 59, 59, 999999, tzinfo=timezone.utc)),
        # Wednesday anchor: mid-week, still Monday..Sunday window.
        (_WED, datetime(2026, 9, 7, 0, 0, 0, tzinfo=timezone.utc),
         datetime(2026, 9, 13, 23, 59, 59, 999999, tzinfo=timezone.utc)),
    ],
)
def test_this_week_window_is_always_monday_to_sunday(
    anchor: datetime, expect_start: datetime, expect_end: datetime
) -> None:
    start, end = resolve_period(
        period="this_week", from_iso=None, to_iso=None, now=anchor
    )
    assert start == expect_start
    assert end == expect_end
    assert _length_days(start, end) == pytest.approx(7.0, abs=1e-6)


@pytest.mark.parametrize("anchor", [_MON, _SUN, _WED])
def test_this_week_is_7_days_from_any_anchor(anchor: datetime) -> None:
    start, end = resolve_period(
        period="this_week", from_iso=None, to_iso=None, now=anchor
    )
    assert _length_days(start, end) == pytest.approx(7.0, abs=1e-6)


@pytest.mark.parametrize("period", ["today", "this_month", "this_year"])
def test_static_period_lengths(period: str) -> None:
    """today=1d, this_month ~1 month, this_year=365d from any anchor."""
    start, end = resolve_period(
        period=period, from_iso=None, to_iso=None, now=_WED
    )
    days = _length_days(start, end)
    if period == "today":
        assert days == pytest.approx(1.0)
    elif period == "this_month":
        assert 27.0 <= days <= 32.0
    else:  # this_year
        assert days == pytest.approx(364.9999, abs=1e-2)


@pytest.mark.parametrize("anchor", [_MON, _SUN, _WED])
def test_compare_to_this_week_has_same_length_as_primary(
    anchor: datetime,
) -> None:
    """compare_to window mirrors the primary length (mirrored-length rule)."""
    primary_start, primary_end = resolve_period(
        period="this_week", from_iso=None, to_iso=None, now=anchor
    )
    cmp_start, cmp_end = resolve_compare_to(
        compare_to="this_week",
        primary_from=primary_start,
        primary_to=primary_end,
    )
    primary_len = _length_days(primary_start, primary_end)
    cmp_len = _length_days(cmp_start, cmp_end)
    assert cmp_len == pytest.approx(primary_len, abs=1e-6)
    # Comparison window ends exactly one microsecond before primary starts.
    assert cmp_end + timedelta(microseconds=1) == primary_start


def test_custom_period_passthrough() -> None:
    start, end = resolve_period(
        period="custom",
        from_iso="2026-06-13T00:00:00Z",
        to_iso="2026-09-05T23:59:59Z",
    )
    assert start.isoformat() == "2026-06-13T00:00:00+00:00"
    assert end.isoformat() == "2026-09-05T23:59:59+00:00"


def test_format_iso_requires_tz_aware() -> None:
    with pytest.raises(ValueError):
        format_iso(datetime(2026, 9, 6, 12, 0, 0))


@pytest.mark.parametrize(
    ("period_alias", "expected_bucket"),
    [
        ("today", "day"),
        ("this_week", "day"),
        ("this_month", "day"),
        ("this_year", "week"),
    ],
)
def test_bucket_selection_for_all_standard_periods(
    period_alias: str, expected_bucket: str
) -> None:
    from app.reports.dashboard import _bucket_for_window
    start, end = resolve_period(
        period=period_alias, from_iso=None, to_iso=None, now=_WED
    )
    bucket = _bucket_for_window(start, end)
    assert bucket == expected_bucket
