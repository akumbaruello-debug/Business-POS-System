"""Data-access layer for the production-runs domain (M5 / Phase E.1).

Per ``schema.sql`` §9.1-9.4 + ``openapi.yaml`` (Production Runs /
Inputs / Cost Lines / Outputs sections), this module is **raw SQL only**,
UoW-scoped (no ORM per ``Backend-Architecture-V1.0.md`` §2/§10).

Tables covered in E.1:

* ``production_runs``      — header (E.1 draft CRUD)
* ``production_inputs``    — raw materials (snapshot on create; also
                              written via this repo because ``ProductionRunCreateRequest``
                              carries ``inputs[]`` inline per openapi.yaml)
* ``production_outputs``   — finished good row (one per run; written on
                              create as a denormalised snapshot of output qty/cost;
                              updated on every draft patch)
* ``production_cost_lines``— overhead cost lines (snapshot on create; also
                              inline in ``ProductionRunCreateRequest``)

Lines-only sub-resource endpoints (``/inputs``, ``/cost-lines`` POST/PATCH/DELETE)
are E.2/E.3 scope and are NOT implemented here. This repository exposes the
row-level helpers that the E.1 service uses for the inline create + draft
update + draft read.

Invariant enforcement is in DB triggers:

* ``fn_production_lifecycle_terminal`` — illegal transitions rejected.
* ``fn_production_lines_immutable_when_posted`` — lines become immutable once
  the parent run leaves draft (POST/cancel, etc.). This means E.1 mutations
  to lines (via inline create/patch) are always allowed because the run is
  in draft at that point.
* ``fn_production_runs_bump_version`` — bumps ``version`` on every UPDATE.
* UNIQUE(line_number) per run on inputs / cost_lines.
* CHECK constraints on quantites / costs.

Services add pre-checks for clean error envelopes before the DB sees the
mutation (e.g. ``products.is_producible = TRUE`` for the output product,
``is_active = TRUE``, lifecycle-draft guard for PATCH).
"""

# ruff: noqa: S608
from __future__ import annotations

import builtins
from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db import UnitOfWork


# Column lists reused across SELECTs.
_RUN_COLS = (
    "id, run_date, output_product_id, output_quantity, finished_unit_cost, "
    "total_raw_cost, total_overhead_cost, lifecycle_status, posted_at, "
    "posted_by, cancellation_date, cancellation_reason, cancelled_by, "
    "notes, created_at, updated_at, created_by, version"
)

_INPUT_COLS = (
    "id, production_run_id, product_id, quantity, unit_cost_snapshot, "
    "line_cost, line_number"
)

_OUTPUT_COLS = (
    "id, production_run_id, product_id, quantity, unit_cost_snapshot, "
    "total_cost"
)

_COST_LINE_COLS = (
    "id, production_run_id, cost_type_id, description, amount, "
    "paid_in_cash, line_number"
)

# Closed-whitelist sort fields for the production_runs list endpoint.
_RUN_SORT_MAP: dict[str, str] = {
    "id": "id",
    "run_date": "run_date",
    "lifecycle_status": "lifecycle_status",
    "created_at": "created_at",
    "updated_at": "updated_at",
}


