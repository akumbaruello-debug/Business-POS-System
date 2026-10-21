"""Deterministic allocation helpers for draft purchase costs."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any

CENT = Decimal("0.01")
METHODS = frozenset({"line_value", "weight", "manual"})


def allocate_purchase_cost(
    amount: Decimal,
    lines: list[dict[str, Any]],
    method: str,
    manual: dict[int, Decimal] | None = None,
) -> dict[int, Decimal]:
    """Allocate exactly ``amount`` across purchase lines, with cents remainder on last line."""
    if method not in METHODS:
        raise ValueError("Unsupported purchase cost allocation method.")
    amount = Decimal(amount).quantize(CENT)
    if amount < 0:
        raise ValueError("Purchase cost cannot be negative.")
    ordered = sorted(lines, key=lambda line: (int(line["line_number"]), int(line["id"])))
    if not ordered:
        if amount == 0:
            return {}
        raise ValueError("A purchase cost requires at least one purchase line.")
    ids = [int(line["id"]) for line in ordered]
    if method == "manual":
        values = {int(key): Decimal(value).quantize(CENT) for key, value in (manual or {}).items()}
        if set(values) - set(ids) or any(value < 0 for value in values.values()):
            raise ValueError("Manual allocation must reference only this purchase's lines with non-negative amounts.")
        if sum(values.values(), Decimal("0")) != amount:
            raise ValueError("Manual allocations must equal the purchase cost amount.")
        return {line_id: values.get(line_id, Decimal("0.00")) for line_id in ids}
    if method == "line_value":
        weights = [Decimal(str(line["line_subtotal"])) for line in ordered]
    else:
        if any(line.get("dimension") != "mass" or line.get("factor_to_base") is None for line in ordered):
            raise ValueError("Weight allocation requires a configured mass unit for every purchase line.")
        weights = [Decimal(str(line["quantity"])) * Decimal(str(line["factor_to_base"])) for line in ordered]
    total = sum(weights, Decimal("0"))
    if total <= 0:
        raise ValueError("Allocation weights must have a positive total.")
    result: dict[int, Decimal] = {}
    remaining = amount
    for index, (line_id, weight) in enumerate(zip(ids, weights, strict=True)):
        if index == len(ids) - 1:
            value = remaining
        else:
            value = (amount * weight / total).quantize(CENT, rounding=ROUND_DOWN)
            remaining -= value
        result[line_id] = value
    return result
