"""Unit tests for the units conversion helper (V3-PLAN test matrix: Units).

Covers the plan's required examples:
* 1 kuintal = 100 kg; 1 ton = 1,000 kg; kg/kuintal/ton normalize consistently.
* buah (count) never converts to kg implicitly — the cross-dimension rule is
  the API-level test (test_cross_dimension_base_rejected); this file covers
  the arithmetic within one dimension incl. precision/rounding.
"""

from __future__ import annotations

import pytest

from app.services.units import convert_quantity, ValidationFailed

# (qty, from_factor, to_factor, expected)
CONVERSION_CASES = [
    # kg(1) -> base(kg): 1 kg
    (1, 1, 1, 1),
    # kuintal(100) -> kg: 1*100/1 = 100
    (1, 100, 1, 100),
    # ton(1000) -> kg: 1*1000/1 = 1000
    (1, 1000, 1, 1000),
    # kg -> kuintal: 100*1/100 = 1
    (100, 1, 100, 1),
    # kg -> ton: 1000*1/1000 = 1
    (1000, 1, 1000, 1),
    # ton -> kuintal: 1*1000/100 = 10
    (1, 1000, 100, 10),
    # kuintal -> ton: 1*100/1000 = 0.1
    (1, 100, 1000, 0.1),
    # fractional rounding: 123.4567 kuintal -> kg
    (123.456789, 100, 1, 12345.6789),
]


@pytest.mark.parametrize(
    ("qty", "from_factor", "to_factor", "expected"),
    CONVERSION_CASES,
)
def test_convert_quantity_standard_conversions(qty, from_factor, to_factor, expected):
    assert convert_quantity(qty, from_factor, to_factor) == pytest.approx(expected, abs=1e-6)


def test_convert_quantity_rounds_to_6_decimals():
    # 1/3 kg in kuintal terms rounds to 6 dp: 1 * 1 / 3
    assert convert_quantity(1, 1, 3) == round(1 / 3, 6)


def test_convert_quantity_missing_factor_raises():
    with pytest.raises(ValidationFailed):
        convert_quantity(1, None, 1)


def test_convert_quantity_nonpositive_factor_raises():
    with pytest.raises(ValidationFailed):
        convert_quantity(1, -1, 1)


def test_legacy_unit_no_factor_is_rejected_explicitly():
    """pack/box have no factor (unknown conversion) — must NOT silently
    behave as factor=1. Both None and one-sided None must raise."""
    with pytest.raises(ValidationFailed):
        convert_quantity(10, None, 1)
    with pytest.raises(ValidationFailed):
        convert_quantity(10, 1, None)
    with pytest.raises(ValidationFailed):
        convert_quantity(10, None, None)