class ProductionRunRepository:
    """Raw-SQL repository for the production-runs domain. UoW-scoped."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ========================================================================
    # production_runs (header)
    # ========================================================================

    async def get(self, run_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_RUN_COLS} FROM production_runs WHERE id = :id",
            {"id": run_id},
        )

    async def get_for_update(self, run_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_RUN_COLS} FROM production_runs WHERE id = :id FOR UPDATE",
            {"id": run_id},
        )

    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        sort: str = "id",
        lifecycle_status: str | None = None,
        output_product_id: int | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[Sequence[dict[str, Any]], int]:
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": per_page,
            "offset": (page - 1) * per_page,
        }
        if lifecycle_status is not None:
            clauses.append("lifecycle_status = :lifecycle_status")
            params["lifecycle_status"] = lifecycle_status
        if output_product_id is not None:
            clauses.append("output_product_id = :output_product_id")
            params["output_product_id"] = output_product_id
        if from_iso is not None:
            clauses.append("run_date >= :from_iso")
            params["from_iso"] = from_iso
        if to_iso is not None:
            clauses.append("run_date <= :to_iso")
            params["to_iso"] = to_iso
        # ``q`` is documented in the OpenAPI list params but not bound to a
        # specific column — match against the notes (nullable) so a draft
        # search can find runs by free-text note.
        if q:
            clauses.append("COALESCE(notes, '') ILIKE :q")
            params["q"] = f"%{q}%"
        order = _RUN_SORT_MAP.get(sort, "id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        total = int(
            await self._uow.first_scalar(
                f"SELECT COUNT(*) FROM production_runs {where}",
                params,
            )
            or 0
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT {_RUN_COLS} FROM production_runs
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
        run_date: Any,
        output_product_id: int,
        output_quantity: Decimal,
        finished_unit_cost: Decimal,
        total_raw_cost: Decimal,
        total_overhead_cost: Decimal,
        notes: str | None,
        created_by: int,
    ) -> dict[str, Any] | None:
        """Insert a draft run.

        The DB default ``lifecycle_status = 'draft'`` and ``version = 1`` are
        used; ``posted_at`` defaults to NULL (consistent with the draft
        lifecycle invariant enforced by ``ck_prun_posted_pair``).
        """
        return await self._uow.first_row(
            f"""
            INSERT INTO production_runs (
                run_date, output_product_id, output_quantity,
                finished_unit_cost, total_raw_cost, total_overhead_cost,
                notes, created_by
            ) VALUES (
                COALESCE(:run_date, NOW()), :output_product_id, :output_quantity,
                :finished_unit_cost, :total_raw_cost, :total_overhead_cost,
                :notes, :created_by
            )
            RETURNING {_RUN_COLS}
            """,
            {
                "run_date": run_date,
                "output_product_id": output_product_id,
                "output_quantity": output_quantity,
                "finished_unit_cost": finished_unit_cost,
                "total_raw_cost": total_raw_cost,
                "total_overhead_cost": total_overhead_cost,
                "notes": notes,
                "created_by": created_by,
            },
        )

    async def update_draft(
        self,
        run_id: int,
        *,
        run_date: Any,
        output_product_id: int | None,
        output_quantity: Decimal | None,
        notes: str | None,
        # Caller-supplied recomputed totals (the service recomputes these
        # before each UPDATE because inputs/cost_lines may have changed since
        # create; ``version`` will be bumped by the DB trigger).
        finished_unit_cost: Decimal | None = None,
        total_raw_cost: Decimal | None = None,
        total_overhead_cost: Decimal | None = None,
    ) -> dict[str, Any] | None:
        """Update the editable fields of a draft run.

        Lifecycle state is NEVER mutated here — that belongs to E.3 (post)
        and E.4 (cancel). ``updated_at`` is bumped by the DB trigger.
        """
        sets: list[str] = []
        params: dict[str, Any] = {"id": run_id}
        if run_date is not None:
            sets.append("run_date = :run_date")
            params["run_date"] = run_date
        if output_product_id is not None:
            sets.append("output_product_id = :output_product_id")
            params["output_product_id"] = output_product_id
        if output_quantity is not None:
            sets.append("output_quantity = :output_quantity")
            params["output_quantity"] = output_quantity
        if notes is not None:
            sets.append("notes = :notes")
            params["notes"] = notes
        if finished_unit_cost is not None:
            sets.append("finished_unit_cost = :finished_unit_cost")
            params["finished_unit_cost"] = finished_unit_cost
        if total_raw_cost is not None:
            sets.append("total_raw_cost = :total_raw_cost")
            params["total_raw_cost"] = total_raw_cost
        if total_overhead_cost is not None:
            sets.append("total_overhead_cost = :total_overhead_cost")
            params["total_overhead_cost"] = total_overhead_cost
        if not sets:
            # Nothing to update; return current row.
            return await self.get(run_id)
        set_clause = ", ".join(sets)
        return await self._uow.first_row(
            f"""
            UPDATE production_runs SET {set_clause}
            WHERE id = :id
            RETURNING {_RUN_COLS}
            """,
            params,
        )

    # ========================================================================
    # production_inputs (raw materials — denormalised into the create body)
    # ========================================================================

    async def next_input_line_number(self, run_id: int) -> int:
        """Return the next ``line_number`` for the given run (1-based)."""
        n = await self._uow.first_scalar(
            "SELECT COALESCE(MAX(line_number), 0) + 1 FROM production_inputs "
            "WHERE production_run_id = :id",
            {"id": run_id},
        )
        return int(n or 1)

    async def insert_input(
        self,
        *,
        run_id: int,
        product_id: int,
        quantity: Decimal,
        unit_cost_snapshot: Decimal,
        line_cost: Decimal,
        line_number: int,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            INSERT INTO production_inputs (
                production_run_id, product_id, quantity,
                unit_cost_snapshot, line_cost, line_number
            ) VALUES (
                :run_id, :product_id, :quantity,
                :unit_cost_snapshot, :line_cost, :line_number
            )
            RETURNING {_INPUT_COLS}
            """,
            {
                "run_id": run_id,
                "product_id": product_id,
                "quantity": quantity,
                "unit_cost_snapshot": unit_cost_snapshot,
                "line_cost": line_cost,
                "line_number": line_number,
            },
        )

    async def list_inputs(
        self, run_id: int
    ) -> Sequence[dict[str, Any]]:
        return await self._uow.fetch_all(
            f"""
            SELECT {_INPUT_COLS} FROM production_inputs
            WHERE production_run_id = :id
            ORDER BY line_number ASC
            """,
            {"id": run_id},
        )

    async def sum_input_line_cost(self, run_id: int) -> Decimal:
        v = await self._uow.first_scalar(
            "SELECT COALESCE(SUM(line_cost), 0) FROM production_inputs "
            "WHERE production_run_id = :id",
            {"id": run_id},
        )
        return Decimal(str(v or 0))

    async def get_input(self, run_id: int, input_id: int) -> dict[str, Any] | None:
        """Fetch a single input line scoped to its parent run."""
        return await self._uow.first_row(
            f"SELECT {_INPUT_COLS} FROM production_inputs "
            "WHERE id = :iid AND production_run_id = :rid",
            {"iid": input_id, "rid": run_id},
        )

    async def update_input(
        self,
        run_id: int,
        input_id: int,
        *,
        product_id: int | None = None,
        quantity: Decimal | None = None,
        unit_cost_snapshot: Decimal | None = None,
        line_cost: Decimal | None = None,
    ) -> dict[str, Any] | None:
        """Update editable columns of a single input line.

        ``line_cost`` is recomputed by the caller (service layer) from the
        new ``unit_cost_snapshot * quantity``; we expose it so the service
        can keep derived state consistent without duplicating SQL.
        """
        sets: list[str] = []
        params: dict[str, Any] = {"rid": run_id, "iid": input_id}
        if product_id is not None:
            sets.append("product_id = :product_id")
            params["product_id"] = product_id
        if quantity is not None:
            sets.append("quantity = :quantity")
            params["quantity"] = quantity
        if unit_cost_snapshot is not None:
            sets.append("unit_cost_snapshot = :unit_cost_snapshot")
            params["unit_cost_snapshot"] = unit_cost_snapshot
        if line_cost is not None:
            sets.append("line_cost = :line_cost")
            params["line_cost"] = line_cost
        if not sets:
            return await self.get_input(run_id, input_id)
        return await self._uow.first_row(
            f"""
            UPDATE production_inputs SET {", ".join(sets)}
            WHERE id = :iid AND production_run_id = :rid
            RETURNING {_INPUT_COLS}
            """,
            params,
        )

    async def delete_input(self, run_id: int, input_id: int) -> int:
        """Delete a single input line (draft-only enforced by the service
        pre-check + DB trigger backstop). Renumber is handled by the
        service so the recompute pass stays consistent."""
        return int(
            await self._uow.scalar(
                "DELETE FROM production_inputs "
                "WHERE id = :iid AND production_run_id = :rid",
                {"iid": input_id, "rid": run_id},
            )
            or 0
        )

    async def renumber_inputs(self, run_id: int) -> None:
        """Re-number remaining input lines to a contiguous 1..N sequence."""
        await self._uow.execute(
            "UPDATE production_inputs SET line_number = sub.rn "
            "FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY line_number) AS rn "
            "FROM production_inputs WHERE production_run_id = :rid) sub "
            "WHERE production_inputs.id = sub.id",
            {"rid": run_id},
        )

    # ========================================================================
    # production_outputs (finished-goods row — one per run)
    # ========================================================================

    async def get_output(self, run_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"SELECT {_OUTPUT_COLS} FROM production_outputs "
            "WHERE production_run_id = :id",
            {"id": run_id},
        )

    async def upsert_output(
        self,
        *,
        run_id: int,
        product_id: int,
        quantity: Decimal,
        unit_cost_snapshot: Decimal,
        total_cost: Decimal,
    ) -> dict[str, Any] | None:
        """Insert or update the single output row for the run.

        There is a UNIQUE(production_run_id) constraint, so this is a
        proper upsert: ON CONFLICT on production_run_id.
        """
        return await self._uow.first_row(
            f"""
            INSERT INTO production_outputs (
                production_run_id, product_id, quantity,
                unit_cost_snapshot, total_cost
            ) VALUES (
                :run_id, :product_id, :quantity,
                :unit_cost_snapshot, :total_cost
            )
            ON CONFLICT (production_run_id) DO UPDATE SET
                product_id         = EXCLUDED.product_id,
                quantity           = EXCLUDED.quantity,
                unit_cost_snapshot = EXCLUDED.unit_cost_snapshot,
                total_cost         = EXCLUDED.total_cost
            RETURNING {_OUTPUT_COLS}
            """,
            {
                "run_id": run_id,
                "product_id": product_id,
                "quantity": quantity,
                "unit_cost_snapshot": unit_cost_snapshot,
                "total_cost": total_cost,
            },
        )

    # ========================================================================
    # production_cost_lines (overhead — denormalised into the create body)
    # ========================================================================

    async def next_cost_line_number(self, run_id: int) -> int:
        n = await self._uow.first_scalar(
            "SELECT COALESCE(MAX(line_number), 0) + 1 FROM production_cost_lines "
            "WHERE production_run_id = :id",
            {"id": run_id},
        )
        return int(n or 1)

    async def insert_cost_line(
        self,
        *,
        run_id: int,
        cost_type_id: int,
        description: str | None,
        amount: Decimal,
        paid_in_cash: bool,
        line_number: int,
    ) -> dict[str, Any] | None:
        return await self._uow.first_row(
            f"""
            INSERT INTO production_cost_lines (
                production_run_id, cost_type_id, description,
                amount, paid_in_cash, line_number
            ) VALUES (
                :run_id, :cost_type_id, :description,
                :amount, :paid_in_cash, :line_number
            )
            RETURNING {_COST_LINE_COLS}
            """,
            {
                "run_id": run_id,
                "cost_type_id": cost_type_id,
                "description": description,
                "amount": amount,
                "paid_in_cash": paid_in_cash,
                "line_number": line_number,
            },
        )

    async def list_cost_lines(
        self, run_id: int
    ) -> Sequence[dict[str, Any]]:
        return await self._uow.fetch_all(
            f"""
            SELECT {_COST_LINE_COLS} FROM production_cost_lines
            WHERE production_run_id = :id
            ORDER BY line_number ASC
            """,
            {"id": run_id},
        )

    async def sum_cost_line_amount(self, run_id: int) -> Decimal:
        v = await self._uow.first_scalar(
            "SELECT COALESCE(SUM(amount), 0) FROM production_cost_lines "
            "WHERE production_run_id = :id",
            {"id": run_id},
        )
        return Decimal(str(v or 0))

    async def get_cost_line(self, run_id: int, line_id: int) -> dict[str, Any] | None:
        """Fetch a single cost-line scoped to its parent run."""
        return await self._uow.first_row(
            f"SELECT {_COST_LINE_COLS} FROM production_cost_lines "
            "WHERE id = :lid AND production_run_id = :rid",
            {"lid": line_id, "rid": run_id},
        )

    async def update_cost_line(
        self,
        run_id: int,
        line_id: int,
        *,
        cost_type_id: int | None = None,
        description: str | None,
        amount: Decimal | None = None,
        paid_in_cash: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update editable columns of a single cost line.

        ``description`` is always set (even to NULL) so clients can
        clear it; the other columns are SET only when not None.
        """
        sets: list[str] = ["description = :description"]
        params: dict[str, Any] = {
            "rid": run_id,
            "iid": line_id,
            "description": description,
        }
        if cost_type_id is not None:
            sets.append("cost_type_id = :cost_type_id")
            params["cost_type_id"] = cost_type_id
        if amount is not None:
            sets.append("amount = :amount")
            params["amount"] = amount
        if paid_in_cash is not None:
            sets.append("paid_in_cash = :paid_in_cash")
            params["paid_in_cash"] = paid_in_cash
        return await self._uow.first_row(
            f"""
            UPDATE production_cost_lines SET {", ".join(sets)}
            WHERE id = :iid AND production_run_id = :rid
            RETURNING {_COST_LINE_COLS}
            """,
            params,
        )

    async def delete_cost_line(self, run_id: int, line_id: int) -> int:
        """Delete a single cost-line (draft-only enforced by the service
        pre-check + DB trigger backstop)."""
        return int(
            await self._uow.scalar(
                "DELETE FROM production_cost_lines "
                "WHERE id = :iid AND production_run_id = :rid",
                {"iid": line_id, "rid": run_id},
            )
            or 0
        )

    async def renumber_cost_lines(self, run_id: int) -> None:
        """Re-number remaining cost lines to a contiguous 1..N sequence."""
        await self._uow.execute(
            "UPDATE production_cost_lines SET line_number = sub.rn "
            "FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY line_number) AS rn "
            "FROM production_cost_lines WHERE production_run_id = :rid) sub "
            "WHERE production_cost_lines.id = sub.id",
            {"rid": run_id},
        )

    # ========================================================================
    # Reference-data lookups used during validation
    # ========================================================================

    async def get_product(self, product_id: int) -> dict[str, Any] | None:
        """Fetch the slim product fields needed to validate producibility.

        Returns ``None`` if the product does not exist.
        """
        return await self._uow.first_row(
            "SELECT id, is_producible, is_active FROM products "
            "WHERE id = :id",
            {"id": product_id},
        )

    async def get_cost_type(self, cost_type_id: int) -> dict[str, Any] | None:
        """Fetch the cost_type fields needed for header validation.

        Returns ``None`` if the cost_type does not exist.
        """
        return await self._uow.first_row(
            "SELECT id, is_active FROM cost_types WHERE id = :id",
            {"id": cost_type_id},
        )

    async def get_input_unit_cost_snapshot(self, product_id: int) -> Decimal:
        """Snapshot the moving-avg unit cost for a raw-material product.

        Mirrors ``app/inventory/valuation.py::compute_moving_average_unit_cost``
        but uses the same SQL the valuation helper would build, so the
        draft snapshot is computed off the same ledger the post-time
        write-back will be.

        Falls back to ``0`` if there is no stock yet — the draft snapshot
        reflects "no cost basis available yet" (mirrors the valuation
        helper's NULL → we store 0 here so the CHECK constraints pass;
        the draft's finished_unit_cost will be recomputed at post time).
        """
        row = await self._uow.first_row(
            """
            SELECT on_hand_quantity, inventory_value
            FROM product_valuation
            WHERE product_id = :id
            """,
            {"id": product_id},
        )
        if row is None:
            return Decimal("0")
        on_hand = row.get("on_hand_quantity")
        inv_value = row.get("inventory_value")
        if on_hand is None or inv_value is None:
            return Decimal("0")
        try:
            qty = float(on_hand)
        except (TypeError, ValueError):
            return Decimal("0")
        if qty == 0:
            return Decimal("0")
        try:
            return Decimal(str(float(inv_value) / qty)).quantize(
                Decimal("0.0001")
            )
        except (TypeError, ValueError, ArithmeticError):
            return Decimal("0")

    # ========================================================================
    # E.3 — Production post lifecycle helpers
    # ========================================================================

    async def lock_product_for_update(self, product_id: int) -> dict[str, Any] | None:
        """``SELECT ... FOR UPDATE`` on the product row.

        Per Backend-Architecture §15.8 step 1 ("Lock products in ASC order")
        — locks are acquired inside a single transaction to serialise
        concurrent production posts that consume the same raw product.
        Asc-order discipline is the caller's responsibility (sorted
        iteration in the service).
        """
        return await self._uow.first_row(
            "SELECT id, is_active, allow_negative_stock "
            "FROM products WHERE id = :id FOR UPDATE",
            {"id": product_id},
        )

    async def on_hand_quantity(self, product_id: int) -> Decimal:
        """Sum of signed ``stock_movements.quantity`` for the product.

        Mirrors the helper in ``app/repositories/sales.py``. Reads run
        inside the same transaction as the ``FOR UPDATE`` lock on the
        product row so concurrent posts on the same raw material are
        serialised (the lock blocks any other inserter until COMMIT).
        """
        row = await self._uow.first_row(
            "SELECT COALESCE(SUM(quantity), 0) AS q "
            "FROM stock_movements WHERE product_id = :id",
            {"id": product_id},
        )
        return Decimal(str((row or {"q": 0})["q"]))

    async def product_allow_negative_stock(self, product_id: int) -> bool | None:
        """Per-product ``allow_negative_stock`` override (NULL → global default).

        Mirrors the sales-side helper. Returns ``None`` when the column
        is ``NULL`` so the caller can fall back to the global
        ``default_negative_stock_allowed`` setting.
        """
        row = await self._uow.first_row(
            "SELECT allow_negative_stock FROM products WHERE id = :id",
            {"id": product_id},
        )
        if row is None:
            return None
        v = row["allow_negative_stock"]
        return None if v is None else bool(v)

    async def insert_stock_movement(
        self,
        *,
        product_id: int,
        trigger: str,
        quantity: Decimal,
        unit_cost_at_movement: Decimal | None,
        total_cost: Decimal | None,
        reference_type: str,
        reference_id: int,
        reference_line_id: int | None = None,
        reversal_of_movement_id: int | None = None,
        reason: str | None = None,
        created_by: int,
    ) -> dict[str, Any] | None:
        """Insert a ``stock_movements`` row inside the active UoW.

        Append-only per the immutability trigger; the post service owns
        the lifecycle so cancellations route through a separate
        reversal-of insert (E.4 — out of scope here).
        """
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

    async def insert_cash_movement(
        self,
        *,
        amount: Decimal,
        direction: str,
        trigger: str,
        payment_method_id: int,
        reference_type: str,
        reference_id: int,
        created_by: int,
    ) -> dict[str, Any] | None:
        """Insert a ``cash_movements`` row inside the active UoW.

        Per DB spec §2.10.1, the pre-insert
        ``fn_cash_movements_balance_check`` trigger enforces
        ``Cash >= 0`` (INV-03). When the row would push the balance
        negative, the trigger raises ``check_violation`` and the
        transaction rolls back — there is no application-layer bypass.
        """
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

    async def post_lifecycle(
        self,
        run_id: int,
        *,
        posted_by: int,
        posted_at: Any,
        finished_unit_cost: Decimal,
        total_raw_cost: Decimal,
        total_overhead_cost: Decimal,
    ) -> dict[str, Any] | None:
        """Flip ``lifecycle_status`` draft → posted with the post-time totals.

        The DB trigger ``fn_production_lifecycle_terminal`` enforces the
        allowed transition (draft → posted | cancelled). The version
        bump is automatic via ``fn_bump_version_and_updated_at``.
        ``posted_at`` MUST be set to a non-NULL value because of
        ``ck_prun_posted_pair``.
        """
        return await self._uow.first_row(
            f"""
            UPDATE production_runs SET
                lifecycle_status     = 'posted',
                posted_by            = :posted_by,
                posted_at            = :posted_at,
                finished_unit_cost   = :finished_unit_cost,
                total_raw_cost       = :total_raw_cost,
                total_overhead_cost  = :total_overhead_cost
            WHERE id = :id
            RETURNING {_RUN_COLS}
            """,
            {
                "id": run_id,
                "posted_by": posted_by,
                "posted_at": posted_at,
                "finished_unit_cost": finished_unit_cost,
                "total_raw_cost": total_raw_cost,
                "total_overhead_cost": total_overhead_cost,
            },
        )

    async def get_setting_bool(self, key: str, *, default: bool = False) -> bool:
        """Read a boolean ``system_settings`` row (per BR-STOCK-011 fallback)."""
        row = await self._uow.first_row(
            "SELECT value, value_type FROM system_settings WHERE key = :k",
            {"k": key},
        )
        if row is None:
            return default
        return str(row["value"]).strip().lower() in ("true", "1", "t", "yes", "on")


    # ========================================================================
    # E.4 — Production cancel lifecycle helpers
    # ========================================================================

    # Column list for the stock_movements rows we need to reverse at cancel time.
    # We fetch movements whose parent run produced the original input/output
    # rows (trigger IN 'production_input' / 'production_output') that have not
    # already been reversed (reversed_by_movement_id IS NULL).
    _CANCEL_MOVEMENT_COLS = (
        "id, product_id, trigger, quantity, unit_cost_at_movement, "
        "total_cost, reference_type, reference_id"
    )

    async def list_posted_movements_for_run(self, run_id: int) -> builtins.list[dict[str, Any]]:
        """Return the original stock_movements produced at post time that
        have not yet been reversed.

        These are the ``production_input`` (negative qty) and
        ``production_output`` (positive qty) rows for this run.  Cancel
        inserts a ``production_reversal`` row for each.
        """
        rows = await self._uow.fetch_all(
            f"""
            SELECT {self._CANCEL_MOVEMENT_COLS}
            FROM stock_movements
            WHERE reference_type = 'production_run'
              AND reference_id = :rid
              AND trigger IN ('production_input', 'production_output')
              AND reversed_by_movement_id IS NULL
            ORDER BY id
            """,
            {"rid": run_id},
        )
        return rows or []

    async def insert_reversal_movement(
        self,
        *,
        product_id: int,
        trigger: str,
        quantity: Decimal,
        unit_cost_at_movement: Decimal | None,
        total_cost: Decimal | None,
        reference_type: str,
        reference_id: int,
        reversal_of_movement_id: int,
        created_by: int,
    ) -> dict[str, Any] | None:
        """Insert a ``production_reversal`` stock_movement row.

        This is the append-only reversal mechanism: a *new* row whose
        ``reversal_of_movement_id`` points at the original, and which the
        original's ``reversed_by_movement_id`` references back.  Per DB
        trigger / constraint rules the original row is never UPDATEd.
        """
        return await self._uow.first_row(
            """
            INSERT INTO stock_movements (
                product_id, trigger, quantity,
                unit_cost_at_movement, total_cost,
                reference_type, reference_id,
                reversal_of_movement_id, created_by
            ) VALUES (
                :product_id, :trigger, :quantity,
                :unit_cost_at_movement, :total_cost,
                :reference_type, :reference_id,
                :reversal_of_movement_id, :created_by
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
                "reversal_of_movement_id": reversal_of_movement_id,
                "created_by": created_by,
            },
        )

    async def cancel_lifecycle(
        self,
        run_id: int,
        *,
        cancelled_by: int,
        cancellation_date: Any,
        cancellation_reason: str,
    ) -> dict[str, Any] | None:
        """Flip ``lifecycle_status`` to ``cancelled`` + set cancellation fields.

        The DB trigger ``fn_production_lifecycle_terminal`` enforces the
        transition (draft→cancelled, posted→cancelled); the service layer
        pre-checks so we can return a clean 409 ``already_cancelled`` /
        ``lifecycle_state_invalid`` instead of a raw constraint error.
        ``version`` is bumped automatically by ``fn_production_runs_bump_version``.
        """
        return await self._uow.first_row(
            f"""
            UPDATE production_runs SET
                lifecycle_status    = 'cancelled',
                cancellation_date   = :cancellation_date,
                cancellation_reason = :cancellation_reason,
                cancelled_by        = :cancelled_by
            WHERE id = :id
            RETURNING {_RUN_COLS}
            """,
            {
                "id": run_id,
                "cancellation_date": cancellation_date,
                "cancellation_reason": cancellation_reason,
                "cancelled_by": cancelled_by,
            },
        )


__all__ = ["ProductionRunRepository"]
