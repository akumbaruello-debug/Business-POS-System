from decimal import Decimal

import pytest

from app.purchases.cost_allocation import allocate_purchase_cost


@pytest.fixture
def lines():
    return [
        {"id": 2, "line_number": 2, "line_subtotal": Decimal("100"), "quantity": Decimal("1"), "dimension": "mass", "factor_to_base": Decimal("1000")},
        {"id": 1, "line_number": 1, "line_subtotal": Decimal("200"), "quantity": Decimal("2"), "dimension": "mass", "factor_to_base": Decimal("1")},
    ]


def test_line_value_allocation_preserves_existing_default_and_rounding(lines):
    allocated = allocate_purchase_cost(Decimal("10.01"), lines, "line_value")
    assert allocated == {1: Decimal("6.67"), 2: Decimal("3.34")}
    assert sum(allocated.values()) == Decimal("10.01")


def test_weight_allocation_uses_kg_equivalent(lines):
    allocated = allocate_purchase_cost(Decimal("10.00"), lines, "weight")
    assert allocated == {1: Decimal("0.01"), 2: Decimal("9.99")}
    assert sum(allocated.values()) == Decimal("10.00")


def test_manual_allocation_requires_exact_reconciliation(lines):
    allocated = allocate_purchase_cost(Decimal("10.00"), lines, "manual", {1: Decimal("4"), 2: Decimal("6")})
    assert allocated == {1: Decimal("4.00"), 2: Decimal("6.00")}
    with pytest.raises(ValueError, match="must equal"):
        allocate_purchase_cost(Decimal("10.00"), lines, "manual", {1: Decimal("4"), 2: Decimal("5.99")})


def test_weight_mode_rejects_implicit_non_mass_conversion(lines):
    lines[0]["dimension"] = "count"
    with pytest.raises(ValueError, match="configured mass unit"):
        allocate_purchase_cost(Decimal("10"), lines, "weight")
