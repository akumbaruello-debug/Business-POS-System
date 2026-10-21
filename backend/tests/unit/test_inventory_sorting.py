from decimal import Decimal

import pytest

from app.inventory.sorting import allocate_cents, to_base_quantity, validate_reconciliation


def test_quantity_conversion_requires_mass_precision():
    assert to_base_quantity(Decimal("1.25"), Decimal("100")) == Decimal("125.0000")
    with pytest.raises(ValueError, match="persisted precision"):
        to_base_quantity(Decimal("0.00001"), Decimal("1"))


def test_allocation_is_weighted_and_reconciles_cents():
    allocated = allocate_cents(Decimal("10.01"), {1: Decimal("1"), 2: Decimal("1"), 3: Decimal("1")})
    assert allocated == {1: Decimal("3.34"), 2: Decimal("3.34"), 3: Decimal("3.33")}
    assert sum(allocated.values()) == Decimal("10.01")


def test_quantity_reconciliation_is_exact():
    validate_reconciliation(Decimal("10.0000"), {1: Decimal("7.5000")}, Decimal("1.0000"), Decimal("1.5000"))
    with pytest.raises(ValueError, match="exactly equal"):
        validate_reconciliation(Decimal("10.0000"), {1: Decimal("7.5000")}, Decimal("1.0000"), Decimal("1.4999"))
