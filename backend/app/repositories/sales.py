"""Data-access layer for the sales domain (B.8).

Per DB spec §2.4 + ``schema.sql`` §6.1-6.5, §7 (stock/cash), §17
(product_stock/product_valuation views).

This module is **raw SQL only**, UoW-scoped. No SQLAlchemy ORM (per the
locked architecture). All UoW methods are synchronous SQLAlchemy calls
through the UnitOfWork facade; the service layer awaits them.

Tables covered:

* ``sales``            — header (C.1/C.2/C.5)
* ``sale_lines``       — line items (C.2)
* ``sale_payments``    — payment allocations (C.4)
* ``sales_returns``    — return header (C.6)
* ``sales_return_lines`` — return lines (C.6)
* ``products``         — read for product.selling_price snapshot at line
  add (via the existing ``app.repositories.products.ProductRepository``)
* ``stock_movements``  — append-only; reversal rows go here
* ``cash_movements``   — append-only; sale_payment / change_tendered rows
* ``product_valuation`` — read for on-hand quantity at post

Inventory and payment writes are the **service layer's** concern; this
repo only provides the SQL building blocks. The service composes them
inside a single ``UnitOfWork`` transaction.
"""

# ruff: noqa: S608
from __future__ import annotations

import builtins
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import UnitOfWork


# Column lists reused across SELECTs.
_SALE_COLS = (
    "id, reference_no, customer_id, sale_date, total_amount, discount_amount, "
    "lifecycle_status, cancellation_date, cancellation_reason, cancelled_by, "
    "notes, created_at, updated_at, created_by, posted_at, posted_by, version"
)

_SALE_LINE_COLS = (
    "id, sale_id, product_id, quantity, unit_price, discount_amount, line_total, "
    "unit_cost_snapshot, cogs_total_snapshot, line_number, "
    "is_negative_stock_fallback, stock_movement_id"
)

_SALE_PAYMENT_COLS = (
    "id, sale_id, payment_method_id, amount, tendered_amount, change_amount, "
    "payment_date, reference, created_at, created_by"
)

_SALES_RETURN_COLS = (
    "id, sale_id, return_date, reason, total_selling_price_returned, "
    "total_cost_returned, lifecycle_status, created_at, created_by, "
    "cancellation_date, cancelled_by, version"
)

_SALES_RETURN_LINE_COLS = (
    "id, sales_return_id, sale_line_id, product_id, quantity, "
    "returned_selling_price, returned_unit_cost, line_number"
)


# Closed-whitelist maps for sort fields on /sales list.
_SALE_SORT_MAP: dict[str, str] = {
    "id": "id",
    "sale_date": "sale_date",
    "total_amount": "total_amount",
    "lifecycle_status": "lifecycle_status",
    "created_at": "created_at",
    "updated_at": "updated_at",
}

_SALE_LINE_SORT_MAP: dict[str, str] = {
    "id": "id",
    "line_number": "line_number",
    "product_id": "product_id",
}

_SALE_PAYMENT_SORT_MAP: dict[str, str] = {
    "id": "id",
    "payment_date": "payment_date",
    "amount": "amount",
    "created_at": "created_at",
}

_SALES_RETURN_SORT_MAP: dict[str, str] = {
    "id": "id",
    "return_date": "return_date",
    "created_at": "created_at",
}

_SALES_RETURN_LINE_SORT_MAP: dict[str, str] = {
    "id": "id",
    "line_number": "line_number",
    "product_id": "product_id",
}


