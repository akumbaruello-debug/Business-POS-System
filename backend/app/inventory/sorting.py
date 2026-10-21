"""Pure quantity and carrying-value reconciliation for fish sorting."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_FLOOR, Decimal

QTY_QUANTUM = Decimal("0.0001")
MONEY_QUANTUM = Decimal("0.01")
MAX_PERSISTED_QUANTITY = Decimal("99999999999.9999")


def to_base_quantity(quantity: Decimal, factor_to_base: Decimal) -> Decimal:
    """Convert a mass quantity without silently rounding persisted quantity."""
    if quantity <= 0 or factor_to_base <= 0:
        raise ValueError("Quantity and mass conversion factor must be positive.")
    raw = quantity * factor_to_base
    rounded = raw.quantize(QTY_QUANTUM)
    if raw != rounded or rounded > MAX_PERSISTED_QUANTITY:
        raise ValueError("Converted quantity exceeds the persisted precision of 4 decimals.")
    return rounded


def allocate_cents(total: Decimal, weights: Mapping[int, Decimal]) -> dict[int, Decimal]:
    """Allocate money by weight using largest-remainder cent rounding.

    Stable key ordering breaks equal fractional-cent ties and guarantees the
    allocations sum exactly to ``total``.
    """
    if total < 0 or not weights or any(weight < 0 for weight in weights.values()):
        raise ValueError("A nonnegative amount and at least one nonnegative weight are required.")
    cents = int((total / MONEY_QUANTUM).to_integral_value())
    weight_total = sum(weights.values(), Decimal("0"))
    if weight_total <= 0:
        if cents:
            raise ValueError("Cannot allocate value across zero quantity.")
        return {key: Decimal("0.00") for key in weights}
    raw_cents = {key: Decimal(cents) * weight / weight_total for key, weight in weights.items()}
    whole = {key: int(value.to_integral_value(rounding=ROUND_FLOOR)) for key, value in raw_cents.items()}
    remainder = cents - sum(whole.values())
    order = sorted(weights, key=lambda key: (-(raw_cents[key] - whole[key]), key))
    for key in order[:remainder]:
        whole[key] += 1
    return {key: (Decimal(value) * MONEY_QUANTUM).quantize(MONEY_QUANTUM) for key, value in whole.items()}


def validate_reconciliation(
    input_quantity: Decimal,
    outputs: Mapping[int, Decimal],
    loss_quantity: Decimal,
    remaining_quantity: Decimal,
) -> None:
    if input_quantity <= 0 or loss_quantity < 0 or remaining_quantity < 0:
        raise ValueError("Input must be positive and loss/remainder cannot be negative.")
    if any(quantity <= 0 for quantity in outputs.values()):
        raise ValueError("Every classified output quantity must be positive.")
    if sum(outputs.values(), Decimal("0")) + loss_quantity + remaining_quantity != input_quantity:
        raise ValueError("Input must exactly equal classified outputs plus loss plus remaining unsorted quantity.")
