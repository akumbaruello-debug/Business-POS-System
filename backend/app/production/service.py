"""Business logic for the Production Runs draft lifecycle (M5 / Phase E.1).

Per ``Backend-Architecture-V1.0.md`` §15.10-§15.12 + ``openapi.yaml`` §15,
the service is the transaction boundary. Every draft mutation runs inside
one ``UnitOfWork`` so the business mutation + audit log share one
``COMMIT``.

Production lifecycle (per PRD §15 + DB trigger
``fn_production_lifecycle_terminal``):

    draft -> posted | cancelled
    posted -> completed | cancelled
    cancelled -> (terminal)

E.1 scope (this file):

* ``listProductionRuns`` — list with filters + pagination
* ``createProductionRun`` — create draft (with inline inputs + cost lines)
* ``getProductionRun`` — read with required inputs/output/cost_lines arrays
* ``updateProductionRunDraft`` — PATCH the editable draft fields

NOT in E.1 (deferred to E.2 / E.3 / E.4):

* Sub-resource endpoints (``/inputs``, ``/cost-lines`` POST/PATCH/DELETE)
* Posting (raw down, finished up, cash for paid overhead, finished_unit_cost
  recompute per Arch §15.8)
* Cancellation reversal (overhead cash NOT reversed per Arch §15.9)

DB triggers enforce the hard invariants:

* ``fn_production_lifecycle_terminal`` — illegal transitions rejected.
* ``fn_production_lines_immutable_when_posted`` — once a run leaves draft,
  its inputs / outputs / cost_lines become immutable.
* ``fn_production_runs_bump_version`` — bumps ``version`` + ``updated_at``
  on every UPDATE (used for ETag).
* CHECK constraints on quantities / costs (positive; closed lifecycle set).

The service does pre-checks before the DB sees the mutation so we return
clean, documented 4xx/409 error codes (with the right ``code`` field)
instead of opaque constraint violations. The DB trigger is the final
backstop for concurrency races.

Critical rule (PRD §15 + plan §5 E.1): the output product must satisfy
``products.is_producible = TRUE`` (and ``is_active = TRUE`` for the
non-regression semantic). This is enforced at the service layer in
``create_draft`` and ``update_draft`` (when the output product changes).
The DB does NOT enforce it directly — the rule is a business invariant
not a schema invariant, so it lives here.

The DB does, however, enforce ``fk_production_runs_output_product`` (FK
to ``products.id``) so an invalid product_id still yields a 4xx but with
the generic ``not_found`` shape (via the SQL state machine in handlers).

ETag derivation: ``etag_and_version_from_updated_at`` from
``app/concurrency/master_etag.py`` (the same helper used by M4
purchases/sales/refunds). If-Match is required for PATCH; the service
checks it against the row read with ``FOR UPDATE`` inside the same
transaction.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.audit.service import (
    ENTITY_PRODUCTION_COST_LINES,
    ENTITY_PRODUCTION_INPUTS,
    ENTITY_PRODUCTION_RUNS,
    AuditContext,
    write_audit,
)
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import (
    BusinessRuleViolation,
    Conflict,
    InsufficientCash,
    LifecycleStateInvalid,
    NotFound,
    ProductionFinishedCostInvalid,
    ProductionInputsExceedStock,
)
from app.logging import get_logger
from app.production.repo import ProductionRunRepository
from app.services.idempotency import (
    IdempotencyStore,
    compute_request_fingerprint,
)
from app.validation.enums import AuditAction, LifecycleStatus

logger = get_logger(__name__)


# Two-decimal quant for all monetary results (matches schema.sql NUMERIC(15,2)).
_2DP = Decimal("0.01")
# Four-decimal quant for unit-cost snapshots (matches NUMERIC(15,4)).
_4DP = Decimal("0.0001")


def _q2(x: Any) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x)).quantize(_2DP)


def _q4(x: Any) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x)).quantize(_4DP)


def _to_jsonb(values: dict[str, Any] | None) -> str | None:
    if values is None:
        return None
    return json.dumps(values, default=str, sort_keys=True)


class ProductionRunService:
    """Business logic for the production-runs draft lifecycle (E.1)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = ProductionRunRepository(uow)

    # ==================================================================
    # Shared helpers
    # ==================================================================

    @staticmethod
    def _etag_from_row(row: dict[str, Any]) -> tuple[str, int]:
        ts = row["updated_at"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return etag_and_version_from_updated_at(ts)

    async def _enrich(
        self,
        row: dict[str, Any],
        *,
        inputs: Sequence[dict[str, Any]] | None = None,
        output_row: dict[str, Any] | None = None,
        cost_lines: Sequence[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Shape the run for the API response.

        Always returns ``inputs`` / ``output`` / ``cost_lines`` (per the
        OpenAPI required-field contract). When not supplied, we fetch
        them from the DB so list + get always return the full shape.
        """
        run_id = int(row["id"])
        if inputs is None:
            inputs = await self._repo.list_inputs(run_id)
        if output_row is None:
            output_row = await self._repo.get_output(run_id)
        if cost_lines is None:
            cost_lines = await self._repo.list_cost_lines(run_id)

        etag, _v = self._etag_from_row(row)

        enriched: dict[str, Any] = dict(row)
        enriched["finished_unit_cost"] = float(row["finished_unit_cost"])
        enriched["total_raw_cost"] = float(row["total_raw_cost"])
        enriched["total_overhead_cost"] = float(row["total_overhead_cost"])
        enriched["output_quantity"] = float(row["output_quantity"])
        enriched["version"] = int(row["version"]) if row.get("version") is not None else 1
        enriched["etag"] = etag

        enriched["inputs"] = [self._input_dict(r) for r in inputs]
        # ``output`` is required by the schema. For a draft that pre-dates
        # the E.1 refactor (theoretical), the row may not exist; we
        # synthesise an empty shell so the response always satisfies the
        # schema. In practice the service always upserts the output on
        # create + patch, so this branch is only a defensive fallback.
        if output_row is None:
            enriched["output"] = {
                "id": 0,
                "production_run_id": run_id,
                "product_id": int(row["output_product_id"]),
                "quantity": float(row["output_quantity"]),
                "unit_cost_snapshot": float(row["finished_unit_cost"]),
                "total_cost": _q2(
                    Decimal(str(row["finished_unit_cost"]))
                    * Decimal(str(row["output_quantity"]))
                ),
            }
        else:
            enriched["output"] = self._output_dict(output_row)
        enriched["cost_lines"] = [self._cost_line_dict(r) for r in cost_lines]
        return enriched

    @staticmethod
    def _input_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "production_run_id": int(r["production_run_id"]),
            "product_id": int(r["product_id"]),
            "quantity": float(r["quantity"]),
            "unit_cost_snapshot": float(r["unit_cost_snapshot"]),
            "line_cost": float(r["line_cost"]),
            "line_number": int(r["line_number"]),
        }

    @staticmethod
    def _output_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "production_run_id": int(r["production_run_id"]),
            "product_id": int(r["product_id"]),
            "quantity": float(r["quantity"]),
            "unit_cost_snapshot": float(r["unit_cost_snapshot"]),
            "total_cost": float(r["total_cost"]),
        }

    @staticmethod
    def _cost_line_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "production_run_id": int(r["production_run_id"]),
            "cost_type_id": int(r["cost_type_id"]),
            "description": r.get("description"),
            "amount": float(r["amount"]),
            "paid_in_cash": bool(r["paid_in_cash"]),
            "line_number": int(r["line_number"]),
        }

    async def _check_output_product_producible(
        self, product_id: int
    ) -> dict[str, Any]:
        """Validate the output product is producible + active.

        The critical business rule from PRD §15 + plan §5 E.1:
        ``products.is_producible = TRUE`` (and ``is_active = TRUE``).
        Raises ``BusinessRuleViolation`` with a code-specific details
        payload if the product is missing, deactivated, or not
        producible.
        """
        row = await self._repo.get_product(product_id)
        if row is None:
            raise NotFound(f"Product {product_id} not found.")
        if not bool(row.get("is_active")):
            raise BusinessRuleViolation(
                "Output product is deactivated; cannot be used in production.",
                details={"field": "output_product_id", "reason": "product_deactivated"},
            )
        if not bool(row.get("is_producible")):
            raise BusinessRuleViolation(
                "Output product is not producible.",
                details={
                    "field": "output_product_id",
                    "reason": "product_not_producible",
                },
            )
        return row

    async def _check_cost_types(
        self, cost_type_ids: Sequence[int]
    ) -> None:
        """Validate every cost_type_id exists and is active.

        Cost-type deactivation is a hard rule for new runs (BR-COST-005
        semantics — referenced cost_types must remain active for new
        write transactions).
        """
        if not cost_type_ids:
            return
        # Dedupe for efficiency.
        seen: set[int] = set()
        for ct_id in cost_type_ids:
            if ct_id in seen:
                continue
            seen.add(ct_id)
            row = await self._repo.get_cost_type(int(ct_id))
            if row is None:
                raise NotFound(f"Cost type {ct_id} not found.")
            if not bool(row.get("is_active")):
                raise BusinessRuleViolation(
                    f"Cost type {ct_id} is deactivated.",
                    details={
                        "field": "cost_lines",
                        "cost_type_id": int(ct_id),
                        "reason": "cost_type_deactivated",
                    },
                )

    async def _compute_totals(self, run_id: int) -> tuple[Decimal, Decimal, Decimal]:
        """Compute (total_raw_cost, total_overhead_cost, finished_unit_cost)
        from the persisted inputs + cost_lines of the run.

        ``finished_unit_cost`` = (raw + overhead) / output_quantity.
        On a brand-new draft before any overhead exists, the value
        reflects inputs only — it is a stable placeholder for the
        draft-time response so the ``ck_prun_finished_unit_cost_pos``
        constraint is satisfied and the API can echo a meaningful number.
        """
        inputs = await self._repo.list_inputs(run_id)
        cost_lines = await self._repo.list_cost_lines(run_id)
        total_raw = sum((Decimal(str(r["line_cost"])) for r in inputs), Decimal("0"))
        total_overhead = sum(
            (Decimal(str(r["amount"])) for r in cost_lines), Decimal("0")
        )
        run = await self._repo.get(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} disappeared mid-transaction.")
        qty = Decimal(str(run["output_quantity"]))
        if qty <= 0:
            # Defensive — should never happen given the CHECK constraint,
            # but the computation would divide-by-zero otherwise.
            finished = _q4(total_raw + total_overhead)
        else:
            finished = _q4((total_raw + total_overhead) / qty)
        # The CHECK constraint requires finished_unit_cost > 0; on a
        # zero-input draft with zero overhead we have 0/0 -> we coerce
        # to a tiny epsilon so the constraint passes. The post-time
        # recompute (E.3) will produce the authoritative value.
        if finished <= 0:
            finished = _q4(Decimal("0.0001"))
        return _q2(total_raw), _q2(total_overhead), finished

    async def _recompute_and_persist_totals(
        self, run_id: int
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Recompute totals and UPDATE the run + output snapshot rows.

        Called after create (so the persisted header reflects the
        totals), and on every draft patch (so changing the output
        product / quantity or editing inputs / cost lines cascades into
        finished_unit_cost). E.2 will mutate inputs / cost lines
        through sub-resource endpoints and must call this helper too.
        """
        total_raw, total_overhead, finished = await self._compute_totals(run_id)
        await self._repo.update_draft(
            run_id,
            run_date=None,
            output_product_id=None,
            output_quantity=None,
            notes=None,
            finished_unit_cost=finished,
            total_raw_cost=total_raw,
            total_overhead_cost=total_overhead,
        )
        # Keep the output row in sync with the run header.
        run = await self._repo.get(run_id)
        if run is not None:
            qty = Decimal(str(run["output_quantity"]))
            total_cost = _q2(finished * qty)
            await self._repo.upsert_output(
                run_id=run_id,
                product_id=int(run["output_product_id"]),
                quantity=qty,
                unit_cost_snapshot=finished,
                total_cost=total_cost,
            )
        return total_raw, total_overhead, finished

    # ==================================================================
    # E.1 endpoints
    # ==================================================================

    async def list_production_runs(
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
    ) -> tuple[list[dict[str, Any]], int]:
        # Lifecycle filter is closed-set; reject unknown values up-front
        # so the response carries a clean 400 instead of an empty list.
        if lifecycle_status is not None and lifecycle_status not in {
            LifecycleStatus.DRAFT.value,
            LifecycleStatus.POSTED.value,
            LifecycleStatus.COMPLETED.value,
            LifecycleStatus.CANCELLED.value,
        }:
            raise BusinessRuleViolation(
                f"Unknown lifecycle_status '{lifecycle_status}'.",
                details={"field": "lifecycle_status"},
            )

        rows, total = await self._repo.list(
            page=page,
            per_page=per_page,
            q=q,
            sort=sort,
            lifecycle_status=lifecycle_status,
            output_product_id=output_product_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        enriched: list[dict[str, Any]] = []
        for r in rows:
            enriched.append(await self._enrich(r))
        return enriched, total

    async def get_production_run(
        self,
        run_id: int,
        *,
        include: set[str] | None = None,
    ) -> dict[str, Any]:
        row = await self._repo.get(run_id)
        if row is None:
            raise NotFound(f"Production run {run_id} not found.")

        # The OpenAPI schema marks ``inputs``, ``output``, ``cost_lines``
        # as required (non-nullable) on the response. The summary also
        # documents ``?include=inputs,cost_lines,output`` as a hint to
        # clients. We always materialise them so the contract is
        # satisfied — the include hint is informational.
        del include  # reserved for forward-compatible extension
        return await self._enrich(row)

    async def create_draft(
        self,
        *,
        principal_user_id: int,
        run_date: datetime | None,
        output_product_id: int,
        output_quantity: Decimal,
        notes: str | None,
        inputs: list[dict[str, Any]],
        cost_lines: list[dict[str, Any]] | None,
        ctx: AuditContext | None,
        idempotency_key: str | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency replay (mirrors M4 purchase/sale pattern).
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path="/production-runs", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /production-runs",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                cached = idem_rec.response_body
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except (ValueError, TypeError):
                        cached = {}
                return cached, True  # type: ignore[return-value]

        # --- pre-validations (clean 4xx codes) ---------------------
        await self._check_output_product_producible(output_product_id)

        # Inputs: validate product FKs and snapshot moving-avg unit cost.
        if not inputs:
            raise BusinessRuleViolation(
                "A production run must have at least one input line.",
                details={"field": "inputs"},
            )
        seen_input_products: set[int] = set()
        validated_inputs: list[dict[str, Any]] = []
        for ln in inputs:
            pid = int(ln["product_id"])
            if pid == output_product_id:
                raise BusinessRuleViolation(
                    "An input product cannot equal the output product.",
                    details={
                        "field": "inputs",
                        "product_id": pid,
                        "reason": "input_equals_output",
                    },
                )
            if pid in seen_input_products:
                raise BusinessRuleViolation(
                    f"Duplicate input product_id {pid}.",
                    details={
                        "field": "inputs",
                        "product_id": pid,
                        "reason": "duplicate_input",
                    },
                )
            seen_input_products.add(pid)
            prod = await self._repo.get_product(pid)
            if prod is None:
                raise NotFound(f"Input product {pid} not found.")
            if not bool(prod.get("is_active")):
                raise BusinessRuleViolation(
                    f"Input product {pid} is deactivated.",
                    details={
                        "field": "inputs",
                        "product_id": pid,
                        "reason": "product_deactivated",
                    },
                )
            qty = _q4(ln["quantity"])
            if qty <= 0:
                raise BusinessRuleViolation(
                    "Input quantity must be positive.",
                    details={"field": "inputs"},
                )
            snapshot = await self._repo.get_input_unit_cost_snapshot(pid)
            line_cost = _q2(snapshot * qty)
            validated_inputs.append(
                {
                    "product_id": pid,
                    "quantity": qty,
                    "unit_cost_snapshot": _q4(snapshot),
                    "line_cost": line_cost,
                }
            )

        # Cost lines: validate cost_type FKs and amounts.
        validated_cost_lines: list[dict[str, Any]] = []
        cost_type_ids: list[int] = []
        if cost_lines:
            for cl in cost_lines:
                cost_type_ids.append(int(cl["cost_type_id"]))
            await self._check_cost_types(cost_type_ids)
            for cl in cost_lines:
                amt = _q2(cl["amount"])
                if amt <= 0:
                    raise BusinessRuleViolation(
                        "Cost-line amount must be positive.",
                        details={"field": "cost_lines"},
                    )
                validated_cost_lines.append(
                    {
                        "cost_type_id": int(cl["cost_type_id"]),
                        "amount": amt,
                        "paid_in_cash": bool(cl.get("paid_in_cash", True)),
                        "description": cl.get("description"),
                    }
                )

        # --- persist header (placeholder totals; recomputed below) ---
        # The DB CHECK requires finished_unit_cost > 0, so we seed it
        # with a tiny epsilon and overwrite after we know the real
        # totals. Total costs start at 0 (CHECK >= 0 allows it).
        finished_seed = _q4(Decimal("0.0001"))
        run = await self._repo.create_draft(
            run_date=run_date,
            output_product_id=output_product_id,
            output_quantity=output_quantity,
            finished_unit_cost=finished_seed,
            total_raw_cost=Decimal("0"),
            total_overhead_cost=Decimal("0"),
            notes=notes,
            created_by=principal_user_id,
        )
        if run is None:
            # RETURNING always produces a row, but mypy needs the guard.
            raise Conflict("Failed to create production run.")
        run_id = int(run["id"])

        # Insert inputs (numbered 1..N).
        for idx, v in enumerate(validated_inputs, start=1):
            await self._repo.insert_input(
                run_id=run_id,
                product_id=v["product_id"],
                quantity=v["quantity"],
                unit_cost_snapshot=v["unit_cost_snapshot"],
                line_cost=v["line_cost"],
                line_number=idx,
            )

        # Insert cost lines (numbered 1..M).
        if validated_cost_lines:
            for idx, v in enumerate(validated_cost_lines, start=1):
                await self._repo.insert_cost_line(
                    run_id=run_id,
                    cost_type_id=v["cost_type_id"],
                    description=v["description"],
                    amount=v["amount"],
                    paid_in_cash=v["paid_in_cash"],
                    line_number=idx,
                )

        # Recompute totals + upsert output snapshot.
        total_raw, total_overhead, finished = await self._recompute_and_persist_totals(
            run_id
        )

        # Audit log (CREATE).
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_PRODUCTION_RUNS,
                entity_id=run_id,
                new_values={
                    "output_product_id": output_product_id,
                    "output_quantity": str(output_quantity),
                    "run_date": run_date.isoformat() if run_date else None,
                    "notes": notes,
                    "total_raw_cost": str(total_raw),
                    "total_overhead_cost": str(total_overhead),
                    "finished_unit_cost": str(finished),
                    "inputs": [
                        {
                            "product_id": v["product_id"],
                            "quantity": str(v["quantity"]),
                            "unit_cost_snapshot": str(v["unit_cost_snapshot"]),
                            "line_cost": str(v["line_cost"]),
                        }
                        for v in validated_inputs
                    ],
                    "cost_lines": (
                        [
                            {
                                "cost_type_id": v["cost_type_id"],
                                "amount": str(v["amount"]),
                                "paid_in_cash": v["paid_in_cash"],
                                "description": v["description"],
                            }
                            for v in validated_cost_lines
                        ]
                        if validated_cost_lines
                        else []
                    ),
                },
                ctx=ctx,
            )

        # Re-read the final state (after recompute UPDATE bumped version).
        final = await self._repo.get(run_id)
        if final is None:
            raise Conflict("Production run disappeared after create.")
        enriched = await self._enrich(final)

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=_to_jsonb(enriched) or "{}",
                user_id=principal_user_id,
            )

        return enriched, False

    async def update_draft(
        self,
        run_id: int,
        *,
        run_date: datetime | None,
        output_product_id: int | None,
        output_quantity: Decimal | None,
        notes: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> dict[str, Any]:
        # FOR UPDATE so concurrent PATCHes serialise.
        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        if run["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{run['lifecycle_status']}' state; "
                "only draft runs can be patched."
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        # Validate the new output product (if changed) against the
        # producibility rule before we mutate the row.
        if output_product_id is not None and int(output_product_id) != int(
            run["output_product_id"]
        ):
            await self._check_output_product_producible(int(output_product_id))

        # DB CHECK constraints on output_quantity are > 0; the schema
        # already validated > 0 at parse time, but be defensive.
        if output_quantity is not None and Decimal(str(output_quantity)) <= 0:
            raise BusinessRuleViolation(
                "output_quantity must be positive.",
                details={"field": "output_quantity"},
            )

        old_values = dict(run)
        updated = await self._repo.update_draft(
            run_id,
            run_date=run_date,
            output_product_id=output_product_id,
            output_quantity=(
                _q4(output_quantity) if output_quantity is not None else None
            ),
            notes=notes,
        )
        if updated is None:
            raise NotFound(f"Production run {run_id} not found after update.")

        # Recompute totals + upsert output snapshot to reflect any
        # quantity change (and to keep finished_unit_cost consistent
        # with the latest input / cost-line set, even though E.1 does
        # not yet mutate those sub-resources — E.2 will, and the
        # E.2 service is responsible for calling the same recompute).
        await self._recompute_and_persist_totals(run_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCTION_RUNS,
                entity_id=run_id,
                old_values={
                    k: old_values.get(k) for k in (
                        "run_date",
                        "output_product_id",
                        "output_quantity",
                        "notes",
                        "lifecycle_status",
                        "version",
                    )
                },
                new_values={
                    "run_date": updated.get("run_date"),
                    "output_product_id": updated.get("output_product_id"),
                    "output_quantity": str(updated.get("output_quantity", "")),
                    "notes": updated.get("notes"),
                    "lifecycle_status": updated.get("lifecycle_status"),
                    "version": updated.get("version"),
                },
                ctx=ctx,
            )

        final = await self._repo.get(run_id)
        if final is None:
            raise NotFound(f"Production run {run_id} not found after update.")
        return await self._enrich(final)

    # ==================================================================
    # E.2 — Production Inputs sub-resource
    # ==================================================================

    async def list_inputs(self, run_id: int) -> list[dict[str, Any]]:
        """listProductionInputs — GET /production-runs/{id}/inputs."""
        run = await self._repo.get(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        rows = await self._repo.list_inputs(run_id)
        return [self._input_dict(r) for r in rows]

    async def add_input(
        self,
        run_id: int,
        *,
        principal_user_id: int,
        input_data: dict[str, Any],
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """addProductionInput — POST /production-runs/{id}/inputs.

        Reuses the exact validation rules from ``create_draft``:
        product existence, active state, no-duplicate-with-existing,
        circular-flow check, quantity > 0. The moving-average cost
        snapshot is taken at the same helper E.1 uses
        (``get_input_unit_cost_snapshot``). After insert the parent
        run's derived totals are recomputed so ``total_raw_cost`` /
        ``finished_unit_cost`` stay consistent.
        """
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/production-runs/{run_id}/inputs",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint=f"POST /production-runs/{run_id}/inputs",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                cached = idem_rec.response_body
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except (ValueError, TypeError):
                        cached = {}
                return cached, True  # type: ignore[return-value]

        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        if run["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{run['lifecycle_status']}' "
                "state; only draft runs accept new inputs."
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        output_product_id = int(run["output_product_id"])
        pid = int(input_data["product_id"])
        if pid == output_product_id:
            raise BusinessRuleViolation(
                "An input product cannot equal the output product.",
                details={
                    "field": "inputs",
                    "product_id": pid,
                    "reason": "input_equals_output",
                },
            )
        prod = await self._repo.get_product(pid)
        if prod is None:
            raise NotFound(f"Input product {pid} not found.")
        if not bool(prod.get("is_active")):
            raise BusinessRuleViolation(
                f"Input product {pid} is deactivated.",
                details={
                    "field": "inputs",
                    "product_id": pid,
                    "reason": "product_deactivated",
                },
            )
        existing = await self._uow.fetch_all(
            "SELECT 1 FROM production_inputs "
            "WHERE production_run_id = :rid AND product_id = :pid",
            {"rid": run_id, "pid": pid},
        )
        if existing:
            raise Conflict(
                f"Input product {pid} is already used in this run.",
                details={
                    "field": "inputs",
                    "product_id": pid,
                    "reason": "duplicate_input",
                },
            )
        qty = _q4(input_data["quantity"])
        if qty <= 0:
            raise BusinessRuleViolation(
                "Input quantity must be positive.",
                details={"field": "inputs"},
            )
        snapshot = await self._repo.get_input_unit_cost_snapshot(pid)
        line_cost = _q2(snapshot * qty)
        ln_no = await self._repo.next_input_line_number(run_id)
        inserted = await self._repo.insert_input(
            run_id=run_id,
            product_id=pid,
            quantity=qty,
            unit_cost_snapshot=_q4(snapshot),
            line_cost=line_cost,
            line_number=ln_no,
        )
        if inserted is None:
            raise Conflict("Failed to insert production input.")

        # Recompute derived totals on the parent run.
        await self._recompute_and_persist_totals(run_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_PRODUCTION_INPUTS,
                entity_id=int(inserted["id"]),
                new_values=self._input_dict(inserted),
                ctx=ctx,
            )

        # Re-read the run (recompute UPDATE bumped version/updated_at).
        final = await self._repo.get(run_id)
        if final is None:
            raise NotFound("Production run disappeared after input add.")
        result = self._input_dict(inserted)
        result["updated_at"] = final.get("updated_at")
        result["etag"] = self._etag_from_row(final)[0]

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=_to_jsonb(result) or "{}",
                user_id=principal_user_id,
            )
        return result, False

    async def update_input(
        self,
        run_id: int,
        input_id: int,
        *,
        principal_user_id: int,
        input_data: dict[str, Any],
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> dict[str, Any]:
        """updateProductionInput — PATCH /production-runs/{id}/inputs/{input_id}.

        Mirrors the create-time validation for product + quantity. The
        ``unit_cost_snapshot`` is **re-snapshotted** from the moving
        average (historical cost preservation rule: the snapshot always
        reflects the current valuation at mutation time, per E.1). When
        the product changes the circular-flow / duplicate checks apply
        against the *other* inputs (E.1 pattern).
        """
        del principal_user_id  # capability already gates access
        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        if run["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{run['lifecycle_status']}' "
                "state; only draft lines can be updated."
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        old = await self._repo.get_input(run_id, input_id)
        if old is None:
            raise NotFound(f"Production input {input_id} not found.")

        output_product_id = int(run["output_product_id"])
        pid = int(input_data["product_id"])
        if pid == output_product_id:
            raise BusinessRuleViolation(
                "An input product cannot equal the output product.",
                details={
                    "field": "inputs",
                    "product_id": pid,
                    "reason": "input_equals_output",
                },
            )
        prod = await self._repo.get_product(pid)
        if prod is None:
            raise NotFound(f"Input product {pid} not found.")
        if not bool(prod.get("is_active")):
            raise BusinessRuleViolation(
                f"Input product {pid} is deactivated.",
                details={
                    "field": "inputs",
                    "product_id": pid,
                    "reason": "product_deactivated",
                },
            )
        # Duplicate check excluding the line being updated.
        dups = await self._uow.fetch_all(
            "SELECT 1 FROM production_inputs "
            "WHERE production_run_id = :rid AND product_id = :pid AND id <> :iid",
            {"rid": run_id, "pid": pid, "iid": input_id},
        )
        if dups:
            raise Conflict(
                f"Input product {pid} is already used in this run.",
                details={
                    "field": "inputs",
                    "product_id": pid,
                    "reason": "duplicate_input",
                },
            )
        qty = _q4(input_data["quantity"])
        if qty <= 0:
            raise BusinessRuleViolation(
                "Input quantity must be positive.",
                details={"field": "inputs"},
            )
        snapshot = await self._repo.get_input_unit_cost_snapshot(pid)
        line_cost = _q2(snapshot * qty)
        updated = await self._repo.update_input(
            run_id=run_id,
            input_id=input_id,
            product_id=pid,
            quantity=qty,
            unit_cost_snapshot=_q4(snapshot),
            line_cost=line_cost,
        )
        if updated is None:
            raise NotFound(f"Production input {input_id} not found after update.")

        await self._recompute_and_persist_totals(run_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCTION_INPUTS,
                entity_id=input_id,
                old_values=dict(old),
                new_values=dict(updated),
                ctx=ctx,
            )

        final_run = await self._repo.get(run_id)
        if final_run is None:
            raise NotFound("Production run disappeared after input update.")
        result = self._input_dict(updated)
        result["updated_at"] = final_run.get("updated_at")
        result["etag"] = self._etag_from_row(final_run)[0]
        return result

    async def delete_input(
        self,
        run_id: int,
        input_id: int,
        *,
        principal_user_id: int,
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> tuple[None, bool]:
        """deleteProductionInput — DELETE /production-runs/{id}/inputs/{input_id}.

        Only drafts accept deletion. After DELETE the remaining lines
        are renumbered and the parent totals recompute to avoid stale
        ``total_raw_cost`` / ``finished_unit_cost``.
        """
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="DELETE",
                path=f"/production-runs/{run_id}/inputs/{input_id}",
                body=None,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint=f"DELETE /production-runs/{run_id}/inputs/{input_id}",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return None, True

        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        if run["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{run['lifecycle_status']}' "
                "state; only draft lines can be deleted."
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        old = await self._repo.get_input(run_id, input_id)
        if old is None:
            if idempotency_key is not None and idem_rec is not None:
                store = IdempotencyStore(self._uow)
                await store.complete(
                    record_id=idem_rec.id,
                    status=204,
                    body="{}",
                    user_id=principal_user_id,
                )
                await self._uow.commit()
            else:
                raise NotFound(f"Production input {input_id} not found.")
            return None, False

        await self._repo.delete_input(run_id, input_id)
        await self._repo.renumber_inputs(run_id)
        await self._recompute_and_persist_totals(run_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCTION_INPUTS,
                entity_id=input_id,
                old_values=dict(old),
                ctx=ctx,
            )

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=204,
                body="{}",
                user_id=principal_user_id,
            )
        return None, False

    # ==================================================================
    # E.2 — Production Cost Lines sub-resource
    # ==================================================================

    async def list_cost_lines(self, run_id: int) -> list[dict[str, Any]]:
        """listProductionCostLines — GET /production-runs/{id}/cost-lines."""
        run = await self._repo.get(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        rows = await self._repo.list_cost_lines(run_id)
        return [self._cost_line_dict(r) for r in rows]

    async def add_cost_line(
        self,
        run_id: int,
        *,
        principal_user_id: int,
        cost_line_data: dict[str, Any],
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """addProductionCostLine — POST /production-runs/{id}/cost-lines."""
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/production-runs/{run_id}/cost-lines",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint=f"POST /production-runs/{run_id}/cost-lines",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                cached = idem_rec.response_body
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except (ValueError, TypeError):
                        cached = {}
                return cached, True  # type: ignore[return-value]

        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        if run["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{run['lifecycle_status']}' "
                "state; only draft runs accept new cost lines."
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        ct_id = int(cost_line_data["cost_type_id"])
        await self._check_cost_types([ct_id])
        amt = _q2(cost_line_data["amount"])
        if amt <= 0:
            raise BusinessRuleViolation(
                "Cost-line amount must be positive.",
                details={"field": "cost_lines"},
            )
        paid_in_cash = bool(cost_line_data.get("paid_in_cash", True))
        description = cost_line_data.get("description")
        ln_no = await self._repo.next_cost_line_number(run_id)
        inserted = await self._repo.insert_cost_line(
            run_id=run_id,
            cost_type_id=ct_id,
            description=description,
            amount=amt,
            paid_in_cash=paid_in_cash,
            line_number=ln_no,
        )
        if inserted is None:
            raise Conflict("Failed to insert production cost line.")

        await self._recompute_and_persist_totals(run_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_PRODUCTION_COST_LINES,
                entity_id=int(inserted["id"]),
                new_values=self._cost_line_dict(inserted),
                ctx=ctx,
            )

        final = await self._repo.get(run_id)
        if final is None:
            raise NotFound("Production run disappeared after cost-line add.")
        result = self._cost_line_dict(inserted)
        result["updated_at"] = final.get("updated_at")
        result["etag"] = self._etag_from_row(final)[0]

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=_to_jsonb(result) or "{}",
                user_id=principal_user_id,
            )
        return result, False

    async def update_cost_line(
        self,
        run_id: int,
        line_id: int,
        *,
        principal_user_id: int,
        cost_line_data: dict[str, Any],
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> dict[str, Any]:
        """updateProductionCostLine — PATCH /production-runs/{id}/cost-lines/{line_id}."""
        del principal_user_id  # capability already gates access
        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        if run["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{run['lifecycle_status']}' "
                "state; only draft lines can be updated."
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        old = await self._repo.get_cost_line(run_id, line_id)
        if old is None:
            raise NotFound(f"Production cost line {line_id} not found.")

        ct_id = int(cost_line_data["cost_type_id"])
        await self._check_cost_types([ct_id])
        amt = _q2(cost_line_data["amount"])
        if amt <= 0:
            raise BusinessRuleViolation(
                "Cost-line amount must be positive.",
                details={"field": "cost_lines"},
            )
        paid_in_cash = bool(cost_line_data.get("paid_in_cash", True))
        description = cost_line_data.get("description")
        updated = await self._repo.update_cost_line(
            run_id=run_id,
            line_id=line_id,
            cost_type_id=ct_id,
            description=description,
            amount=amt,
            paid_in_cash=paid_in_cash,
        )
        if updated is None:
            raise NotFound(f"Production cost line {line_id} not found after update.")

        await self._recompute_and_persist_totals(run_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCTION_COST_LINES,
                entity_id=line_id,
                old_values=dict(old),
                new_values=dict(updated),
                ctx=ctx,
            )

        final_run = await self._repo.get(run_id)
        if final_run is None:
            raise NotFound("Production run disappeared after cost-line update.")
        result = self._cost_line_dict(updated)
        result["updated_at"] = final_run.get("updated_at")
        result["etag"] = self._etag_from_row(final_run)[0]
        return result

    async def delete_cost_line(
        self,
        run_id: int,
        line_id: int,
        *,
        principal_user_id: int,
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> tuple[None, bool]:
        """deleteProductionCostLine — DELETE /production-runs/{id}/cost-lines/{line_id}."""
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="DELETE",
                path=f"/production-runs/{run_id}/cost-lines/{line_id}",
                body=None,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint=f"DELETE /production-runs/{run_id}/cost-lines/{line_id}",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return None, True

        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        if run["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{run['lifecycle_status']}' "
                "state; only draft lines can be deleted."
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        old = await self._repo.get_cost_line(run_id, line_id)
        if old is None:
            if idempotency_key is not None and idem_rec is not None:
                store = IdempotencyStore(self._uow)
                await store.complete(
                    record_id=idem_rec.id,
                    status=204,
                    body="{}",
                    user_id=principal_user_id,
                )
                await self._uow.commit()
            else:
                raise NotFound(f"Production cost line {line_id} not found.")
            return None, False

        await self._repo.delete_cost_line(run_id, line_id)
        await self._repo.renumber_cost_lines(run_id)
        await self._recompute_and_persist_totals(run_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCTION_COST_LINES,
                entity_id=line_id,
                old_values=dict(old),
                ctx=ctx,
            )

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=204,
                body="{}",
                user_id=principal_user_id,
            )
        return None, False


# ==================================================================
    # E.3 — Production post (lifecycle: draft → posted)
    # ==================================================================

    async def post(
        self,
        run_id: int,
        *,
        principal_user_id: int,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """postProductionRun — POST /production-runs/{id}/post.

        Implements the authoritative production-post transaction per
        ``Backend-Architecture-V1.0.md`` §15.8 + Event 16 of the
        accounting event matrix. All business mutations + audit + the
        idempotency entry share one ``COMMIT``; any failure rolls back
        the entire transaction.

        Pre-conditions (raised as 4xx with the canonical error code):
        * run exists and is in draft lifecycle (else 409 lifecycle_violation)
        * ETag matches the current ``updated_at`` (else 409 version_mismatch)
        * ≥1 input row, exactly one output, all input qty > 0 (else 409)
        * output product is active + producible (else 422)
        * all referenced cost_types active (else 422)
        * on-hand stock ≥ requested input qty for every input (else 409
          production_inputs_exceed_stock / insufficient_stock)
        * cash balance ≥ Σ paid-in-cash overhead (DB trigger
          fn_cash_movements_balance_check enforces INV-03)

        Idempotency: standard M4 pattern. Replay returns the cached
        response; key+body mismatch returns 409 idempotency_violation.
        """
        # ----- idempotency replay (mirrors M4 pattern) -----
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/production-runs/{run_id}/post",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint=f"POST /production-runs/{run_id}/post",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                cached = idem_rec.response_body
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except (ValueError, TypeError):
                        cached = {}
                return cached, True  # type: ignore[return-value]

        # ----- row-level lock on the run header -----
        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        if run["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{run['lifecycle_status']}' "
                "state; only draft runs can be posted.",
                details={
                    "code": "lifecycle_state_invalid",
                    "current_state": str(run["lifecycle_status"]),
                    "expected_state": LifecycleStatus.DRAFT.value,
                },
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        # ----- snapshot inputs / output / cost_lines from DB -----
        inputs = await self._repo.list_inputs(run_id)
        if not inputs:
            raise LifecycleStateInvalid(
                "Cannot post a production run with no inputs.",
                details={"code": "lifecycle_state_invalid", "field": "inputs"},
            )
        cost_lines = await self._repo.list_cost_lines(run_id)
        output = await self._repo.get_output(run_id)
        if output is None:
            raise LifecycleStateInvalid(
                "Production run is missing its output row; cannot post.",
                details={"code": "lifecycle_state_invalid", "field": "output"},
            )

        output_product_id = int(run["output_product_id"])
        output_quantity = _q4(run["output_quantity"])

        # ----- validate output product + cost types -----
        await self._check_output_product_producible(output_product_id)
        if cost_lines:
            await self._check_cost_types(
                [int(cl["cost_type_id"]) for cl in cost_lines]
            )

        # ----- total_raw_cost / total_overhead_cost from DB -----
        # Per Arch §15.8 step 4-5: ``Σ inputs.line_cost`` and
        # ``Σ cost_lines.amount``. These are the same numbers the
        # draft-time recompute produced — the input snapshot is
        # already frozen onto each ``production_inputs`` row by E.1/E.2
        # (per BR-COST-003 historical-cost preservation).
        total_raw_cost = sum(
            (Decimal(str(r["line_cost"])) for r in inputs), Decimal("0")
        )
        total_overhead_cost = sum(
            (Decimal(str(r["amount"])) for r in cost_lines), Decimal("0")
        )
        total_raw_cost = _q2(total_raw_cost)
        total_overhead_cost = _q2(total_overhead_cost)

        # ----- finished_unit_cost = (raw + overhead) / output_quantity -----
        if output_quantity <= 0:
            # Defensive — DB CHECK ck_prun_output_qty_pos already guards
            # against this; we surface a clean 409 instead of a divide-by-zero.
            raise ProductionFinishedCostInvalid(
                "Production run output_quantity must be positive.",
                details={
                    "code": "production_finished_cost_invalid",
                    "field": "output_quantity",
                },
            )
        finished_unit_cost = _q4(
            (total_raw_cost + total_overhead_cost) / output_quantity
        )
        if finished_unit_cost <= 0:
            raise ProductionFinishedCostInvalid(
                "Computed finished_unit_cost must be positive.",
                details={
                    "code": "production_finished_cost_invalid",
                    "total_production_cost": float(
                        total_raw_cost + total_overhead_cost
                    ),
                    "output_quantity": float(output_quantity),
                },
            )

        # ----- concurrency: lock raw-product rows ASC, check on-hand -----
        # Per Arch §15.8 step 1 ("Lock products in ASC order"). Sorting
        # prevents two concurrent posts from deadlocking when they share
        # >1 raw product (A locks 1 then 2; B locks 2 then 1 → deadlock
        # without ASC ordering).
        sorted_inputs = sorted(
            inputs, key=lambda r: int(r["product_id"])
        )
        # Cache on-hand per product so a repeated input product is
        # checked once against the post-time view.
        # ``set`` below guarantees we only ``SELECT ... FOR UPDATE``
        # each unique raw product once.
        seen_locked: set[int] = set()
        on_hand_cache: dict[int, Decimal] = {}
        # Read the global negative-stock default ONCE (BR-STOCK-011).
        global_allow_neg = await self._repo.get_setting_bool(
            "default_negative_stock_allowed", default=False
        )

        for ln in sorted_inputs:
            pid = int(ln["product_id"])
            if pid in seen_locked:
                # Same product appears on two lines — already locked,
                # on-hand already captured.
                continue
            seen_locked.add(pid)
            prod = await self._repo.lock_product_for_update(pid)
            if prod is None:
                raise NotFound(f"Input product {pid} not found.")
            if not bool(prod.get("is_active")):
                raise LifecycleStateInvalid(
                    f"Input product {pid} is deactivated.",
                    details={
                        "code": "lifecycle_state_invalid",
                        "field": "inputs",
                        "product_id": pid,
                        "reason": "product_deactivated",
                    },
                )
            qty = _q4(ln["quantity"])
            on_hand = await self._repo.on_hand_quantity(pid)
            on_hand_cache[pid] = on_hand
            allow_neg_override = await self._repo.product_allow_negative_stock(pid)
            product_neg_ok = (
                bool(allow_neg_override)
                if allow_neg_override is not None
                else global_allow_neg
            )
            if on_hand < qty and not product_neg_ok:
                raise ProductionInputsExceedStock(
                    f"Insufficient raw stock for product {pid}: "
                    f"have {on_hand}, need {qty}.",
                    details={
                        "code": "production_inputs_exceed_stock",
                        "product_id": pid,
                        "available": float(on_hand),
                        "required": float(qty),
                    },
                )

        # ----- insert stock_movements (one negative row per input) -----
        posted_at = datetime.now(UTC)
        for ln in sorted_inputs:
            pid = int(ln["product_id"])
            qty = _q4(ln["quantity"])
            # Historical-cost preservation per BR-COST-003: use the
            # snapshot already on production_inputs.unit_cost_snapshot.
            # The snapshot was taken at draft-add time via
            # ``get_input_unit_cost_snapshot``; we honour it instead of
            # recomputing, so cost history is immutable.
            unit_cost = _q4(ln["unit_cost_snapshot"])
            total_cost = _q2(qty * unit_cost)
            await self._repo.insert_stock_movement(
                product_id=pid,
                trigger="production_input",
                quantity=-qty,
                unit_cost_at_movement=unit_cost,
                total_cost=-total_cost,
                reference_type="production_run",
                reference_id=run_id,
                reference_line_id=int(ln["id"]),
                created_by=principal_user_id,
            )

        # ----- insert stock_movement for output (positive qty) -----
        await self._repo.insert_stock_movement(
            product_id=output_product_id,
            trigger="production_output",
            quantity=output_quantity,
            unit_cost_at_movement=finished_unit_cost,
            total_cost=_q2(finished_unit_cost * output_quantity),
            reference_type="production_run",
            reference_id=run_id,
            created_by=principal_user_id,
        )

        # ----- cash_movements for overhead lines with paid_in_cash=true -----
        # Per Arch §15.8 step 9 + Event 16: Σ overhead lines with
        # paid_in_cash=true → cash-out movements. We use payment_method
        # id=1 (Cash — seeded by conftest). The DB trigger
        # ``fn_cash_movements_balance_check`` is the authoritative
        # ceiling (INV-03); we still pre-check so we can return a
        # clean ``insufficient_cash`` code instead of letting the
        # trigger raise a raw check_violation.
        if any(bool(cl.get("paid_in_cash", True)) for cl in cost_lines):
            row = await self._uow.first_row(
                "SELECT COALESCE(SUM(amount), 0) AS b FROM cash_movements"
            )
            current_cash = Decimal(str((row or {"b": 0})["b"]))
            paid_total = sum(
                (
                    _q2(cl["amount"])
                    for cl in cost_lines
                    if bool(cl.get("paid_in_cash", True))
                ),
                Decimal("0"),
            )
            if current_cash < paid_total:
                raise InsufficientCash(
                    "Insufficient cash balance to cover paid-in-cash overhead.",
                    details={
                        "current_cash": float(current_cash),
                        "required": float(paid_total),
                    },
                )

        for cl in cost_lines:
            if not bool(cl.get("paid_in_cash", True)):
                continue
            amt = _q2(cl["amount"])
            await self._repo.insert_cash_movement(
                amount=-amt,
                direction="out",
                trigger="production_overhead",
                payment_method_id=1,  # Cash seed
                reference_type="production_run",
                reference_id=run_id,
                created_by=principal_user_id,
            )

        # ----- lifecycle: draft → posted (DB trigger enforces + version bump) -----
        posted = await self._repo.post_lifecycle(
            run_id,
            posted_by=principal_user_id,
            posted_at=posted_at,
            finished_unit_cost=finished_unit_cost,
            total_raw_cost=total_raw_cost,
            total_overhead_cost=total_overhead_cost,
        )
        if posted is None:
            raise Conflict("Failed to post production run.")

        # ----- audit -----
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.POST,
                entity_type=ENTITY_PRODUCTION_RUNS,
                entity_id=run_id,
                old_values={
                    "lifecycle_status": LifecycleStatus.DRAFT.value,
                    "version": int(run.get("version", 0)),
                },
                new_values={
                    "lifecycle_status": LifecycleStatus.POSTED.value,
                    "posted_at": posted_at.isoformat(),
                    "posted_by": principal_user_id,
                    "finished_unit_cost": str(finished_unit_cost),
                    "total_raw_cost": str(total_raw_cost),
                    "total_overhead_cost": str(total_overhead_cost),
                    "input_movements": [
                        {
                            "product_id": int(ln["product_id"]),
                            "quantity": float(ln["quantity"]),
                            "unit_cost_snapshot": float(
                                ln["unit_cost_snapshot"]
                            ),
                        }
                        for ln in sorted_inputs
                    ],
                    "cash_out_total": float(
                        sum(
                            _q2(cl["amount"])
                            for cl in cost_lines
                            if bool(cl.get("paid_in_cash", True))
                        )
                    ),
                },
                ctx=ctx,
            )

        # ----- enrich + return -----
        final = await self._repo.get(run_id)
        if final is None:
            raise NotFound("Production run disappeared after post.")
        enriched = await self._enrich(final)

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=200,
                body=_to_jsonb(enriched) or "{}",
                user_id=principal_user_id,
            )

        return enriched, False


    # ==================================================================
    # E.4 — Production cancel (lifecycle: posted|completed -> cancelled)
    # ==================================================================

    async def cancel(
        self,
        run_id: int,
        *,
        principal_user_id: int,
        reason: str,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """cancelProductionRun — POST /production-runs/{id}/cancel.

        Implements the authoritative production-cancel transaction per
        ``Backend-Architecture-V1.0.md`` §15.9 + Event 16 reversal
        semantics.  All business mutations + audit + the idempotency entry
        share one ``COMMIT``; any failure rolls back the entire
        transaction.

        Pre-conditions (raised as 4xx with the canonical error code):
        * run exists (else 404 not_found)
        * run is in a postable state (``posted`` or ``completed``) —
          from ``cancelled`` → 409 ``already_cancelled``;
          from ``draft`` → 409 ``lifecycle_state_invalid``
        * ETag matches (else 412 version_mismatch)
        * ``reason`` is non-empty (enforced by Pydantic body schema)

        Cancellation effects:
        * For each original ``production_input`` movement: insert a
          ``production_reversal`` row (+qty, +total_cost).
        * For the ``production_output`` movement: insert a
          ``production_reversal`` row (-qty, -total_cost).
        * **No cash_movement reversal** — overhead cash is immutable
          (DB spec §6.2 / Arch §15.9 / PRD §8.3).
        * Set ``lifecycle_status = 'cancelled'`` + cancellation fields.
        * Insert ``audit_log`` (AuditAction.CANCEL).

        Idempotency mirrors E.3: replay returns cached body with
        ``Idempotent-Replay: true``; key+body mismatch → 409
        ``idempotency_violation``.
        """
        # ----- idempotency replay (mirrors E.3 pattern) ----
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/production-runs/{run_id}/cancel",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint=f"POST /production-runs/{run_id}/cancel",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                cached = idem_rec.response_body
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except (ValueError, TypeError):
                        cached = {}
                return cached, True  # type: ignore[return-value]

        # ----- row-level lock on the run header -----
        run = await self._repo.get_for_update(run_id)
        if run is None:
            raise NotFound(f"Production run {run_id} not found.")
        current_status = str(run["lifecycle_status"])
        if current_status == LifecycleStatus.CANCELLED.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is already cancelled.",
                details={
                    "code": "already_cancelled",
                    "lifecycle_status": current_status,
                },
            )
        if current_status != LifecycleStatus.POSTED.value and current_status != LifecycleStatus.COMPLETED.value:
            raise LifecycleStateInvalid(
                f"Production run {run_id} is in '{current_status}' state; "
                "only posted or completed runs can be cancelled (draft runs "
                "can be edited or deleted).",
                details={
                    "code": "lifecycle_state_invalid",
                    "current_state": current_status,
                    "expected_state": "posted | completed",
                },
            )
        current_etag, _v = self._etag_from_row(run)
        check_if_match(provided=if_match, current_etag=current_etag)

        # ----- fetch original stock_movements to reverse -----
        movements = await self._repo.list_posted_movements_for_run(run_id)
        if not movements:
            # A posted run always has input + output movements. If none
            # are found (already reversed or edge case), refuse to
            # create spurious reversal rows — surface a clean conflict.
            raise Conflict(
                f"Production run {run_id} has no reversible input/output movements.",
                details={
                    "code": "lifecycle_state_invalid",
                    "current_state": current_status,
                    "reason": "no_reversible_movements",
                },
            )

        # ----- insert reversal movements + back-link originals -----
        # Per Arch §15.9:
        #   - input reversal: quantity = +original.qty, total_cost = +original.total_cost
        #   - output reversal: quantity = -original.qty, total_cost = -original.total_cost
        #   - unit_cost_at_movement = original snapshot (historical cost preservation)
        #   - reversal_of_movement_id = original.id
        #   - The original's reversed_by_movement_id is set back.
        for mv in movements:
            orig_qty = Decimal(str(mv["quantity"]))
            orig_total_cost = Decimal(str(mv["total_cost"] or 0))
            orig_unit_cost = mv["unit_cost_at_movement"]
            await self._repo.insert_reversal_movement(
                product_id=int(mv["product_id"]),
                trigger="production_reversal",
                quantity=-orig_qty,  # opposite sign of original
                unit_cost_at_movement=(
                    Decimal(str(orig_unit_cost))
                    if orig_unit_cost is not None
                    else None
                ),
                total_cost=-orig_total_cost,
                reference_type="production_run",
                reference_id=run_id,
                reversal_of_movement_id=int(mv["id"]),
                created_by=principal_user_id,
            )

        # ----- lifecycle: -> cancelled -----
        cancellation_date = datetime.now(UTC)
        cancelled = await self._repo.cancel_lifecycle(
            run_id,
            cancelled_by=principal_user_id,
            cancellation_date=cancellation_date,
            cancellation_reason=reason,
        )
        if cancelled is None:
            raise Conflict("Failed to cancel production run.")

        # ----- audit (AuditAction.CANCEL) -----
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CANCEL,
                entity_type=ENTITY_PRODUCTION_RUNS,
                entity_id=run_id,
                old_values={
                    "lifecycle_status": current_status,
                    "version": int(run.get("version", 0)),
                },
                new_values={
                    "lifecycle_status": LifecycleStatus.CANCELLED.value,
                    "cancellation_date": cancellation_date.isoformat(),
                    "cancellation_reason": reason,
                    "cancelled_by": principal_user_id,
                    "reversal_movements": len(movements),
                },
                ctx=ctx,
            )

        # ----- enrich + return -----
        final = await self._repo.get(run_id)
        if final is None:
            raise NotFound("Production run disappeared after cancel.")
        enriched = await self._enrich(final)

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=200,
                body=_to_jsonb(enriched) or "{}",
                user_id=principal_user_id,
            )

        return enriched, False


__all__ = ["ProductionRunService"]