class SaleRepository:
    """Raw-SQL repository for the sales domain. UoW-scoped."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ========================================================================
    # sales (header)
    # ========================================================================

    async def get(self, sale_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_SALE_COLS} FROM sales WHERE id = :id",
            {"id": sale_id},
        )

    async def get_for_update(self, sale_id: int) -> dict[str, Any] | None:
        """Lock the sale row in this transaction. Used by post/cancel/return."""
        return await self._uow.first_row(
            f"SELECT {_SALE_COLS} FROM sales WHERE id = :id FOR UPDATE",
            {"id": sale_id},
        )

    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        sort: str = "id",
        lifecycle_status: str | None = None,
        customer_id: int | None = None,
        payment_state: str | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[builtins.list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": per_page,
            "offset": (page - 1) * per_page,
        }
        if lifecycle_status is not None:
            clauses.append("lifecycle_status = :lifecycle_status")
            params["lifecycle_status"] = lifecycle_status
        if customer_id is not None:
            clauses.append("customer_id = :customer_id")
            params["customer_id"] = customer_id
        # payment_state is a derived column; for now filter the simple
        # paid_amount vs total_amount client-side in the service after the
        # fetch. (We pass it through but it's not used at the SQL level.)
        del payment_state  # not used; filtered in service
        if q:
            clauses.append("COALESCE(reference_no, '') ILIKE :q")
            params["q"] = f"%{q}%"
        if from_iso is not None:
            clauses.append("sale_date >= :from_iso")
            params["from_iso"] = from_iso
        if to_iso is not None:
            clauses.append("sale_date <= :to_iso")
            params["to_iso"] = to_iso
        order = _SALE_SORT_MAP.get(sort, "id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(
            await self._uow.first_scalar(
                f"SELECT COUNT(*) FROM sales {where}",
                params,
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_SALE_COLS} FROM sales
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
        customer_id: int | None,
        sale_date: datetime | str | None,
        total_amount: Decimal,
        discount_amount: Decimal,
        notes: str | None,
        reference_no: str | None,
        created_by: int,
    ) -> dict[str, Any]:
        row = await self._uow.first_row(
            f"""
            INSERT INTO sales (
                reference_no, customer_id, sale_date,
                total_amount, discount_amount,
                lifecycle_status, notes, created_by, version
            ) VALUES (
                :reference_no, :customer_id, COALESCE(:sale_date, NOW()),
                :total_amount, :discount_amount,
                'draft', :notes, :created_by, 1
            )
            RETURNING {_SALE_COLS}
            """,
            {
                "reference_no": reference_no,
                "customer_id": customer_id,
                "sale_date": sale_date,
                "total_amount": total_amount,
                "discount_amount": discount_amount,
                "notes": notes,
                "created_by": created_by,
            },
        )
        if row is None:
            raise RuntimeError("sale insert returned no row")
        return row

    async def update_draft(
        self,
        sale_id: int,
        *,
        customer_id: int | None = None,
        sale_date: datetime | str | None = None,
        discount_amount: Decimal | None = None,
        notes: str | None = None,
        total_amount: Decimal | None = None,
    ) -> dict[str, Any] | None:
        """Update a draft sale. Returns the updated row.

        ``total_amount`` is recomputed by the service (Σ line_total -
        discount) and passed in here. ``version`` is bumped by the DB
        trigger.
        """
        sets: list[str] = []
        params: dict[str, Any] = {"id": sale_id}
        if customer_id is not None:
            sets.append("customer_id = :customer_id")
            params["customer_id"] = customer_id
        if sale_date is not None:
            sets.append("sale_date = :sale_date")
            params["sale_date"] = sale_date
        if discount_amount is not None:
            sets.append("discount_amount = :discount_amount")
            params["discount_amount"] = discount_amount
        if notes is not None:
            sets.append("notes = :notes")
            params["notes"] = notes
        if total_amount is not None:
            sets.append("total_amount = :total_amount")
            params["total_amount"] = total_amount
        if not sets:
            return await self.get(sale_id)
        return await self._uow.first_row(
            f"""
            UPDATE sales SET {", ".join(sets)}
            WHERE id = :id
            RETURNING {_SALE_COLS}
            """,
            params,
        )

    async def post(
        self,
        sale_id: int,
        *,
        posted_by: int,
        posted_at: datetime | str,
        lifecycle_status: str = "posted",
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            UPDATE sales
            SET lifecycle_status = :lifecycle_status,
                posted_at = :posted_at,
                posted_by = :posted_by
            WHERE id = :id
            RETURNING {_SALE_COLS}
            """,
            {
                "lifecycle_status": lifecycle_status,
                "posted_at": posted_at,
                "posted_by": posted_by,
                "id": sale_id,
            },
        )

    async def mark_completed(self, sale_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            UPDATE sales SET lifecycle_status = 'completed'
            WHERE id = :id
            RETURNING {_SALE_COLS}
            """,
            {"id": sale_id},
        )

    async def cancel(
        self,
        sale_id: int,
        *,
        cancelled_by: int,
        cancellation_date: datetime | str,
        cancellation_reason: str,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            UPDATE sales
            SET lifecycle_status = 'cancelled',
                cancellation_date = :cancellation_date,
                cancellation_reason = :cancellation_reason,
                cancelled_by = :cancelled_by
            WHERE id = :id
            RETURNING {_SALE_COLS}
            """,
            {
                "cancellation_date": cancellation_date,
                "cancellation_reason": cancellation_reason,
                "cancelled_by": cancelled_by,
                "id": sale_id,
            },
        )

    async def set_lifecycle_status(
        self,
        sale_id: int,
        *,
        lifecycle_status: str,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            UPDATE sales
            SET lifecycle_status = :lifecycle_status
            WHERE id = :id
            RETURNING {_SALE_COLS}
            """,
            {"lifecycle_status": lifecycle_status, "id": sale_id},
        )

    async def delete_draft(self, sale_id: int) -> int:
        # ON DELETE CASCADE on sale_lines / sale_payments; sales_returns
        # is RESTRICT, but a draft has no returns (returns only after post).
        return int(
            await self._uow.scalar(
                "DELETE FROM sales WHERE id = :id AND lifecycle_status = 'draft'",
                {"id": sale_id},
            )
            or 0
        )

    # ========================================================================
    # sale_lines
    # ========================================================================

    async def list_lines(
        self,
        sale_id: int,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "line_number",
    ) -> tuple[builtins.list[dict[str, Any]], int]:
        order = _SALE_LINE_SORT_MAP.get(sort, "line_number")
        total = int(
            await self._uow.first_scalar(
                "SELECT COUNT(*) FROM sale_lines WHERE sale_id = :id",
                {"id": sale_id},
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_SALE_LINE_COLS}
            FROM sale_lines
            WHERE sale_id = :id
            ORDER BY {order} ASC
            LIMIT :limit OFFSET :offset
            """,
            {
                "id": sale_id,
                "limit": per_page,
                "offset": (page - 1) * per_page,
            },
        )
        return rows, total

    async def get_line(self, sale_id: int, line_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_SALE_LINE_COLS} FROM sale_lines WHERE id = :lid AND sale_id = :sid",
            {"sid": sale_id, "lid": line_id},
        )

    async def next_line_number(self, sale_id: int) -> int:
        row = await self._uow.first_row(
            "SELECT COALESCE(MAX(line_number), 0) AS m FROM sale_lines WHERE sale_id = :id",
            {"id": sale_id},
        )
        return int((row or {"m": 0})["m"]) + 1

    async def insert_line(
        self,
        *,
        sale_id: int,
        product_id: int,
        quantity: Decimal,
        unit_price: Decimal,
        discount_amount: Decimal,
        line_total: Decimal,
        line_number: int,
        unit_cost_snapshot: Decimal | None = None,
        cogs_total_snapshot: Decimal | None = None,
        is_negative_stock_fallback: bool = False,
        stock_movement_id: int | None = None,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO sale_lines (
                sale_id, product_id, quantity, unit_price,
                discount_amount, line_total, line_number,
                unit_cost_snapshot, cogs_total_snapshot,
                is_negative_stock_fallback, stock_movement_id
            ) VALUES (
                :sale_id, :product_id, :quantity, :unit_price,
                :discount_amount, :line_total, :line_number,
                :unit_cost_snapshot, :cogs_total_snapshot,
                :is_negative_stock_fallback, :stock_movement_id
            )
            RETURNING {_SALE_LINE_COLS}
            """,
            {
                "sale_id": sale_id,
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount_amount": discount_amount,
                "line_total": line_total,
                "line_number": line_number,
                "unit_cost_snapshot": unit_cost_snapshot,
                "cogs_total_snapshot": cogs_total_snapshot,
                "is_negative_stock_fallback": is_negative_stock_fallback,
                "stock_movement_id": stock_movement_id,
            },
        )
        return row

    async def update_line(
        self,
        sale_id: int,
        line_id: int,
        *,
        quantity: Decimal | None = None,
        unit_price: Decimal | None = None,
        discount_amount: Decimal | None = None,
        line_total: Decimal | None = None,
    ) -> dict[str, Any] | None:
        sets: list[str] = []
        params: dict[str, Any] = {"sid": sale_id, "lid": line_id}
        if quantity is not None:
            sets.append("quantity = :quantity")
            params["quantity"] = quantity
        if unit_price is not None:
            sets.append("unit_price = :unit_price")
            params["unit_price"] = unit_price
        if discount_amount is not None:
            sets.append("discount_amount = :discount_amount")
            params["discount_amount"] = discount_amount
        if line_total is not None:
            sets.append("line_total = :line_total")
            params["line_total"] = line_total
        if not sets:
            return await self.get_line(sale_id, line_id)
        return await self._uow.first_row(
            f"""
            UPDATE sale_lines SET {", ".join(sets)}
            WHERE id = :lid AND sale_id = :sid
            RETURNING {_SALE_LINE_COLS}
            """,
            params,
        )

    async def delete_line(self, sale_id: int, line_id: int) -> int:
        return int(
            await self._uow.scalar(
                "DELETE FROM sale_lines WHERE id = :lid AND sale_id = :sid",
                {"sid": sale_id, "lid": line_id},
            )
            or 0
        )

    async def sum_line_total(self, sale_id: int) -> Decimal:
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(line_total), 0) AS s FROM sale_lines WHERE sale_id = :id",
            {"id": sale_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    # ========================================================================
    # sale_payments
    # ========================================================================

    async def list_payments(
        self,
        sale_id: int,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "payment_date",
    ) -> tuple[builtins.list[dict[str, Any]], int]:
        order = _SALE_PAYMENT_SORT_MAP.get(sort, "payment_date")
        total = int(
            await self._uow.first_scalar(
                "SELECT COUNT(*) FROM sale_payments WHERE sale_id = :id",
                {"id": sale_id},
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_SALE_PAYMENT_COLS}
            FROM sale_payments
            WHERE sale_id = :id
            ORDER BY {order} ASC
            LIMIT :limit OFFSET :offset
            """,
            {
                "id": sale_id,
                "limit": per_page,
                "offset": (page - 1) * per_page,
            },
        )
        return rows, total

    async def get_payment(self, payment_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_SALE_PAYMENT_COLS} FROM sale_payments WHERE id = :id",
            {"id": payment_id},
        )

    async def insert_payment(
        self,
        *,
        sale_id: int,
        payment_method_id: int,
        amount: Decimal,
        tendered_amount: Decimal | None,
        change_amount: Decimal | None,
        payment_date: str | None,
        reference: str | None,
        created_by: int,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO sale_payments (
                sale_id, payment_method_id, amount,
                tendered_amount, change_amount,
                payment_date, reference, created_by
            ) VALUES (
                :sale_id, :payment_method_id, :amount,
                :tendered_amount, :change_amount,
                COALESCE(:payment_date, NOW()), :reference, :created_by
            )
            RETURNING {_SALE_PAYMENT_COLS}
            """,
            {
                "sale_id": sale_id,
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

    async def sum_payments(self, sale_id: int) -> Decimal:
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM sale_payments WHERE sale_id = :id",
            {"id": sale_id},
        )
        return Decimal(str((row or {"s": 0})["s"]))

    # ========================================================================
    # sales_returns + sales_return_lines
    # ========================================================================

    async def list_returns_for_sale(
        self,
        sale_id: int,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "return_date",
    ) -> tuple[builtins.list[dict[str, Any]], int]:
        order = _SALES_RETURN_SORT_MAP.get(sort, "return_date")
        total = int(
            await self._uow.first_scalar(
                "SELECT COUNT(*) FROM sales_returns WHERE sale_id = :id",
                {"id": sale_id},
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_SALES_RETURN_COLS}
            FROM sales_returns
            WHERE sale_id = :id
            ORDER BY {order} DESC
            LIMIT :limit OFFSET :offset
            """,
            {
                "id": sale_id,
                "limit": per_page,
                "offset": (page - 1) * per_page,
            },
        )
        return rows, total

    async def list_returns(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
        sale_id: int | None = None,
        customer_id: int | None = None,
        lifecycle_status: str | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[builtins.list[dict[str, Any]], int]:
        """Top-level ``GET /sales-returns`` list. ``customer_id`` is
        joined through the parent sale."""
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": per_page,
            "offset": (page - 1) * per_page,
        }
        if sale_id is not None:
            clauses.append("sr.sale_id = :sale_id")
            params["sale_id"] = sale_id
        if customer_id is not None:
            clauses.append("s.customer_id = :customer_id")
            params["customer_id"] = customer_id
        if lifecycle_status is not None:
            clauses.append("sr.lifecycle_status = :lifecycle_status")
            params["lifecycle_status"] = lifecycle_status
        if from_iso is not None:
            clauses.append("sr.return_date >= :from_iso")
            params["from_iso"] = from_iso
        if to_iso is not None:
            clauses.append("sr.return_date <= :to_iso")
            params["to_iso"] = to_iso
        order = _SALES_RETURN_SORT_MAP.get(sort, "id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(
            await self._uow.first_scalar(
                f"SELECT COUNT(*) FROM sales_returns sr "
                f"LEFT JOIN sales s ON s.id = sr.sale_id {where}",
                params,
            )
            or 0
        )
        # The outer query JOINs ``sales`` (which also has an ``id``
        # column). Use aliased projections so the row dict keeps the same
        # keys as the single-table callers (get_return, etc.).
        sr_cols = ", ".join(
            f"sr.{c.strip()} AS {c.strip()}" for c in _SALES_RETURN_COLS.split(",")
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {sr_cols}
            FROM sales_returns sr
            LEFT JOIN sales s ON s.id = sr.sale_id
            {where}
            ORDER BY sr.{order} DESC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return rows, total

    async def get_return(self, return_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_SALES_RETURN_COLS} FROM sales_returns WHERE id = :id",
            {"id": return_id},
        )

    async def get_return_for_update(self, return_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_SALES_RETURN_COLS} FROM sales_returns WHERE id = :id FOR UPDATE",
            {"id": return_id},
        )

    async def insert_return(
        self,
        *,
        sale_id: int,
        return_date: datetime | str | None,
        reason: str | None,
        total_selling_price_returned: Decimal,
        total_cost_returned: Decimal,
        created_by: int,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            f"""
            INSERT INTO sales_returns (
                sale_id, return_date, reason,
                total_selling_price_returned, total_cost_returned,
                lifecycle_status, created_by
            ) VALUES (
                :sale_id, COALESCE(:return_date, NOW()), :reason,
                :total_selling_price_returned, :total_cost_returned,
                'posted', :created_by
            )
            RETURNING {_SALES_RETURN_COLS}
            """,
            {
                "sale_id": sale_id,
                "return_date": return_date,
                "reason": reason,
                "total_selling_price_returned": total_selling_price_returned,
                "total_cost_returned": total_cost_returned,
                "created_by": created_by,
            },
        )
        return row

    async def cancel_return(
        self,
        return_id: int,
        *,
        cancelled_by: int,
        cancellation_date: datetime | str,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            UPDATE sales_returns
            SET lifecycle_status = 'cancelled',
                cancellation_date = :cancellation_date,
                cancelled_by = :cancelled_by
            WHERE id = :id
            RETURNING {_SALES_RETURN_COLS}
            """,
            {
                "cancellation_date": cancellation_date,
                "cancelled_by": cancelled_by,
                "id": return_id,
            },
        )

    async def list_return_lines(self, return_id: int) -> builtins.list[dict[str, Any]]:
        return await self._uow.fetch_all(
            f"SELECT {_SALES_RETURN_LINE_COLS} FROM sales_return_lines "
            f"WHERE sales_return_id = :id ORDER BY line_number ASC",
            {"id": return_id},
        )

    async def next_return_line_number(self, return_id: int) -> int:
        row = await self._uow.first_row(
            "SELECT COALESCE(MAX(line_number), 0) AS m FROM sales_return_lines "
            "WHERE sales_return_id = :id",
            {"id": return_id},
        )
        return int((row or {"m": 0})["m"]) + 1

    async def insert_return_line(
        self,
        *,
        sales_return_id: int,
        sale_line_id: int,
        product_id: int,
        quantity: Decimal,
        returned_selling_price: Decimal,
        returned_unit_cost: Decimal,
        line_number: int,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            INSERT INTO sales_return_lines (
                sales_return_id, sale_line_id, product_id,
                quantity, returned_selling_price, returned_unit_cost,
                line_number
            ) VALUES (
                :sales_return_id, :sale_line_id, :product_id,
                :quantity, :returned_selling_price, :returned_unit_cost,
                :line_number
            )
            RETURNING {_SALES_RETURN_LINE_COLS}
            """,
            {
                "sales_return_id": sales_return_id,
                "sale_line_id": sale_line_id,
                "product_id": product_id,
                "quantity": quantity,
                "returned_selling_price": returned_selling_price,
                "returned_unit_cost": returned_unit_cost,
                "line_number": line_number,
            },
        )

    async def sum_returned_qty_for_line(
        self, sale_line_id: int, *, exclude_return_id: int | None = None
    ) -> Decimal:
        params: dict[str, Any] = {"lid": sale_line_id}
        where_extra = ""
        if exclude_return_id is not None:
            where_extra = " AND sales_return_id <> :ex_id"
            params["ex_id"] = exclude_return_id
        row = await self._uow.first_row(
            f"SELECT COALESCE(SUM(quantity), 0) AS s FROM sales_return_lines "
            f"WHERE sale_line_id = :lid {where_extra}",
            params,
        )
        return Decimal(str((row or {"s": 0})["s"]))

    # ========================================================================
    # stock_movements (append-only inserts; reversals are new rows)
    # ========================================================================

    async def insert_stock_movement(
        self,
        *,
        product_id: int,
        trigger: str,
        quantity: Decimal,
        unit_cost_at_movement: Decimal | None,
        total_cost: Decimal | None,
        reference_type: str | None,
        reference_id: int | None,
        reference_line_id: int | None,
        reversal_of_movement_id: int | None = None,
        reason: str | None = None,
        created_by: int,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            """
            INSERT INTO stock_movements (
                product_id, trigger, quantity,
                unit_cost_at_movement, total_cost,
                reference_type, reference_id, reference_line_id,
                reversal_of_movement_id, reason, created_by
            ) VALUES (
                :product_id, :trigger, :quantity,
                :unit_cost_at_movement, :total_cost,
                :reference_type, :reference_id, :reference_line_id,
                :reversal_of_movement_id, :reason, :created_by
            )
            RETURNING id
            """,
            {
                "product_id": product_id,
                "trigger": trigger,
                "quantity": quantity,
                "unit_cost_at_movement": unit_cost_at_movement,
                "total_cost": total_cost,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "reference_line_id": reference_line_id,
                "reversal_of_movement_id": reversal_of_movement_id,
                "reason": reason,
                "created_by": created_by,
            },
        )

    async def get_stock_movement(self, movement_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            "SELECT * FROM stock_movements WHERE id = :id",
            {"id": movement_id},
        )

    # ========================================================================
    # cash_movements (append-only inserts)
    # ========================================================================

    async def insert_cash_movement(
        self,
        *,
        amount: Decimal,
        direction: str,
        trigger: str,
        payment_method_id: int,
        reference_type: str | None,
        reference_id: int | None,
        created_by: int,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            """
            INSERT INTO cash_movements (
                amount, direction, trigger,
                payment_method_id, reference_type, reference_id, created_by
            ) VALUES (
                :amount, :direction, :trigger,
                :payment_method_id, :reference_type, :reference_id, :created_by
            )
            RETURNING id
            """,
            {
                "amount": amount,
                "direction": direction,
                "trigger": trigger,
                "payment_method_id": payment_method_id,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "created_by": created_by,
            },
        )

    # ========================================================================
    # cash balance (read)
    # ========================================================================

    async def current_cash_balance(self) -> Decimal:
        row = await self._uow.first_row("SELECT COALESCE(SUM(amount), 0) AS s FROM cash_movements")
        return Decimal(str((row or {"s": 0})["s"]))

    # ========================================================================
    # helpers used by service
    # ========================================================================

    async def payment_method_is_cash(self, payment_method_id: int) -> bool:
        row = await self._uow.first_row(
            "SELECT is_cash FROM payment_methods WHERE id = :id",
            {"id": payment_method_id},
        )
        if row is None:
            return False
        return bool(row["is_cash"])

    async def product_exists(self, product_id: int) -> bool:
        row = await self._uow.first_row("SELECT 1 FROM products WHERE id = :id", {"id": product_id})
        return row is not None

    async def product_is_sellable(self, product_id: int) -> bool:
        row = await self._uow.first_row(
            "SELECT is_sellable, is_active FROM products WHERE id = :id",
            {"id": product_id},
        )
        if row is None:
            return False
        return bool(row["is_sellable"]) and bool(row["is_active"])

    async def product_selling_price(self, product_id: int) -> Decimal | None:
        row = await self._uow.first_row(
            "SELECT selling_price FROM products WHERE id = :id",
            {"id": product_id},
        )
        if row is None:
            return None
        return Decimal(str(row["selling_price"]))

    async def product_allow_negative_stock(self, product_id: int) -> bool | None:
        row = await self._uow.first_row(
            "SELECT allow_negative_stock FROM products WHERE id = :id",
            {"id": product_id},
        )
        if row is None:
            return None
        v = row["allow_negative_stock"]
        return None if v is None else bool(v)

    async def contact_exists(self, contact_id: int) -> bool:
        row = await self._uow.first_row("SELECT 1 FROM contacts WHERE id = :id", {"id": contact_id})
        return row is not None

    async def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            "SELECT id, name, type, email, phone, address, is_active FROM contacts WHERE id = :id",
            {"id": contact_id},
        )

    async def on_hand_quantity(self, product_id: int) -> Decimal:
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(quantity), 0) AS q FROM stock_movements WHERE product_id = :id",
            {"id": product_id},
        )
        return Decimal(str((row or {"q": 0})["q"]))

    async def moving_average_unit_cost(self, product_id: int) -> Decimal | None:
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(total_cost), 0) AS tc, "
            "COALESCE(SUM(quantity), 0) AS q "
            "FROM stock_movements "
            "WHERE product_id = :id AND quantity > 0 AND total_cost IS NOT NULL",
            {"id": product_id},
        )
        if row is None:
            return None
        q = Decimal(str(row["q"]))
        tc = Decimal(str(row["tc"]))
        if q == 0:
            return None
        return tc / q

    async def get_setting_bool(self, key: str, *, default: bool = False) -> bool:
        row = await self._uow.first_row(
            "SELECT value, value_type FROM system_settings WHERE key = :k",
            {"k": key},
        )
        if row is None:
            return default
        return str(row["value"]).strip().lower() in ("true", "1", "t", "yes", "on")


__all__ = ["SaleRepository"]
