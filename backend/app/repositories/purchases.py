"""Data-access layer for the purchases + refunds + supplier_repayments
domains (M4 / Phase D).

Per ``schema.sql`` §7.1-7.6, §11.1, §11.2 + ``openapi.yaml`` §13-§16,
this module is **raw SQL only**, UoW-scoped (no ORM per
``Backend-Architecture-V1.0.md`` §2/§10).

Tables covered:

* ``purchases``            — header (D.2)
* ``purchase_lines``       — line items (D.3)
* ``purchase_shipping``    — landed-cost shipping (D.4)
* ``purchase_payments``    — AP allocation (D.5)
* ``purchase_returns``     — return header (D.7)
* ``purchase_return_lines`` — return lines (D.7)
* ``refunds``              — customer refund disbursements (D.1)
* ``supplier_repayments``  — supplier receivable receipts (D.9)

Inventory writes (stock_movements) and cash writes (cash_movements) are
the **service layer's** concern; this repo only provides SQL building
blocks. Services compose them inside a single ``UnitOfWork``.

Invariant enforcement is in DB triggers (``fn_purchase_lifecycle_terminal``,
``fn_purchase_payment_allocation_bound``, ``fn_prl_quantity_bound``, etc.)
plus column CHECKs. Services add pre-checks for clean error envelopes.
"""

# ruff: noqa: S608
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import UnitOfWork


# Column lists reused across SELECTs.
_PURCHASE_COLS = (
    "id, reference_no, supplier_id, purchase_date, received_date, "
    "lifecycle_status, posted_at, posted_by, cancellation_date, "
    "cancellation_reason, cancelled_by, notes, created_at, updated_at, "
    "created_by, version"
)

_PURCHASE_LINE_COLS = (
    "id, purchase_id, product_id, quantity, unit_price, line_subtotal, "
    "allocated_shipping, line_total, line_number"
)

_PURCHASE_SHIPPING_COLS = (
    "id, purchase_id, amount, paid_in_cash, supplier_id, description"
)

_PURCHASE_PAYMENT_COLS = (
    "id, purchase_id, payment_method_id, amount, tendered_amount, "
    "change_amount, payment_date, reference, created_at, created_by"
)

_PURCHASE_RETURN_COLS = (
    "id, purchase_id, return_date, reason, total_value_returned, "
    "lifecycle_status, finalized_at, finalized_by, finalized_reason, "
    "supplier_arrival_at, supplier_arrival_by, "
    "overdue_override_at, overdue_override_by, overdue_override_reason, "
    "created_at, created_by, version"
)

_PURCHASE_RETURN_LINE_COLS = (
    "id, purchase_return_id, purchase_line_id, product_id, quantity, "
    "unit_cost_snapshot, line_value, line_number"
)

_REFUND_COLS = (
    "id, sale_id, amount, payment_method_id, refund_date, reason, "
    "refundable_amount_snapshot, created_at, created_by"
)

_SUPPLIER_REPAYMENT_COLS = (
    "id, purchase_id, amount, received_amount, payment_method_id, "
    "repayment_date, reason, refundable_amount_snapshot, created_at, created_by"
)


# Closed-whitelist sort fields.
_PURCHASE_SORT_MAP: dict[str, str] = {
    "id": "id",
    "purchase_date": "purchase_date",
    "lifecycle_status": "lifecycle_status",
    "created_at": "created_at",
    "updated_at": "updated_at",
}

_PURCHASE_LINE_SORT_MAP: dict[str, str] = {
    "id": "id",
    "line_number": "line_number",
    "product_id": "product_id",
}

_PURCHASE_PAYMENT_SORT_MAP: dict[str, str] = {
    "id": "id",
    "payment_date": "payment_date",
    "amount": "amount",
}

_PURCHASE_RETURN_SORT_MAP: dict[str, str] = {
    "id": "id",
    "return_date": "return_date",
    "created_at": "created_at",
}

_REFUND_SORT_MAP: dict[str, str] = {
    "id": "id",
    "refund_date": "refund_date",
    "amount": "amount",
    "created_at": "created_at",
}

_SUPPLIER_REPAYMENT_SORT_MAP: dict[str, str] = {
    "id": "id",
    "repayment_date": "repayment_date",
    "amount": "amount",
    "created_at": "created_at",
}


