"""Receipt-batch sorting lifecycle with exact quantity and value snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.audit.service import ENTITY_SORTING_EVENTS, AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.db import UnitOfWork
from app.errors import Conflict, NotFound, ValidationFailed
from app.inventory.sorting import allocate_cents, to_base_quantity, validate_reconciliation
from app.services.idempotency import IdempotencyStore, compute_request_fingerprint
from app.validation.enums import AuditAction

QTY = Decimal("0.0001")
MONEY = Decimal("0.01")


def _d(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _q2(value: Decimal) -> Decimal:
    return value.quantize(MONEY)


def _q4_exact(value: Any, label: str) -> Decimal:
    raw = _d(value)
    rounded = raw.quantize(QTY)
    if raw != rounded:
        raise ValidationFailed(f"{label} must be representable at 4 decimal places.")
    return rounded


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def _etag(row: dict[str, Any]) -> str:
    return f'"{int(row["version"])}"'


class SortingService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list_pending(self) -> list[dict[str, Any]]:
        rows = await self._uow.fetch_all(
            """SELECT b.id,b.purchase_line_id,b.unit_code_snapshot,b.unit_name_snapshot,
                      b.factor_to_base_snapshot,b.base_unit_code_snapshot,b.original_quantity,
                      b.original_value,COALESCE(SUM(m.quantity),0) AS available_quantity,
                      COALESCE(SUM(m.total_cost),0) AS inventory_value
               FROM pending_receipt_batches b
               JOIN pending_inventory_movements m ON m.pending_receipt_batch_id=b.id
               GROUP BY b.id HAVING SUM(m.quantity)>0 ORDER BY b.id"""
        )
        for row in rows:
            for key in ("factor_to_base_snapshot", "original_quantity", "original_value", "available_quantity", "inventory_value"):
                row[key] = float(row[key] or 0)
        return rows

    async def get_event(self, event_id: int) -> dict[str, Any] | None:
        event = await self._uow.first_row(
            """SELECT e.*,EXISTS(SELECT 1 FROM sorting_event_reversals r WHERE r.sorting_event_id=e.id) AS reversed
               FROM sorting_events e WHERE e.id=:id""", {"id": event_id}
        )
        if event is None:
            return None
        event["outputs"] = await self._uow.fetch_all(
            """SELECT o.*,p.name AS product_name FROM sorting_event_outputs o
               JOIN products p ON p.id=o.product_id WHERE o.sorting_event_id=:id ORDER BY o.line_number""",
            {"id": event_id},
        )
        return event

    async def list_events(self, pending_receipt_batch_id: int | None = None) -> list[dict[str, Any]]:
        params = {"batch": pending_receipt_batch_id}

        rows = await self._uow.fetch_all(
            """SELECT e.id,e.pending_receipt_batch_id,e.input_quantity,e.loss_quantity,
                       e.remaining_quantity,e.source_value_snapshot,e.loss_value_snapshot,
                       e.financial_category_id,e.reason,e.created_at,e.created_by,e.posted_at,
                       e.posted_by,e.version,EXISTS(SELECT 1 FROM sorting_event_reversals r
                       WHERE r.sorting_event_id=e.id) AS reversed
                FROM sorting_events e
                WHERE (CAST(:batch AS BIGINT) IS NULL OR e.pending_receipt_batch_id=:batch)
                ORDER BY e.id DESC""", params
        )
        for row in rows:
            row["outputs"] = await self._uow.fetch_all(
                """SELECT o.*,p.name AS product_name FROM sorting_event_outputs o
                   JOIN products p ON p.id=o.product_id WHERE o.sorting_event_id=:id ORDER BY o.line_number""",
                {"id": row["id"]},
            )
        return rows

    async def _prepare(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        batch_id = int(payload["pending_receipt_batch_id"])
        batch = await self._uow.first_row("SELECT * FROM pending_receipt_batches WHERE id=:id", {"id": batch_id})
        if batch is None:
            raise NotFound(f"Pending receipt batch {batch_id} not found.")
        input_qty = _q4_exact(payload["input_quantity"], "Input quantity")
        loss_qty = _q4_exact(payload.get("loss_quantity", 0), "Loss quantity")
        remaining_qty = _q4_exact(payload.get("remaining_quantity", 0), "Remaining quantity")
        prepared: list[dict[str, Any]] = []
        weights: dict[int, Decimal] = {}
        if not str(payload["reason"]).strip():
            raise ValidationFailed("A sorting reason is required.")
        for index, raw in enumerate(payload["outputs"], start=1):
            product = await self._uow.first_row(
                """SELECT p.id,p.name,p.unit_id,p.is_active,p.is_sellable,u.code,u.dimension,u.factor_to_base
                   FROM products p LEFT JOIN units u ON u.id=p.unit_id WHERE p.id=:id""",
                {"id": int(raw["product_id"])},
            )
            if product is None or not product["is_active"] or not product["is_sellable"]:
                raise ValidationFailed("Sorting outputs must reference active, sellable classified products.")
            if product.get("dimension") != "mass" or product.get("factor_to_base") is None:
                raise ValidationFailed("Sorting outputs require products configured with a mass unit and conversion factor.")
            qty = _q4_exact(raw["quantity"], "Output quantity")
            try:
                base_qty = to_base_quantity(qty, _d(product["factor_to_base"]))
            except ValueError as exc:
                raise ValidationFailed(str(exc)) from exc
            weights[index] = base_qty
            prepared.append({
                "line_number": index, "product_id": int(product["id"]), "product_name": product["name"],
                "quantity": qty, "unit_id": int(product["unit_id"]), "unit_code": product["code"],
                "factor": _d(product["factor_to_base"]), "base_quantity": base_qty,
            })
        try:
            validate_reconciliation(input_qty, weights, loss_qty, remaining_qty)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        if loss_qty > 0:
            category_id = payload.get("financial_category_id")
            if category_id is None:
                raise ValidationFailed("Choose an expense category to recognize physical sorting loss.")
            category = await self._uow.first_row(
                "SELECT id FROM financial_categories WHERE id=:id AND entry_type='expense' AND is_active=TRUE",
                {"id": int(category_id)},
            )
            if category is None:
                raise ValidationFailed("Sorting loss requires an active expense category.")
        elif payload.get("financial_category_id") is not None:
            raise ValidationFailed("An expense category is only valid when loss quantity is greater than zero.")
        pending = await self._uow.first_row(
            "SELECT COALESCE(SUM(quantity),0) AS q FROM pending_inventory_movements WHERE pending_receipt_batch_id=:id",
            {"id": batch_id},
        )
        if input_qty > _d(pending["q"] if pending else 0):
            raise Conflict("Sorting input exceeds the available unsorted balance.", details={"available_quantity": float(_d(pending["q"] if pending else 0))})
        return ({"batch": batch, "input_quantity": input_qty, "loss_quantity": loss_qty,
                 "remaining_quantity": remaining_qty, "financial_category_id": payload.get("financial_category_id"),
                 "reason": str(payload["reason"]).strip()}, prepared)

    async def create_draft(self, payload: dict[str, Any], *, user_id: int, ctx: AuditContext | None) -> dict[str, Any]:
        core, outputs = await self._prepare(payload)
        event = await self._uow.first_row(
            """INSERT INTO sorting_events(pending_receipt_batch_id,input_quantity,loss_quantity,
                   remaining_quantity,financial_category_id,reason,created_by)
               VALUES(:batch,:input,:loss,:remaining,:category,:reason,:user) RETURNING *""",
            {"batch": core["batch"]["id"], "input": core["input_quantity"], "loss": core["loss_quantity"],
             "remaining": core["remaining_quantity"], "category": core["financial_category_id"],
             "reason": core["reason"], "user": user_id},
        )
        for output in outputs:
            await self._uow.execute(
                """INSERT INTO sorting_event_outputs(sorting_event_id,product_id,quantity,unit_id,
                       unit_code_snapshot,factor_to_base_snapshot,base_quantity,value_snapshot,line_number)
                   VALUES(:event,:product,:quantity,:unit,:code,:factor,:base,0,:line)""",
                {"event": event["id"], "product": output["product_id"], "quantity": output["quantity"],
                 "unit": output["unit_id"], "code": output["unit_code"], "factor": output["factor"],
                 "base": output["base_quantity"], "line": output["line_number"]},
            )
        if ctx is not None:
            await write_audit(self._uow, action=AuditAction.CREATE, entity_type=ENTITY_SORTING_EVENTS,
                entity_id=int(event["id"]), new_values={"pending_receipt_batch_id": core["batch"]["id"],
                "input_quantity": str(core["input_quantity"]), "loss_quantity": str(core["loss_quantity"]),
                "remaining_quantity": str(core["remaining_quantity"])}, reason=core["reason"], ctx=ctx)
        return (await self.get_event(int(event["id"]))) or {}

    async def update_draft(self, event_id: int, payload: dict[str, Any], *, if_match: str | None, ctx: AuditContext | None) -> dict[str, Any]:
        current = await self._uow.first_row("SELECT * FROM sorting_events WHERE id=:id FOR UPDATE", {"id": event_id})
        if current is None:
            raise NotFound(f"Sorting event {event_id} not found.")
        if current["posted_at"] is not None:
            raise Conflict("Posted sorting events are immutable; create a linked correction if eligible.")
        check_if_match(provided=if_match, current_etag=_etag(current))
        old = dict(current)
        core, outputs = await self._prepare(payload)
        await self._uow.execute("DELETE FROM sorting_event_outputs WHERE sorting_event_id=:id", {"id": event_id})
        await self._uow.execute(
            """UPDATE sorting_events SET pending_receipt_batch_id=:batch,input_quantity=:input,
                   loss_quantity=:loss,remaining_quantity=:remaining,financial_category_id=:category,
                   reason=:reason,version=version+1 WHERE id=:id""",
            {"batch": core["batch"]["id"], "input": core["input_quantity"], "loss": core["loss_quantity"],
             "remaining": core["remaining_quantity"], "category": core["financial_category_id"],
             "reason": core["reason"], "id": event_id},
        )
        for output in outputs:
            await self._uow.execute(
                """INSERT INTO sorting_event_outputs(sorting_event_id,product_id,quantity,unit_id,
                       unit_code_snapshot,factor_to_base_snapshot,base_quantity,value_snapshot,line_number)
                   VALUES(:event,:product,:quantity,:unit,:code,:factor,:base,0,:line)""",
                {"event": event_id, "product": output["product_id"], "quantity": output["quantity"],
                 "unit": output["unit_id"], "code": output["unit_code"], "factor": output["factor"],
                 "base": output["base_quantity"], "line": output["line_number"]},
            )
        if ctx is not None:
            await write_audit(self._uow, action=AuditAction.UPDATE, entity_type=ENTITY_SORTING_EVENTS,
                entity_id=event_id, old_values=old, new_values={"input_quantity": str(core["input_quantity"]),
                "loss_quantity": str(core["loss_quantity"]), "remaining_quantity": str(core["remaining_quantity"])},
                reason=core["reason"], ctx=ctx)
        return (await self.get_event(event_id)) or {}

    async def preview(self, event_id: int) -> dict[str, Any]:
        event = await self.get_event(event_id)
        if event is None:
            raise NotFound(f"Sorting event {event_id} not found.")
        batch_id = int(event["pending_receipt_batch_id"])
        balance = await self._uow.first_row(
            "SELECT COALESCE(SUM(quantity),0) AS q,COALESCE(SUM(total_cost),0) AS v FROM pending_inventory_movements WHERE pending_receipt_batch_id=:id",
            {"id": batch_id},
        )
        available_q, available_v = _d(balance["q"]), _d(balance["v"])
        input_q = _d(event["input_quantity"])
        if input_q > available_q or available_q <= 0:
            raise Conflict("Sorting input exceeds the available unsorted balance.")
        source_value = allocate_cents(available_v, {1: input_q, 2: available_q - input_q})[1]
        line_weights = {int(row["line_number"]): _d(row["base_quantity"]) for row in event["outputs"]}
        if _d(event["loss_quantity"]) > 0:
            line_weights[0] = _d(event["loss_quantity"])
        if _d(event["remaining_quantity"]) > 0:
            line_weights[-1] = _d(event["remaining_quantity"])
        allocation = allocate_cents(source_value, line_weights)
        outputs = [{**row, "value_snapshot": allocation[int(row["line_number"])]} for row in event["outputs"]]
        return {"id": event_id, "input_quantity": float(input_q), "source_value": float(source_value),
                "outputs": [{**row, "quantity": float(row["quantity"]),
                             "base_quantity": float(row["base_quantity"]),
                             "factor_to_base_snapshot": float(row["factor_to_base_snapshot"]),
                             "value_snapshot": float(row["value_snapshot"])} for row in outputs],
                "loss_quantity": float(event["loss_quantity"]),
                "loss_value": float(allocation.get(0, Decimal("0.00"))),
                "remaining_quantity": float(event["remaining_quantity"]),
                "remaining_value": float(allocation.get(-1, Decimal("0.00"))),
                "available_quantity": float(available_q), "available_value": float(available_v),
                "reconciles": sum(allocation.values(), Decimal("0")) == source_value}

    async def post(self, event_id: int, *, user_id: int, if_match: str | None,
                   idempotency_key: str | None, ctx: AuditContext | None,
                   request_body: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
        store = IdempotencyStore(self._uow)
        idem = None
        if idempotency_key:
            idem = await store.start(key=idempotency_key, user_id=user_id,
                endpoint="POST /sorting-events/{id}/post",
                fingerprint=compute_request_fingerprint(method="POST", path=f"/sorting-events/{event_id}/post", body=request_body))
            if idem is not None and idem.is_completed:
                await self._uow.commit()
                return idem.response_body, True
        event = await self._uow.first_row("SELECT * FROM sorting_events WHERE id=:id FOR UPDATE", {"id": event_id})
        if event is None:
            raise NotFound(f"Sorting event {event_id} not found.")
        if event["posted_at"] is not None:
            raise Conflict("Sorting event is already posted.")
        check_if_match(provided=if_match, current_etag=_etag(event))
        batch = await self._uow.first_row("SELECT * FROM pending_receipt_batches WHERE id=:id FOR UPDATE", {"id": event["pending_receipt_batch_id"]})
        await self.preview(event_id)
        outputs = await self._uow.fetch_all("SELECT * FROM sorting_event_outputs WHERE sorting_event_id=:id ORDER BY line_number", {"id": event_id})
        pids = sorted({int(row["product_id"]) for row in outputs})
        for product_id in pids:
            await self._uow.first_row("SELECT id FROM products WHERE id=:id FOR UPDATE", {"id": product_id})
        for output in outputs:
            current_product = await self._uow.first_row(
                """SELECT p.is_active,p.is_sellable,p.unit_id,u.dimension,u.factor_to_base
                   FROM products p LEFT JOIN units u ON u.id=p.unit_id WHERE p.id=:id""",
                {"id": output["product_id"]},
            )
            if (current_product is None or not current_product["is_active"] or not current_product["is_sellable"]
                    or current_product["unit_id"] != output["unit_id"]
                    or current_product["dimension"] != "mass"
                    or _d(current_product["factor_to_base"]) != _d(output["factor_to_base_snapshot"])):
                raise Conflict("A sorting output product or conversion changed after draft creation; update the draft and preview again.")
        if _d(event["loss_quantity"]) > 0:
            category = await self._uow.first_row(
                "SELECT id FROM financial_categories WHERE id=:id AND entry_type='expense' AND is_active=TRUE",
                {"id": event["financial_category_id"]},
            )
            if category is None:
                raise Conflict("The sorting loss expense category is no longer active; update the draft before posting.")
        # Recheck after taking source and product locks. The pending-ledger trigger is the DB backstop.
        batch_row = await self._uow.first_row(
            "SELECT COALESCE(SUM(quantity),0) AS q,COALESCE(SUM(total_cost),0) AS v FROM pending_inventory_movements WHERE pending_receipt_batch_id=:id",
            {"id": batch["id"]},
        )
        q, v = _d(batch_row["q"]), _d(batch_row["v"])
        input_q = _d(event["input_quantity"])
        if input_q > q or q <= 0:
            raise Conflict("Insufficient pending stock; refresh the source balance before posting.")
        source_value = allocate_cents(v, {1: input_q, 2: q-input_q})[1]
        weights = {int(row["line_number"]): _d(row["base_quantity"]) for row in outputs}
        if _d(event["loss_quantity"]) > 0:
            weights[0] = _d(event["loss_quantity"])
        if _d(event["remaining_quantity"]) > 0:
            weights[-1] = _d(event["remaining_quantity"])
        values = allocate_cents(source_value, weights)
        actor = user_id
        batch_id = int(batch["id"])
        input_cost_per_kg = (source_value / input_q).quantize(Decimal("0.0001"))
        await self._uow.execute(
            """INSERT INTO pending_inventory_movements(pending_receipt_batch_id,trigger,quantity,
                   unit_cost_at_movement,total_cost,reference_type,reference_id,created_by)
               VALUES(:batch,'sorting_input',:qty,:unit,:value,'sorting_event',:event,:actor)""",
            {"batch": batch_id, "qty": -input_q, "unit": input_cost_per_kg, "value": -source_value,
             "event": event_id, "actor": actor},
        )
        for row in outputs:
            amount = values[int(row["line_number"])]
            quantity = _d(row["quantity"])
            unit_cost = (amount / quantity).quantize(Decimal("0.0001")) if quantity else Decimal("0")
            movement = await self._uow.first_row(
                """INSERT INTO stock_movements(product_id,trigger,quantity,unit_cost_at_movement,total_cost,
                       reference_type,reference_id,reference_line_id,created_by)
                   VALUES(:product,'sorting_output',:qty,:unit,:value,'sorting_event',:event,:line,:actor)
                   RETURNING id""",
                {"product": row["product_id"], "qty": quantity, "unit": unit_cost, "value": amount,
                 "event": event_id, "line": row["id"], "actor": actor},
            )
            await self._uow.execute("UPDATE sorting_event_outputs SET value_snapshot=:value,stock_movement_id=:movement WHERE id=:id",
                {"value": amount, "movement": movement["id"], "id": row["id"]})
        remainder_q = _d(event["remaining_quantity"])
        remainder_value = values.get(-1, Decimal("0.00"))
        if remainder_q > 0:
            await self._uow.execute(
                """INSERT INTO pending_inventory_movements(pending_receipt_batch_id,trigger,quantity,
                       unit_cost_at_movement,total_cost,reference_type,reference_id,created_by)
                   VALUES(:batch,'sorting_remainder',:qty,:unit,:value,'sorting_event',:event,:actor)""",
                {"batch": batch_id, "qty": remainder_q, "unit": (remainder_value/remainder_q).quantize(Decimal("0.0001")),
                 "value": remainder_value, "event": event_id, "actor": actor},
            )
        after_q = q - input_q + remainder_q
        after_value = v - source_value + remainder_value
        now = datetime.now(UTC)
        await self._uow.execute(
            """UPDATE sorting_events SET source_value_snapshot=:source,loss_value_snapshot=:loss,
                   posted_at=:now,posted_by=:actor WHERE id=:id""",
            {"source": source_value, "loss": values.get(0, Decimal("0.00")),
             "now": now, "actor": actor, "id": event_id},
        )
        if ctx is not None:
            await write_audit(self._uow, action=AuditAction.POST, entity_type=ENTITY_SORTING_EVENTS,
                entity_id=event_id, old_values={"posted_at": None},
                new_values={"posted_at": now.isoformat(), "source_value": str(source_value),
                            "loss_value": str(values.get(0, Decimal("0.00"))), "output_values": {str(k): str(v) for k,v in values.items()},
                            "source_balance_before": {"quantity": str(q), "value": str(v)},
                            "source_balance_after": {"quantity": str(after_q), "value": str(after_value)}},
                reason=str(event["reason"]), ctx=ctx)
        result = (await self.get_event(event_id)) or {}
        if idem is not None:
            await store.complete(record_id=idem.id, status=200, body=_json_safe(result), user_id=user_id)
        return result, False

    async def reverse(self, event_id: int, *, user_id: int, reason: str,
                      if_match: str | None, ctx: AuditContext | None) -> dict[str, Any]:
        event = await self._uow.first_row("SELECT * FROM sorting_events WHERE id=:id FOR UPDATE", {"id": event_id})
        if event is None:
            raise NotFound(f"Sorting event {event_id} not found.")
        if event["posted_at"] is None:
            raise Conflict("Only a posted sorting event can be reversed.")
        if _d(event["loss_quantity"]) != 0:
            raise Conflict("This sorting event recorded physical loss and cannot be reversed. Use a linked correction after physical recount.")
        if await self._uow.first_row("SELECT 1 FROM sorting_event_reversals WHERE sorting_event_id=:id", {"id": event_id}):
            raise Conflict("Sorting event is already reversed.")
        check_if_match(provided=if_match, current_etag=_etag(event))
        batch_id = int(event["pending_receipt_batch_id"])
        await self._uow.first_row("SELECT id FROM pending_receipt_batches WHERE id=:id FOR UPDATE", {"id": batch_id})
        balance_before = await self._uow.first_row(
            "SELECT COALESCE(SUM(quantity),0) q,COALESCE(SUM(total_cost),0) v FROM pending_inventory_movements WHERE pending_receipt_batch_id=:id",
            {"id": batch_id})
        outputs = await self._uow.fetch_all("SELECT * FROM sorting_event_outputs WHERE sorting_event_id=:id ORDER BY product_id,id", {"id": event_id})
        product_ids = sorted({int(row["product_id"]) for row in outputs})
        for product_id in product_ids:
            await self._uow.first_row("SELECT id FROM products WHERE id=:id FOR UPDATE", {"id": product_id})
        for output in outputs:
            later = await self._uow.first_row("SELECT 1 FROM stock_movements WHERE product_id=:product AND id>:movement LIMIT 1",
                {"product": output["product_id"], "movement": output["stock_movement_id"]})
            if later:
                raise Conflict("A sorting output has later inventory activity and is no longer safely reversible.")
        reversal = await self._uow.first_row(
            "INSERT INTO sorting_event_reversals(sorting_event_id,reason,created_by) VALUES(:id,:reason,:user) RETURNING id",
            {"id": event_id, "reason": reason, "user": user_id})
        source_input = await self._uow.first_row(
            "SELECT id,quantity,total_cost,unit_cost_at_movement FROM pending_inventory_movements WHERE reference_type='sorting_event' AND reference_id=:id AND trigger='sorting_input'",
            {"id": event_id})
        if source_input:
            await self._uow.execute(
                """INSERT INTO pending_inventory_movements(pending_receipt_batch_id,trigger,quantity,
                       unit_cost_at_movement,total_cost,reference_type,reference_id,reversal_of_movement_id,reason,created_by)
                   VALUES(:batch,'sorting_reversal',:qty,:unit,:value,'sorting_event',:event,:original,:reason,:user)""",
                {"batch": batch_id, "qty": -_d(source_input["quantity"]), "unit": source_input["unit_cost_at_movement"],
                 "value": -_d(source_input["total_cost"]), "event": event_id, "original": source_input["id"], "reason": reason, "user": user_id})
        remainder = await self._uow.first_row(
            "SELECT id,quantity,total_cost,unit_cost_at_movement FROM pending_inventory_movements WHERE reference_type='sorting_event' AND reference_id=:id AND trigger='sorting_remainder'",
            {"id": event_id})
        if remainder:
            await self._uow.execute(
                """INSERT INTO pending_inventory_movements(pending_receipt_batch_id,trigger,quantity,
                       unit_cost_at_movement,total_cost,reference_type,reference_id,reversal_of_movement_id,reason,created_by)
                   VALUES(:batch,'sorting_reversal',:qty,:unit,:value,'sorting_event',:event,:original,:reason,:user)""",
                {"batch": batch_id, "qty": -_d(remainder["quantity"]), "unit": remainder["unit_cost_at_movement"],
                 "value": -_d(remainder["total_cost"]), "event": event_id, "original": remainder["id"], "reason": reason, "user": user_id})
        for output in outputs:
            original = await self._uow.first_row("SELECT id FROM stock_movements WHERE id=:id", {"id": output["stock_movement_id"]})
            await self._uow.execute(
                """INSERT INTO stock_movements(product_id,trigger,quantity,unit_cost_at_movement,total_cost,
                       reference_type,reference_id,reference_line_id,reversal_of_movement_id,reason,created_by)
                   SELECT product_id,'sorting_reversal',-quantity,unit_cost_at_movement,-total_cost,
                       'sorting_event',:event,:line,id,:reason,:user FROM stock_movements WHERE id=:original""",
                {"event": event_id, "line": output["id"], "reason": reason, "user": user_id,
                 "original": original["id"]})
        balance_after = await self._uow.first_row(
            "SELECT COALESCE(SUM(quantity),0) q,COALESCE(SUM(total_cost),0) v FROM pending_inventory_movements WHERE pending_receipt_batch_id=:id",
            {"id": batch_id})
        if ctx is not None:
            await write_audit(self._uow, action=AuditAction.CANCEL, entity_type=ENTITY_SORTING_EVENTS,
                entity_id=event_id,
                old_values={"reversed": False, "source_balance": {"quantity": str(balance_before["q"]), "value": str(balance_before["v"])}},
                new_values={"reversal_id": reversal["id"], "reversed": True,
                            "source_balance": {"quantity": str(balance_after["q"]), "value": str(balance_after["v"])}},
                reason=reason, ctx=ctx)
        return (await self.get_event(event_id)) or {}