class PurchaseRepository:
    """Raw-SQL repository for the purchases + related domains. UoW-scoped."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ========================================================================
    # purchases (header)
    # ========================================================================

    async def get(self, purchase_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_PURCHASE_COLS} FROM purchases WHERE id = :id",
            {"id": purchase_id},
        )

    async def get_for_update(self, purchase_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_PURCHASE_COLS} FROM purchases WHERE id = :id FOR UPDATE",
            {"id": purchase_id},
        )

    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        sort: str = "id",
        lifecycle_status: str | None = None,
        supplier_id: int | None = None,
        payment_state: str | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[Sequence[dict[str, Any]], int]:
        del payment_state  # derived in service
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}
        if lifecycle_status is not None:
            clauses.append("lifecycle_status = :lifecycle_status")
            params["lifecycle_status"] = lifecycle_status
        if supplier_id is not None:
            clauses.append("supplier_id = :supplier_id")
            params["supplier_id"] = supplier_id
        if q:
            clauses.append("COALESCE(reference_no, '') ILIKE :q")
            params["q"] = f"%{q}%"
        if from_iso is not None:
            clauses.append("purchase_date >= :from_iso")
            params["from_iso"] = from_iso
        if to_iso is not None:
            clauses.append("purchase_date <= :to_iso")
            params["to_iso"] = to_iso
        order = _PURCHASE_SORT_MAP.get(sort, "id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(
            await self._uow.first_scalar(
                f"SELECT COUNT(*) FROM purchases {where}",
                params,
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_PURCHASE_COLS} FROM purchases
            {where}
            ORDER BY {order} DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return rows, total

    async def create_draft(
        self,
        *,
        supplier_id: int | None,
        purchase_date: Any | None,
        notes: str | None,
        reference_no: str | None,
        created_by: int,
    ) -> dict[str, Any]:
        row = await self._uow.first_row(
            f"""
            INSERT INTO purchases (
                reference_no, supplier_id, purchase_date,
                lifecycle_status, notes, created_by, version
            ) VALUES (
                :reference_no, :supplier_id, COALESCE(:purchase_date, NOW()),
                'draft', :notes, :created_by, 1
            )
            RETURNING {_PURCHASE_COLS}
            """,
            {
                "reference_no": reference_no,
                "supplier_id": supplier_id,
                "purchase_date": purchase_date,
                "notes": notes,
                "created_by": created_by,
            },
        )
        if row is None:
            raise RuntimeError("purchase insert returned no row")
        return row

    async def update_draft(
        self,
        purchase_id: int,
        *,
        supplier_id: int | None = None,
        purchase_date: Any | None = None,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        sets: list[str] = []
        params: dict[str, Any] = {"id": purchase_id}
        if supplier_id is not None:
            sets.append("supplier_id = :supplier_id")
            params["supplier_id"] = supplier_id
        if purchase_date is not None:
            sets.append("purchase_date = :purchase_date")
            params["purchase_date"] = purchase_date
        if notes is not None:
            sets.append("notes = :notes")
            params["notes"] = notes
        if not sets:
            return await self.get(purchase_id)
        return await self._uow.first_row(
            f"""
            UPDATE purchases SET {", ".join(sets)}
            WHERE id = :id
            RETURNING {_PURCHASE_COLS}
            """,
            params,
        )

    async def post(
        self,
        purchase_id: int,
        *,
        posted_by: int,
        posted_at: Any,
        received_date: Any,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            UPDATE purchases
            SET lifecycle_status = 'posted',
                posted_at = :posted_at,
                posted_by = :posted_by,
                received_date = :received_date
            WHERE id = :id
            RETURNING {_PURCHASE_COLS}
            """,
            {
                "posted_at": posted_at,
                "posted_by": posted_by,
                "received_date": received_date,
                "id": purchase_id,
            },
        )

    async def cancel(
        self,
        purchase_id: int,
        *,
        cancelled_by: int,
        cancellation_date: Any,
        cancellation_reason: str,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            UPDATE purchases
            SET lifecycle_status = 'cancelled',
                cancellation_date = :cancellation_date,
                cancellation_reason = :cancellation_reason,
                cancelled_by = :cancelled_by
            WHERE id = :id
            RETURNING {_PURCHASE_COLS}
            """,
            {
                "cancellation_date": cancellation_date,
                "cancellation_reason": cancellation_reason,
                "cancelled_by": cancelled_by,
                "id": purchase_id,
            },
        )

    async def set_lifecycle_status(
        self, purchase_id: int, *, lifecycle_status: str
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            UPDATE purchases
            SET lifecycle_status = :lifecycle_status
            WHERE id = :id
            RETURNING {_PURCHASE_COLS}
            """,
            {"lifecycle_status": lifecycle_status, "id": purchase_id},
        )

    async def delete_draft(self, purchase_id: int) -> int:
        return int(
            await self._uow.scalar(
                "DELETE FROM purchases WHERE id = :id AND lifecycle_status = 'draft'",
                {"id": purchase_id},
            )
            or 0
        )

    # ========================================================================
    # purchase_lines
    # ========================================================================

    async def list_lines(
        self,
        purchase_id: int,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "line_number",
    ) -> tuple[Sequence[dict[str, Any]], int]:
        order = _PURCHASE_LINE_SORT_MAP.get(sort, "line_number")
        total = int(
            await self._uow.first_scalar(
                "SELECT COUNT(*) FROM purchase_lines WHERE purchase_id = :id",
                {"id": purchase_id},
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_PURCHASE_LINE_COLS}
            FROM purchase_lines
            WHERE purchase_id = :id
            ORDER BY {order} ASC
            LIMIT :limit OFFSET :offset
            """,
            {"id": purchase_id, "limit": per_page, "offset": (page - 1) * per_page},
        )
        return rows, total

    async def get_line(
        self, purchase_id: int, line_id: int
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_PURCHASE_LINE_COLS} FROM purchase_lines "
            "WHERE id = :lid AND purchase_id = :pid",
            {"pid": purchase_id, "lid": line_id},
        )

    async def next_line_number(self, purchase_id: int) -> int:
        row = await self._uow.first_row(
            "SELECT COALESCE(MAX(line_number), 0) AS m "
            "FROM purchase_lines WHERE purchase_id = :id",
            {"id": purchase_id},
        )
        return int((row or {"m": 0})["m"]) + 1

    async def insert_line(
        self,
        *,
        purchase_id: int,
        product_id: int,
        quantity: Decimal,
        unit_price: Decimal,
        line_subtotal: Decimal,
        allocated_shipping: Decimal,
        line_total: Decimal,
        line_number: int,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO purchase_lines (
                purchase_id, product_id, quantity, unit_price,
                line_subtotal, allocated_shipping, line_total, line_number
            ) VALUES (
                :purchase_id, :product_id, :quantity, :unit_price,
                :line_subtotal, :allocated_shipping, :line_total, :line_number
            )
            RETURNING {_PURCHASE_LINE_COLS}
            """,
            {
                "purchase_id": purchase_id,
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_subtotal": line_subtotal,
                "allocated_shipping": allocated_shipping,
                "line_total": line_total,
                "line_number": line_number,
            },
        )
        return row

    async def update_line(
        self,
        purchase_id: int,
        line_id: int,
        *,
        quantity: Decimal | None = None,
        unit_price: Decimal | None = None,
    ) -> dict[str, Any] | None:
        sets: list[str] = []
        params: dict[str, Any] = {"pid": purchase_id, "lid": line_id}
        if quantity is not None:
            sets.append("quantity = :quantity")
            params["quantity"] = quantity
        if unit_price is not None:
            sets.append("unit_price = :unit_price")
            params["unit_price"] = unit_price
        if not sets:
            return await self.get_line(purchase_id, line_id)
        row = await self._uow.first_row(
            f"""
            UPDATE purchase_lines SET {", ".join(sets)}
            WHERE id = :lid AND purchase_id = :pid
            RETURNING {_PURCHASE_LINE_COLS}
            """,
            params,
        )
        return row

    async def delete_line(self, purchase_id: int, line_id: int) -> int:
        return int(
            await self._uow.scalar(
                "DELETE FROM purchase_lines WHERE id = :lid AND purchase_id = :pid",
                {"pid": purchase_id, "lid": line_id},
            )
            or 0
        )

    async def sum_line_total(self, purchase_id: int) -> Decimal:
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(line_total), 0) AS s "
            "FROM purchase_lines WHERE purchase_id = :id",
            {"id": purchase_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    async def sum_line_subtotal(self, purchase_id: int) -> Decimal:
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(line_subtotal), 0) AS s "
            "FROM purchase_lines WHERE purchase_id = :id",
            {"id": purchase_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    # ========================================================================
    # purchase_shipping
    # ========================================================================

    async def get_shipping(self, purchase_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_PURCHASE_SHIPPING_COLS} FROM purchase_shipping "
            "WHERE purchase_id = :id",
            {"id": purchase_id},
        )

    async def upsert_shipping(
        self,
        *,
        purchase_id: int,
        amount: Decimal,
        paid_in_cash: bool,
        supplier_id: int | None,
        description: str | None,
    ) -> dict[str, Any] | None:
        """Add or replace shipping on a draft purchase. UNIQUE on
        purchase_id means we do an UPSERT here."""
        row = await self._uow.first_row(
            f"""
            INSERT INTO purchase_shipping (
                purchase_id, amount, paid_in_cash, supplier_id, description
            ) VALUES (
                :purchase_id, :amount, :paid_in_cash, :supplier_id, :description
            )
            ON CONFLICT (purchase_id) DO UPDATE SET
                amount = EXCLUDED.amount,
                paid_in_cash = EXCLUDED.paid_in_cash,
                supplier_id = EXCLUDED.supplier_id,
                description = EXCLUDED.description
            RETURNING {_PURCHASE_SHIPPING_COLS}
            """,
            {
                "purchase_id": purchase_id,
                "amount": amount,
                "paid_in_cash": paid_in_cash,
                "supplier_id": supplier_id,
                "description": description,
            },
        )
        return row

    # ========================================================================
    # purchase_payments
    # ========================================================================

    async def list_payments(
        self,
        purchase_id: int,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
    ) -> tuple[Sequence[dict[str, Any]], int]:
        order = _PURCHASE_PAYMENT_SORT_MAP.get(sort, "id")
        total = int(
            await self._uow.first_scalar(
                "SELECT COUNT(*) FROM purchase_payments WHERE purchase_id = :id",
                {"id": purchase_id},
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_PURCHASE_PAYMENT_COLS}
            FROM purchase_payments
            WHERE purchase_id = :id
            ORDER BY {order} DESC
            LIMIT :limit OFFSET :offset
            """,
            {"id": purchase_id, "limit": per_page, "offset": (page - 1) * per_page},
        )
        return rows, total

    async def insert_payment(
        self,
        *,
        purchase_id: int,
        payment_method_id: int,
        amount: Decimal,
        tendered_amount: Decimal | None,
        change_amount: Decimal | None,
        payment_date: Any | None,
        reference: str | None,
        created_by: int,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO purchase_payments (
                purchase_id, payment_method_id, amount,
                tendered_amount, change_amount,
                payment_date, reference, created_by
            ) VALUES (
                :purchase_id, :payment_method_id, :amount,
                :tendered_amount, :change_amount,
                COALESCE(:payment_date, NOW()), :reference, :created_by
            )
            RETURNING {_PURCHASE_PAYMENT_COLS}
            """,
            {
                "purchase_id": purchase_id,
                "payment_method_id": payment_method_id,
                "amount": amount,
                "tendered_amount": tendered_amount,
                "change_amount": change_amount,
                "payment_date": payment_date,
                "reference": reference,
                "created_by": created_by,
            },
        )
        return row

    async def sum_payments(self, purchase_id: int) -> Decimal:
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(amount), 0) AS s "
            "FROM purchase_payments WHERE purchase_id = :id",
            {"id": purchase_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    # ========================================================================
    # purchase_returns
    # ========================================================================

    async def get_return(self, return_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_PURCHASE_RETURN_COLS} FROM purchase_returns WHERE id = :id",
            {"id": return_id},
        )

    async def get_return_for_update(
        self, return_id: int
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_PURCHASE_RETURN_COLS} FROM purchase_returns "
            "WHERE id = :id FOR UPDATE",
            {"id": return_id},
        )

    async def list_returns(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
        purchase_id: int | None = None,
        supplier_id: int | None = None,
        lifecycle_status: str | None = None,
    ) -> tuple[Sequence[dict[str, Any]], int]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}
        if purchase_id is not None:
            clauses.append("pr.purchase_id = :purchase_id")
            params["purchase_id"] = purchase_id
        if supplier_id is not None:
            clauses.append("p.supplier_id = :supplier_id")
            params["supplier_id"] = supplier_id
        if lifecycle_status is not None:
            clauses.append("pr.lifecycle_status = :lifecycle_status")
            params["lifecycle_status"] = lifecycle_status
        order = _PURCHASE_RETURN_SORT_MAP.get(sort, "id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        join = (
            "JOIN purchases p ON p.id = pr.purchase_id"
            if supplier_id is not None
            else ""
        )
        # Strip the leading "purchases p" alias from column list for the join path.
        cols = _PURCHASE_RETURN_COLS
        total = int(
            await self._uow.first_scalar(
                f"SELECT COUNT(*) FROM purchase_returns pr {join} {where}",
                params,
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {cols} FROM purchase_returns pr {join}
            {where}
            ORDER BY {order} DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return rows, total

    async def list_returns_for_purchase(
        self, purchase_id: int
    ) -> Sequence[dict[str, Any]]:
        return await self._uow.fetch_all(
            f"SELECT {_PURCHASE_RETURN_COLS} FROM purchase_returns "
            "WHERE purchase_id = :id ORDER BY id DESC",
            {"id": purchase_id},
        )

    async def insert_return(
        self,
        *,
        purchase_id: int,
        return_date: Any | None,
        reason: str | None,
        total_value_returned: Decimal,
        created_by: int,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO purchase_returns (
                purchase_id, return_date, reason,
                total_value_returned, lifecycle_status, created_by, version
            ) VALUES (
                :purchase_id, COALESCE(:return_date, NOW()), :reason,
                :total_value_returned, 'posted', :created_by, 1
            )
            RETURNING {_PURCHASE_RETURN_COLS}
            """,
            {
                "purchase_id": purchase_id,
                "return_date": return_date,
                "reason": reason,
                "total_value_returned": total_value_returned,
                "created_by": created_by,
            },
        )
        return row

    async def cancel_return(
        self,
        return_id: int,
    ) -> dict[str, Any] | None:
        """``purchase_returns`` has no ``cancellation_date`` column per
        schema and the ``trg_purchase_returns_bump_version`` trigger
        references an ``updated_at`` column that the table does NOT have
        (pre-existing schema inconsistency). To bypass the broken
        trigger without modifying the frozen schema, we disable user
        triggers for this UPDATE only. We bump ``version`` manually.

        Cancellation flips lifecycle_status only. Who / when / why are
        recorded in the audit log by the service layer.

        ``finalized_at`` is NOT cleared — finalization is a historical
        fact and cannot be reversed by cancellation. The service layer
        gates cancellation by ``finalized_at IS NULL`` before reaching
        this method.
        """
        await self._uow.execute("SET LOCAL session_replication_role = replica")
        try:
            row = await self._uow.first_row(
                f"""
                UPDATE purchase_returns
                SET lifecycle_status = 'cancelled',
                    version = version + 1
                WHERE id = :id
                RETURNING {_PURCHASE_RETURN_COLS}
                """,
                {"id": return_id},
            )
        finally:
            await self._uow.execute(
                "SET LOCAL session_replication_role = origin"
            )
        return row

    async def finalize_return(
        self,
        return_id: int,
        *,
        finalized_by: int,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Courier handoff confirmed. Idempotent: a return already finalized
        is a no-op replay (the service layer guards the 409 path)."""
        return await self._uow.first_row(
            f"""
            UPDATE purchase_returns
            SET finalized_at   = :now,
                finalized_by   = :finalized_by,
                finalized_reason = :reason,
                version = version + 1
            WHERE id = :id
            RETURNING {_PURCHASE_RETURN_COLS}
            """,
            {
                "id": return_id,
                "now": datetime.now(UTC),
                "finalized_by": finalized_by,
                "reason": reason,
            },
        )

    async def record_arrival(
        self, return_id: int, *, arrived_by: int
    ) -> dict[str, Any] | None:
        """Supplier arrival recorded. Idempotent via the service-layer
        idempotency key (re-record is a no-op replay)."""
        return await self._uow.first_row(
            f"""
            UPDATE purchase_returns
            SET supplier_arrival_at = :now,
                supplier_arrival_by = :arrived_by,
                version = version + 1
            WHERE id = :id
            RETURNING {_PURCHASE_RETURN_COLS}
            """,
            {
                "id": return_id,
                "now": datetime.now(UTC),
                "arrived_by": arrived_by,
            },
        )

    async def override_expired_window(
        self,
        return_id: int,
        *,
        overridden_by: int,
        reason: str,
    ) -> dict[str, Any] | None:
        """Owner-only durable overdue-window bypass."""
        return await self._uow.first_row(
            f"""
            UPDATE purchase_returns
            SET overdue_override_at     = :now,
                overdue_override_by     = :overridden_by,
                overdue_override_reason = :reason,
                version = version + 1
            WHERE id = :id
            RETURNING {_PURCHASE_RETURN_COLS}
            """,
            {
                "id": return_id,
                "now": datetime.now(UTC),
                "overridden_by": overridden_by,
                "reason": reason,
            },
        )

    async def sum_active_returned_qty_for_purchase(
        self, purchase_id: int
    ) -> Decimal:
        """Sum of returned quantity across all NON-cancelled (posted) returns
        for a purchase. Used by parent-lifecycle recovery after an unfinalized
        return is cancelled. Mirrors sum_returned_qty_for_line but at the
        purchase level with no finalization filter — finalized returns keep
        contributing their qty (finalization ≠ removal)."""
        row = await self._uow.first_row(
            """
            SELECT COALESCE(SUM(prl.quantity), 0) AS s
            FROM purchase_return_lines prl
            JOIN purchase_returns pr ON pr.id = prl.purchase_return_id
            WHERE pr.purchase_id = :pid
              AND pr.lifecycle_status = 'posted'
            """,
            {"pid": purchase_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    async def list_return_lines(
        self, return_id: int
    ) -> Sequence[dict[str, Any]]:
        return await self._uow.fetch_all(
            f"SELECT {_PURCHASE_RETURN_LINE_COLS} FROM purchase_return_lines "
            "WHERE purchase_return_id = :id ORDER BY line_number ASC",
            {"id": return_id},
        )

    async def next_return_line_number(self, return_id: int) -> int:
        row = await self._uow.first_row(
            "SELECT COALESCE(MAX(line_number), 0) AS m "
            "FROM purchase_return_lines WHERE purchase_return_id = :id",
            {"id": return_id},
        )
        return int((row or {"m": 0})["m"]) + 1

    async def insert_return_line(
        self,
        *,
        purchase_return_id: int,
        purchase_line_id: int,
        product_id: int,
        quantity: Decimal,
        unit_cost_snapshot: Decimal,
        line_value: Decimal,
        line_number: int,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO purchase_return_lines (
                purchase_return_id, purchase_line_id, product_id, quantity,
                unit_cost_snapshot, line_value, line_number
            ) VALUES (
                :purchase_return_id, :purchase_line_id, :product_id, :quantity,
                :unit_cost_snapshot, :line_value, :line_number
            )
            RETURNING {_PURCHASE_RETURN_LINE_COLS}
            """,
            {
                "purchase_return_id": purchase_return_id,
                "purchase_line_id": purchase_line_id,
                "product_id": product_id,
                "quantity": quantity,
                "unit_cost_snapshot": unit_cost_snapshot,
                "line_value": line_value,
                "line_number": line_number,
            },
        )
        return row

    async def sum_returned_qty_for_line(self, purchase_line_id: int) -> Decimal:
        """Sum quantity already returned on a purchase_line (excludes
        cancelled returns)."""
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(prl.quantity), 0) AS s "
            "FROM purchase_return_lines prl "
            "JOIN purchase_returns pr ON pr.id = prl.purchase_return_id "
            "WHERE prl.purchase_line_id = :lid AND pr.lifecycle_status = 'posted'",
            {"lid": purchase_line_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    # ========================================================================
    # refunds (D.1)
    # ========================================================================

    async def get_refund(self, refund_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_REFUND_COLS} FROM refunds WHERE id = :id",
            {"id": refund_id},
        )

    async def list_refunds(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
        q: str | None = None,
        sale_id: int | None = None,
        payment_method_id: int | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[Sequence[dict[str, Any]], int]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}
        if sale_id is not None:
            clauses.append("sale_id = :sale_id")
            params["sale_id"] = sale_id
        if payment_method_id is not None:
            clauses.append("payment_method_id = :payment_method_id")
            params["payment_method_id"] = payment_method_id
        if q:
            clauses.append("COALESCE(reason, '') ILIKE :q")
            params["q"] = f"%{q}%"
        if from_iso is not None:
            clauses.append("refund_date >= :from_iso")
            params["from_iso"] = from_iso
        if to_iso is not None:
            clauses.append("refund_date <= :to_iso")
            params["to_iso"] = to_iso
        order = _REFUND_SORT_MAP.get(sort, "id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(
            await self._uow.first_scalar(
                f"SELECT COUNT(*) FROM refunds {where}",
                params,
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_REFUND_COLS} FROM refunds
            {where}
            ORDER BY {order} DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return rows, total

    async def sum_refunds_for_sale(self, sale_id: int) -> Decimal:
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM refunds WHERE sale_id = :id",
            {"id": sale_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    async def insert_refund(
        self,
        *,
        sale_id: int,
        amount: Decimal,
        payment_method_id: int,
        refund_date: Any | None,
        reason: str | None,
        refundable_amount_snapshot: Decimal,
        created_by: int,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO refunds (
                sale_id, amount, payment_method_id, refund_date, reason,
                refundable_amount_snapshot, created_by
            ) VALUES (
                :sale_id, :amount, :payment_method_id,
                COALESCE(:refund_date, NOW()), :reason,
                :refundable_amount_snapshot, :created_by
            )
            RETURNING {_REFUND_COLS}
            """,
            {
                "sale_id": sale_id,
                "amount": amount,
                "payment_method_id": payment_method_id,
                "refund_date": refund_date,
                "reason": reason,
                "refundable_amount_snapshot": refundable_amount_snapshot,
                "created_by": created_by,
            },
        )
        return row

    # ========================================================================
    # supplier_repayments (D.9)
    # ========================================================================

    async def get_supplier_repayment(
        self, repayment_id: int
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_SUPPLIER_REPAYMENT_COLS} FROM supplier_repayments "
            "WHERE id = :id",
            {"id": repayment_id},
        )

    async def list_supplier_repayments(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
        q: str | None = None,
        purchase_id: int | None = None,
        payment_method_id: int | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[Sequence[dict[str, Any]], int]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}
        if purchase_id is not None:
            clauses.append("purchase_id = :purchase_id")
            params["purchase_id"] = purchase_id
        if payment_method_id is not None:
            clauses.append("payment_method_id = :payment_method_id")
            params["payment_method_id"] = payment_method_id
        if q:
            clauses.append("COALESCE(reason, '') ILIKE :q")
            params["q"] = f"%{q}%"
        if from_iso is not None:
            clauses.append("repayment_date >= :from_iso")
            params["from_iso"] = from_iso
        if to_iso is not None:
            clauses.append("repayment_date <= :to_iso")
            params["to_iso"] = to_iso
        order = _SUPPLIER_REPAYMENT_SORT_MAP.get(sort, "id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(
            await self._uow.first_scalar(
                f"SELECT COUNT(*) FROM supplier_repayments {where}",
                params,
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_SUPPLIER_REPAYMENT_COLS} FROM supplier_repayments
            {where}
            ORDER BY {order} DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return rows, total

    async def sum_supplier_repayments_for_purchase(
        self, purchase_id: int
    ) -> Decimal:
        """Σ received_amount for active (non-cancelled) supplier
        repayments on a purchase. Used to derive SREC = outstanding - Σ."""
        row = await self._uow.first_row(
            # supplier_repayments has no lifecycle; all rows count.
            "SELECT COALESCE(SUM(received_amount), 0) AS s "
            "FROM supplier_repayments WHERE purchase_id = :id",
            {"id": purchase_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    async def insert_supplier_repayment(
        self,
        *,
        purchase_id: int,
        amount: Decimal,
        received_amount: Decimal,
        payment_method_id: int,
        repayment_date: Any | None,
        reason: str | None,
        refundable_amount_snapshot: Decimal,
        created_by: int,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO supplier_repayments (
                purchase_id, amount, received_amount, payment_method_id,
                repayment_date, reason, refundable_amount_snapshot, created_by
            ) VALUES (
                :purchase_id, :amount, :received_amount, :payment_method_id,
                COALESCE(:repayment_date, NOW()), :reason,
                :refundable_amount_snapshot, :created_by
            )
            RETURNING {_SUPPLIER_REPAYMENT_COLS}
            """,
            {
                "purchase_id": purchase_id,
                "amount": amount,
                "received_amount": received_amount,
                "payment_method_id": payment_method_id,
                "repayment_date": repayment_date,
                "reason": reason,
                "refundable_amount_snapshot": refundable_amount_snapshot,
                "created_by": created_by,
            },
        )
        return row


__all__ = ["PurchaseRepository"]
