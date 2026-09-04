"""F.1 - Sales + Purchases report repository.

Implements the sales report aggregate (per D-8 evidence):

* ``sales.total_amount`` is **immutable at the original gross value**
  - the lifecycle trigger (``fn_sale_lifecycle_terminal``,
  ``schema.sql`` §18.6) fires only on ``UPDATE OF lifecycle_status``
  and the only column it writes is ``lifecycle_status`` itself. The
  create-return service path (``app.services.sales.create_sale_return``)
  uses ``set_lifecycle_status`` which only updates ``lifecycle_status``.
  Lines are immutable after post (``fn_sale_lines_immutable_when_posted``,
  ``schema.sql`` §18.7). Returns are tracked separately in
  ``sales_returns.total_selling_price_returned`` (selling) and
  ``sales_returns.total_cost_returned`` (cost).

* Therefore ``revenue`` is computed as:

  ``SUM(s.total_amount) - SUM(sr.total_selling_price_returned)``

  where ``s`` is in the period, posted, and non-cancelled; ``sr`` is
  a posted sales return (``lifecycle_status = 'posted'``) whose
  parent ``s.sale_date`` falls in the period.

  Symmetric for COGS using ``sale_lines.cogs_total_snapshot`` and
  ``sales_returns.total_cost_returned``.

Single round-trip via CTE: one ``WITH`` produces the per-sale
revenue/cogs base; a second aggregates; the comparison window is
just the same query over a different ``[from, to]``.

ruff: S608 ignored - every interpolated fragment is a hard-coded
identifier or a value pulled from a hard-coded whitelist. All
user-supplied values are bound parameters.
"""

# ruff: noqa: S608

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import UnitOfWork

__all__ = [
    "count_cash_flow_report",
    "count_crl_report",
    "count_manual_report",
    "count_payables_report",
    "count_production_report",
    "count_purchase_returns_report",
    "count_purchases_report",
    "count_receivables_report",
    "count_refunds_report",
    "count_sales_returns_report",
    "count_supplier_receivables_report",
    "count_supplier_repayments_report",
    "fetch_cash_flow_aggregates",
    "fetch_crl_aggregates",
    "fetch_manual_aggregates",
    "fetch_payables_aggregates",
    "fetch_pnl_aggregates",
    "fetch_production_aggregates",
    "fetch_purchase_returns_aggregates",
    "fetch_purchases_aggregates",
    "fetch_receivables_aggregates",
    "fetch_refunds_aggregates",
    "fetch_sales_aggregates",
    "fetch_sales_returns_aggregates",
    "fetch_supplier_receivables_aggregates",
    "fetch_supplier_repayments_aggregates",
    "list_cash_flow_report",
    "list_crl_report",
    "list_manual_report",
    "list_payables_report",
    "list_production_report",
    "list_purchase_returns_report",
    "list_purchases_report",
    "list_receivables_report",
    "list_refunds_report",
    "list_sales_returns_report",
    "list_supplier_receivables_report",
    "list_supplier_repayments_report",
]


# The CTE produces per-sale net revenue/cogs (gross - returned), so
# the outer SELECT is a simple SUM. Posted returns (``sr.lifecycle_status
# = 'posted'``) are deducted; cancelled returns are excluded.
_AGGREGATE_SQL = """
WITH base AS (
    SELECT
        s.id,
        s.total_amount,
        COALESCE((
            SELECT SUM(sl.cogs_total_snapshot)
            FROM sale_lines sl
            WHERE sl.sale_id = s.id
        ), 0) AS cogs_total
    FROM sales s
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
),
returned AS (
    SELECT
        sr.sale_id,
        COALESCE(SUM(sr.total_selling_price_returned) FILTER (
            WHERE sr.lifecycle_status = 'posted'
        ), 0) AS selling_returned,
        COALESCE(SUM(sr.total_cost_returned) FILTER (
            WHERE sr.lifecycle_status = 'posted'
        ), 0) AS cost_returned
    FROM sales_returns sr
    JOIN sales s ON s.id = sr.sale_id
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
    GROUP BY sr.sale_id
),
net AS (
    SELECT
        b.id,
        GREATEST(b.total_amount - COALESCE(r.selling_returned, 0), 0) AS net_revenue,
        GREATEST(b.cogs_total - COALESCE(r.cost_returned, 0), 0) AS net_cogs
    FROM base b
    LEFT JOIN returned r ON r.sale_id = b.id
)
SELECT
    COALESCE(SUM(net_revenue), 0)  AS revenue,
    COALESCE(SUM(net_cogs), 0)     AS cogs,
    COALESCE(SUM(net_revenue - net_cogs), 0) AS gross_profit,
    COUNT(*)                       AS sales_count
FROM net
"""


async def fetch_sales_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    """Return ``{revenue, cogs, gross_profit, sales_count}`` for the window.

    ``average_ticket`` is computed by the caller (route layer) from
    ``revenue / sales_count`` to keep the SQL single-purpose.
    """
    row = await uow.first_row(
        _AGGREGATE_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {
            "revenue": 0.0,
            "cogs": 0.0,
            "gross_profit": 0.0,
            "sales_count": 0,
        }
    revenue = float(row.get("revenue") or 0)
    cogs = float(row.get("cogs") or 0)
    gross_profit = float(row.get("gross_profit") or 0)
    sales_count = int(row.get("sales_count") or 0)
    return {
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "sales_count": sales_count,
    }


# ---------------------------------------------------------------------------
# Purchases report - paginated list of posted, non-cancelled purchases in
# the period, plus a count for pagination. Returns the existing purchase
# header row shape (purchases has no `total_amount` column - total is
# derived from purchase_lines.line_total + purchase_shipping.amount in
# the existing E.2 path; the F.1 row shape mirrors the E.2 column list
# so the route layer can hand each row back as-is).
# ---------------------------------------------------------------------------

_PURCHASE_COLS = (
    "id, reference_no, supplier_id, purchase_date, received_date, "
    "lifecycle_status, posted_at, posted_by, cancellation_date, "
    "cancelled_by, notes, created_at, updated_at, created_by, version"
)

# Closed whitelist for the ``sort`` query parameter. Anything not in
# this map falls back to ``id`` (stable primary-key order).
_PURCHASE_SORT_MAP: dict[str, str] = {
    "id": "id ASC",
    "-id": "id DESC",
    "purchase_date": "purchase_date ASC, id ASC",
    "-purchase_date": "purchase_date DESC, id ASC",
    "lifecycle_status": "lifecycle_status ASC, id ASC",
    "-lifecycle_status": "lifecycle_status DESC, id ASC",
    "created_at": "created_at ASC, id ASC",
    "-created_at": "created_at DESC, id ASC",
    "updated_at": "updated_at ASC, id ASC",
    "-updated_at": "updated_at DESC, id ASC",
}


def _purchase_order_by(sort: str) -> str:
    return _PURCHASE_SORT_MAP.get((sort or "id").strip(), "id ASC")


def _purchase_where(
    *,
    from_iso: datetime | None,
    to_iso: datetime | None,
    supplier_id: int | None,
    lifecycle_status: str | None,
    posted_by: int | None,
    q: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = ["lifecycle_status <> 'cancelled'", "posted_at IS NOT NULL"]
    params: dict[str, Any] = {}
    if from_iso is not None:
        clauses.append("purchase_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("purchase_date <= :to_iso")
        params["to_iso"] = to_iso
    if supplier_id is not None:
        clauses.append("supplier_id = :supplier_id")
        params["supplier_id"] = int(supplier_id)
    if lifecycle_status is not None:
        clauses.append("lifecycle_status = :lifecycle_status")
        params["lifecycle_status"] = str(lifecycle_status)
    if posted_by is not None:
        clauses.append("posted_by = :posted_by")
        params["posted_by"] = int(posted_by)
    if q:
        clauses.append(
            "(COALESCE(reference_no, '') ILIKE :q OR COALESCE(notes, '') ILIKE :q)"
        )
        params["q"] = f"%{q}%"
    return "WHERE " + " AND ".join(clauses), params


async def count_purchases_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime | None,
    to_iso: datetime | None,
    supplier_id: int | None,
    lifecycle_status: str | None,
    posted_by: int | None,
    q: str | None,
) -> int:
    where, params = _purchase_where(
        from_iso=from_iso,
        to_iso=to_iso,
        supplier_id=supplier_id,
        lifecycle_status=lifecycle_status,
        posted_by=posted_by,
        q=q,
    )
    total = await uow.scalar(f"SELECT COUNT(*) FROM purchases {where}", params)
    return int(total or 0)


async def list_purchases_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    sort: str,
    from_iso: datetime | None,
    to_iso: datetime | None,
    supplier_id: int | None,
    lifecycle_status: str | None,
    posted_by: int | None,
    q: str | None,
) -> list[dict[str, Any]]:
    where, params = _purchase_where(
        from_iso=from_iso,
        to_iso=to_iso,
        supplier_id=supplier_id,
        lifecycle_status=lifecycle_status,
        posted_by=posted_by,
        q=q,
    )
    order = _purchase_order_by(sort)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_PURCHASE_COLS} FROM purchases {where} "
        f"ORDER BY {order} LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# Aggregate for purchases comparison block. Mirrors the sales report
# shape (count + sum of qualifying lines) so the same PeriodComparison
# envelope can be reused. Purchases have no header-level total; the
# comparison block exposes ``purchase_count`` and ``total_amount``
# (Σ purchase_lines.line_total - the canonical per-purchase value
# before shipping allocation, per DB spec §2.5.2).
_PURCHASE_AGG_SQL = """
SELECT
    COUNT(DISTINCT p.id) AS purchase_count,
    COALESCE(SUM(pl.line_total), 0) AS total_amount
FROM purchases p
JOIN purchase_lines pl ON pl.purchase_id = p.id
WHERE p.lifecycle_status <> 'cancelled'
  AND p.posted_at IS NOT NULL
  AND p.purchase_date >= :from_iso
  AND p.purchase_date <= :to_iso
"""


async def fetch_purchases_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    """Return ``{purchase_count, total_amount}`` for the comparison block.

    The purchases report's response is a paginated list, not an
    aggregate. The comparison block (per D-7) is the only place
    that needs an aggregate - it shows the totals of the period
    alongside the previous period.
    """
    row = await uow.first_row(
        _PURCHASE_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"purchase_count": 0, "total_amount": 0.0}
    return {
        "purchase_count": int(row.get("purchase_count") or 0),
        "total_amount": float(row.get("total_amount") or 0),
    }


# ---------------------------------------------------------------------------
# F.2 - Sales-returns + Purchase-returns reports
#
# Paginated list of return-header rows (no new row model - same columns
# as the source table). Period filter is on the return date column
# (``sales_returns.return_date`` / ``purchase_returns.return_date``).
# Cancelled returns are still listed - the lifecycle column is preserved
# so clients can filter client-side, and ``sales_returns`` itself never
# hard-deletes (the cancel lifecycle is ``UPDATE OF lifecycle_status``
# only - per ``schema.sql`` §18.16).
#
# For F.2 the OpenAPI only declares Period/From/To/CompareTo, so the
# list/count helpers take only the date window. Server-side pagination
# defaults live in ``app.reports.returns`` (page=1, per_page=50).
# ---------------------------------------------------------------------------

_SALES_RETURN_COLS = (
    "id, sale_id, return_date, reason, total_selling_price_returned, "
    "total_cost_returned, lifecycle_status, created_at, created_by, "
    "cancellation_date, cancelled_by, version"
)

_PURCHASE_RETURN_COLS = (
    "id, purchase_id, return_date, reason, total_value_returned, "
    "lifecycle_status, created_at, created_by, version"
)


def _sales_return_where(
    *, from_iso: datetime | None, to_iso: datetime | None
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if from_iso is not None:
        clauses.append("return_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("return_date <= :to_iso")
        params["to_iso"] = to_iso
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


def _purchase_return_where(
    *, from_iso: datetime | None, to_iso: datetime | None
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if from_iso is not None:
        clauses.append("return_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("return_date <= :to_iso")
        params["to_iso"] = to_iso
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


async def count_sales_returns_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> int:
    where, params = _sales_return_where(from_iso=from_iso, to_iso=to_iso)
    total = await uow.scalar(
        f"SELECT COUNT(*) FROM sales_returns {where}", params
    )
    return int(total or 0)


async def list_sales_returns_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> list[dict[str, Any]]:
    where, params = _sales_return_where(from_iso=from_iso, to_iso=to_iso)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_SALES_RETURN_COLS} FROM sales_returns {where} "
        "ORDER BY return_date DESC, id ASC LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


async def count_purchase_returns_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> int:
    where, params = _purchase_return_where(from_iso=from_iso, to_iso=to_iso)
    total = await uow.scalar(
        f"SELECT COUNT(*) FROM purchase_returns {where}", params
    )
    return int(total or 0)


async def list_purchase_returns_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> list[dict[str, Any]]:
    where, params = _purchase_return_where(from_iso=from_iso, to_iso=to_iso)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_PURCHASE_RETURN_COLS} FROM purchase_returns {where} "
        "ORDER BY return_date DESC, id ASC LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# Aggregates for the comparison block. The monetary column differs by
# table - sales returns carry selling + cost; purchase returns carry
# a single ``total_value_returned``. To keep the comparison envelope
# uniform with the F.1 purchases report (``return_count`` +
# ``total_amount``), the sales-return aggregate sums
# ``total_selling_price_returned`` (the "amount returned to the
# customer" - what a finance person wants to see in period-over-period
# comparison). COGS-deduction is intentionally NOT included here
# because that would mix gross/net comparisons.
_SALES_RETURN_AGG_SQL = """
SELECT
    COUNT(*) AS return_count,
    COALESCE(SUM(total_selling_price_returned), 0) AS total_amount
FROM sales_returns
WHERE return_date >= :from_iso
  AND return_date <= :to_iso
"""

_PURCHASE_RETURN_AGG_SQL = """
SELECT
    COUNT(*) AS return_count,
    COALESCE(SUM(total_value_returned), 0) AS total_amount
FROM purchase_returns
WHERE return_date >= :from_iso
  AND return_date <= :to_iso
"""


async def fetch_sales_returns_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    row = await uow.first_row(
        _SALES_RETURN_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"return_count": 0, "total_amount": 0.0}
    return {
        "return_count": int(row.get("return_count") or 0),
        "total_amount": float(row.get("total_amount") or 0),
    }


async def fetch_purchase_returns_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    row = await uow.first_row(
        _PURCHASE_RETURN_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"return_count": 0, "total_amount": 0.0}
    return {
        "return_count": int(row.get("return_count") or 0),
        "total_amount": float(row.get("total_amount") or 0),
    }


# ---------------------------------------------------------------------------
# F.2 - Production report
#
# Paginated list of ``production_runs`` header rows. Period filter is
# on ``run_date`` (the date the run was performed). All lifecycle
# states (draft / posted / completed / cancelled) are listed - the
# lifecycle column is preserved so clients can filter.
# ---------------------------------------------------------------------------

_PRODUCTION_COLS = (
    "id, run_date, output_product_id, output_quantity, "
    "finished_unit_cost, total_raw_cost, total_overhead_cost, "
    "lifecycle_status, posted_at, posted_by, cancellation_date, "
    "cancellation_reason, cancelled_by, notes, created_at, updated_at, "
    "created_by, version"
)


def _production_where(
    *, from_iso: datetime | None, to_iso: datetime | None
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if from_iso is not None:
        clauses.append("run_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("run_date <= :to_iso")
        params["to_iso"] = to_iso
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


async def count_production_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> int:
    where, params = _production_where(from_iso=from_iso, to_iso=to_iso)
    total = await uow.scalar(
        f"SELECT COUNT(*) FROM production_runs {where}", params
    )
    return int(total or 0)


async def list_production_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> list[dict[str, Any]]:
    where, params = _production_where(from_iso=from_iso, to_iso=to_iso)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_PRODUCTION_COLS} FROM production_runs {where} "
        "ORDER BY run_date DESC, id ASC LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# Aggregate for the comparison block. ``total_amount`` = raw + overhead
# - the canonical run cost. Draft runs are excluded from the aggregate
# because their costs may still be moving; posted/completed/cancelled
# are the historical facts. (This matches the E.3 production post
# lifecycle decision: only posted runs are financially settled.)
_PRODUCTION_AGG_SQL = """
SELECT
    COUNT(*) AS run_count,
    COALESCE(SUM(total_raw_cost + total_overhead_cost), 0) AS total_amount
FROM production_runs
WHERE lifecycle_status IN ('posted', 'completed', 'cancelled')
  AND run_date >= :from_iso
  AND run_date <= :to_iso
"""


async def fetch_production_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    row = await uow.first_row(
        _PRODUCTION_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"run_count": 0, "total_amount": 0.0}
    return {
        "run_count": int(row.get("run_count") or 0),
        "total_amount": float(row.get("total_amount") or 0),
    }


# ---------------------------------------------------------------------------
# F.3 - Manual income/expense reports
#
# Paginated list of ``manual_finance_entries`` joined to
# ``financial_categories`` so the ``entry_type`` discriminator and
# category metadata are available without a second round-trip. The
# date filter is on ``entry_date`` (the date the entry was recorded,
# not the lifecycle timestamp). The list surfaces every row
# regardless of lifecycle; the aggregate EXCLUDES cancelled entries
# (the cancel lifecycle inserts a reversing ``cash_movement`` that
# already nets out in the cash-flow report).
# ---------------------------------------------------------------------------

# Closed whitelist of valid entry_type values. The DB CHECK
# constraint (``ck_financial_categories_entry_type``) only permits
# these two values.
_VALID_ENTRY_TYPES: frozenset[str] = frozenset({"income", "expense"})

# Row shape: header columns + the joined category discriminator +
# code + name. The route layer hands each row back as-is (the same
# row model E.8's manual-entries list endpoint returns).
_MANUAL_COLS = (
    "mfe.id, mfe.category_id, fc.code AS category_code, "
    "fc.name AS category_name, fc.entry_type, "
    "mfe.entry_date, mfe.amount, mfe.payment_method_id, mfe.notes, "
    "mfe.lifecycle_status, mfe.cancellation_date, "
    "mfe.cancellation_reason, mfe.cancelled_by, "
    "mfe.created_at, mfe.created_by, mfe.version"
)


def _manual_where(
    *,
    entry_type: str,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> tuple[str, dict[str, Any]]:
    if entry_type not in _VALID_ENTRY_TYPES:
        raise ValueError(
            f"invalid entry_type '{entry_type}'; expected one of: "
            f"{sorted(_VALID_ENTRY_TYPES)}"
        )
    clauses: list[str] = ["fc.entry_type = :entry_type"]
    params: dict[str, Any] = {"entry_type": entry_type}
    if from_iso is not None:
        clauses.append("mfe.entry_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("mfe.entry_date <= :to_iso")
        params["to_iso"] = to_iso
    return "WHERE " + " AND ".join(clauses), params


async def count_manual_report(
    uow: UnitOfWork,
    *,
    entry_type: str,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> int:
    where, params = _manual_where(
        entry_type=entry_type, from_iso=from_iso, to_iso=to_iso
    )
    sql = (
        f"SELECT COUNT(*) FROM manual_finance_entries mfe "
        f"JOIN financial_categories fc ON fc.id = mfe.category_id "
        f"{where}"
    )
    total = await uow.scalar(sql, params)
    return int(total or 0)


async def list_manual_report(
    uow: UnitOfWork,
    *,
    entry_type: str,
    page: int,
    per_page: int,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> list[dict[str, Any]]:
    where, params = _manual_where(
        entry_type=entry_type, from_iso=from_iso, to_iso=to_iso
    )
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_MANUAL_COLS} FROM manual_finance_entries mfe "
        f"JOIN financial_categories fc ON fc.id = mfe.category_id "
        f"{where} "
        "ORDER BY mfe.entry_date DESC, mfe.id ASC "
        "LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# Aggregate for the comparison block. Cancelled entries are EXCLUDED
# from the total - a cancellation creates a reversing cash_movement
# (per ``schema.sql`` §12.1 comment), so counting the original
# entry here would double-count when combined with the cash-flow
# report. The list still shows cancelled entries with their
# ``lifecycle_status`` so clients can audit the trail.
_MANUAL_AGG_SQL = """
SELECT
    COUNT(*) AS entry_count,
    COALESCE(SUM(mfe.amount), 0) AS total_amount
FROM manual_finance_entries mfe
JOIN financial_categories fc ON fc.id = mfe.category_id
WHERE fc.entry_type = :entry_type
  AND mfe.lifecycle_status = 'posted'
  AND mfe.entry_date >= :from_iso
  AND mfe.entry_date <= :to_iso
"""


async def fetch_manual_aggregates(
    uow: UnitOfWork,
    *,
    entry_type: str,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    if entry_type not in _VALID_ENTRY_TYPES:
        raise ValueError(
            f"invalid entry_type '{entry_type}'; expected one of: "
            f"{sorted(_VALID_ENTRY_TYPES)}"
        )
    row = await uow.first_row(
        _MANUAL_AGG_SQL,
        {"entry_type": entry_type, "from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"entry_count": 0, "total_amount": 0.0}
    return {
        "entry_count": int(row.get("entry_count") or 0),
        "total_amount": float(row.get("total_amount") or 0),
    }


# ---------------------------------------------------------------------------
# F.3 - Refunds report
#
# ``refunds`` has no lifecycle column (the cross-row INV-04 trigger
# enforces the refundable-amount ceiling at insert time). All rows
# in the period are listed and aggregated.
# ---------------------------------------------------------------------------

_REFUND_COLS = (
    "id, sale_id, amount, payment_method_id, refund_date, "
    "reason, refundable_amount_snapshot, created_at, created_by"
)


def _refund_where(
    *, from_iso: datetime | None, to_iso: datetime | None
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if from_iso is not None:
        clauses.append("refund_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("refund_date <= :to_iso")
        params["to_iso"] = to_iso
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


async def count_refunds_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> int:
    where, params = _refund_where(from_iso=from_iso, to_iso=to_iso)
    total = await uow.scalar(f"SELECT COUNT(*) FROM refunds {where}", params)
    return int(total or 0)


async def list_refunds_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> list[dict[str, Any]]:
    where, params = _refund_where(from_iso=from_iso, to_iso=to_iso)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_REFUND_COLS} FROM refunds {where} "
        "ORDER BY refund_date DESC, id ASC LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


_REFUND_AGG_SQL = """
SELECT
    COUNT(*) AS refund_count,
    COALESCE(SUM(amount), 0) AS total_amount
FROM refunds
WHERE refund_date >= :from_iso
  AND refund_date <= :to_iso
"""


async def fetch_refunds_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    row = await uow.first_row(
        _REFUND_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"refund_count": 0, "total_amount": 0.0}
    return {
        "refund_count": int(row.get("refund_count") or 0),
        "total_amount": float(row.get("total_amount") or 0),
    }


# ---------------------------------------------------------------------------
# F.3 - Supplier-repayments report
#
# ``supplier_repayments`` has no lifecycle column. ``amount`` is the
# obligation, ``received_amount`` is the cumulative cash received
# (updated by ``cash_movement`` rows with trigger='supplier_repayment'
# per schema §11.2). The aggregate sums ``received_amount`` (actual
# cash flow) not ``amount`` (the obligation, which doesn't change
# with cash events).
# ---------------------------------------------------------------------------

_SUPPLIER_REPAYMENT_COLS = (
    "id, purchase_id, amount, received_amount, payment_method_id, "
    "repayment_date, reason, refundable_amount_snapshot, "
    "created_at, created_by"
)


def _supplier_repayment_where(
    *, from_iso: datetime | None, to_iso: datetime | None
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if from_iso is not None:
        clauses.append("repayment_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("repayment_date <= :to_iso")
        params["to_iso"] = to_iso
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


async def count_supplier_repayments_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> int:
    where, params = _supplier_repayment_where(
        from_iso=from_iso, to_iso=to_iso
    )
    total = await uow.scalar(
        f"SELECT COUNT(*) FROM supplier_repayments {where}", params
    )
    return int(total or 0)


async def list_supplier_repayments_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> list[dict[str, Any]]:
    where, params = _supplier_repayment_where(
        from_iso=from_iso, to_iso=to_iso
    )
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_SUPPLIER_REPAYMENT_COLS} FROM supplier_repayments {where} "
        "ORDER BY repayment_date DESC, id ASC LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# Aggregate sums ``received_amount`` - the actual cash received in
# the period. ``amount`` is the obligation ceiling and doesn't
# change with cash events; using it would mislead a finance person
# looking at the cash-flow story.
_SUPPLIER_REPAYMENT_AGG_SQL = """
SELECT
    COUNT(*) AS repayment_count,
    COALESCE(SUM(received_amount), 0) AS total_received
FROM supplier_repayments
WHERE repayment_date >= :from_iso
  AND repayment_date <= :to_iso
"""


async def fetch_supplier_repayments_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    row = await uow.first_row(
        _SUPPLIER_REPAYMENT_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"repayment_count": 0, "total_received": 0.0}
    return {
        "repayment_count": int(row.get("repayment_count") or 0),
        "total_received": float(row.get("total_received") or 0),
    }


# ---------------------------------------------------------------------------
# F.3 - Cash-flow report
#
# ``cash_movements`` is append-only (no UPDATE/DELETE). The
# ``ck_cash_movements_in_pos`` / ``ck_cash_movements_out_neg``
# constraints enforce that ``in`` rows have positive amount and
# ``out`` rows have negative amount - so ``SUM(amount)`` directly
# equals the period's net cash effect.
# ---------------------------------------------------------------------------

_CASH_FLOW_COLS = (
    "id, movement_date, amount, direction, trigger, "
    "payment_method_id, reference_type, reference_id, "
    "created_at, created_by"
)


def _cash_flow_where(
    *, from_iso: datetime | None, to_iso: datetime | None
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if from_iso is not None:
        clauses.append("movement_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("movement_date <= :to_iso")
        params["to_iso"] = to_iso
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


async def count_cash_flow_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> int:
    where, params = _cash_flow_where(from_iso=from_iso, to_iso=to_iso)
    total = await uow.scalar(
        f"SELECT COUNT(*) FROM cash_movements {where}", params
    )
    return int(total or 0)


async def list_cash_flow_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime | None,
    to_iso: datetime | None,
) -> list[dict[str, Any]]:
    where, params = _cash_flow_where(from_iso=from_iso, to_iso=to_iso)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_CASH_FLOW_COLS} FROM cash_movements {where} "
        "ORDER BY movement_date DESC, id ASC LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# Cash-flow aggregate. The DB constraints ensure ``amount`` is
# positive for ``direction='in'`` and negative for ``direction='out'``,
# so ``SUM(amount)`` equals the period's net cash effect (in + out).
# We break this into cash_in / cash_out / net so the comparison
# envelope can show all three as separate metric keys.
_CASH_FLOW_AGG_SQL = """
SELECT
    COUNT(*) AS movement_count,
    COALESCE(SUM(amount) FILTER (WHERE direction = 'in'), 0) AS cash_in,
    COALESCE(SUM(amount) FILTER (WHERE direction = 'out'), 0) AS cash_out,
    COALESCE(SUM(amount), 0) AS net
FROM cash_movements
WHERE movement_date >= :from_iso
  AND movement_date <= :to_iso
"""


async def fetch_cash_flow_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    row = await uow.first_row(
        _CASH_FLOW_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {
            "movement_count": 0,
            "cash_in": 0.0,
            "cash_out": 0.0,
            "net": 0.0,
        }
    return {
        "movement_count": int(row.get("movement_count") or 0),
        "cash_in": float(row.get("cash_in") or 0),
        "cash_out": float(row.get("cash_out") or 0),
        "net": float(row.get("net") or 0),
    }


# ===========================================================================
# F.4 - Profit & Loss + Receivables + Payables + Supplier Receivables +
# Customer Refund Liabilities
#
# All five endpoints are derived from the same underlying ledgers
# (sales, sale_payments, sales_returns, refunds, purchases,
# purchase_payments, purchase_returns, supplier_repayments,
# manual_finance_entries, financial_categories) per the authoritative
# accounting model in V1.9-Accounting-Event-Matrix.md. The list
# endpoints surface one row per source document with a computed
# ``open_balance``; the comparison aggregates the open_balance (or,
# for P&L, the headline metrics) over the period.
#
# Date semantics: each list endpoint filters on the period of the
# *originating* document (sales.sale_date / purchases.purchase_date /
# supplier_repayments.repayment_date). The P&L is a window aggregate
# over the same ledgers using the same date columns as F.1 sales +
# F.3 manual-income / manual-expense (the canonical D-8 derivation
# is preserved).
#
# Lifecycle: only posted, non-cancelled sales/purchases are surfaced.
# Cancelled manual finance entries are excluded from the P&L
# aggregate to avoid double-counting the reversing cash_movement
# (per F.3 decision). Returns are posted-only; cancelled returns are
# excluded from every aggregate and from the open_balance numerator.
# ===========================================================================


# ---------------------------------------------------------------------------
# Receivables per sale (open AR)
#
# Per the V1.9 model, the AR balance for a sale is the customer-side
# receivable. The cleanest per-sale AR is:
#
#   AR_sale = sales.total_amount - Σ(sale_payments.amount)
#
# Sales returns on an *unpaid* sale reduce AR (Event 8: return on
# unpaid sale credits AR up to outstanding balance); sales returns
# on a *paid* sale create CRL (a separate liability surfaced by the
# CRL report). Refunds are CRL outflows, not AR.
#
# For F.4 v1, the formula is the simple GL balance: open_balance =
# total - paid. Only posted, non-cancelled sales are listed; only
# sales with open_balance > 0 are surfaced.
# ---------------------------------------------------------------------------

_RECEIVABLES_SQL = """
SELECT
    s.id                  AS sale_id,
    s.reference_no,
    s.customer_id,
    s.sale_date,
    s.total_amount        AS total_invoiced,
    COALESCE((
        SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.sale_id = s.id
    ), 0)                 AS total_paid,
    GREATEST(
        s.total_amount - COALESCE((
            SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.sale_id = s.id
        ), 0),
        0
    )                     AS open_balance
FROM sales s
WHERE s.lifecycle_status <> 'cancelled'
  AND s.posted_at IS NOT NULL
  AND s.sale_date >= :from_iso
  AND s.sale_date <= :to_iso
ORDER BY s.sale_date DESC, s.id ASC
LIMIT :limit OFFSET :offset
"""


async def list_receivables_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime,
    to_iso: datetime,
) -> list[dict[str, Any]]:
    offset = (page - 1) * per_page
    rows = await uow.fetch_all(
        _RECEIVABLES_SQL,
        {"from_iso": from_iso, "to_iso": to_iso, "limit": per_page, "offset": offset},
    )
    # Filter to only sales with open_balance > 0 (the GL definition of
    # a live receivable - fully paid sales don't appear).
    return [r for r in rows if float(r.get("open_balance") or 0) > 0]


async def count_receivables_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> int:
    """Count of posted, non-cancelled sales in the period with
    open_balance > 0 (the live receivables)."""
    sql = """
SELECT COUNT(*) FROM (
    SELECT
        GREATEST(
            s.total_amount - COALESCE((
                SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.sale_id = s.id
            ), 0),
            0
        ) AS open_balance
    FROM sales s
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
) t
WHERE t.open_balance > 0
"""
    total = await uow.scalar(
        sql, {"from_iso": from_iso, "to_iso": to_iso}
    )
    return int(total or 0)


_RECEIVABLES_AGG_SQL = """
SELECT
    COUNT(*) AS receivable_count,
    COALESCE(SUM(GREATEST(
        s.total_amount - COALESCE((
            SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.sale_id = s.id
        ), 0),
        0
    )), 0) AS total_open
FROM sales s
WHERE s.lifecycle_status <> 'cancelled'
  AND s.posted_at IS NOT NULL
  AND s.sale_date >= :from_iso
  AND s.sale_date <= :to_iso
"""


async def fetch_receivables_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    """Return ``{receivable_count, total_open}`` for the comparison block."""
    row = await uow.first_row(
        _RECEIVABLES_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"receivable_count": 0, "total_open": 0.0}
    return {
        "receivable_count": int(row.get("receivable_count") or 0),
        "total_open": float(row.get("total_open") or 0),
    }


# ---------------------------------------------------------------------------
# Payables per purchase (open AP)
#
# Per the V1.9 model, the AP balance for a purchase is the supplier-side
# payable. Per-purchase total = SUM(purchase_lines.line_total) +
# purchase_shipping.amount (per schema.sql §7.2 cross-row invariant).
# The cleanest per-purchase AP is:
#
#   AP_purchase = total - Σ(purchase_payments.amount)
#
# Purchase returns on an *unpaid* purchase reduce AP (Event 15);
# purchase returns on a *paid* purchase create Supplier Receivable
# (a separate asset surfaced by the supplier-receivables report).
# Supplier repayments are a separate Supplier Receivable flow.
#
# Only posted, non-cancelled purchases are listed; only purchases with
# open_balance > 0 are surfaced.
# ---------------------------------------------------------------------------

_PAYABLES_SQL = """
SELECT
    p.id                  AS purchase_id,
    p.reference_no,
    p.supplier_id,
    p.purchase_date,
    COALESCE((
        SELECT SUM(pl.line_total) FROM purchase_lines pl WHERE pl.purchase_id = p.id
    ), 0)
    + COALESCE((
        SELECT ps.amount FROM purchase_shipping ps WHERE ps.purchase_id = p.id
    ), 0)                 AS total_invoiced,
    COALESCE((
        SELECT SUM(pp.amount) FROM purchase_payments pp WHERE pp.purchase_id = p.id
    ), 0)                 AS total_paid,
    GREATEST(
        COALESCE((
            SELECT SUM(pl.line_total) FROM purchase_lines pl WHERE pl.purchase_id = p.id
        ), 0)
        + COALESCE((
            SELECT ps.amount FROM purchase_shipping ps WHERE ps.purchase_id = p.id
        ), 0)
        - COALESCE((
            SELECT SUM(pp.amount) FROM purchase_payments pp WHERE pp.purchase_id = p.id
        ), 0),
        0
    )                     AS open_balance
FROM purchases p
WHERE p.lifecycle_status <> 'cancelled'
  AND p.posted_at IS NOT NULL
  AND p.purchase_date >= :from_iso
  AND p.purchase_date <= :to_iso
ORDER BY p.purchase_date DESC, p.id ASC
LIMIT :limit OFFSET :offset
"""


async def list_payables_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime,
    to_iso: datetime,
) -> list[dict[str, Any]]:
    offset = (page - 1) * per_page
    rows = await uow.fetch_all(
        _PAYABLES_SQL,
        {"from_iso": from_iso, "to_iso": to_iso, "limit": per_page, "offset": offset},
    )
    return [r for r in rows if float(r.get("open_balance") or 0) > 0]


async def count_payables_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> int:
    """Count of posted, non-cancelled purchases in the period with
    open_balance > 0 (the live payables)."""
    sql = """
SELECT COUNT(*) FROM (
    SELECT
        GREATEST(
            COALESCE((
                SELECT SUM(pl.line_total) FROM purchase_lines pl WHERE pl.purchase_id = p.id
            ), 0)
            + COALESCE((
                SELECT ps.amount FROM purchase_shipping ps WHERE ps.purchase_id = p.id
            ), 0)
            - COALESCE((
                SELECT SUM(pp.amount) FROM purchase_payments pp WHERE pp.purchase_id = p.id
            ), 0),
            0
        ) AS open_balance
    FROM purchases p
    WHERE p.lifecycle_status <> 'cancelled'
      AND p.posted_at IS NOT NULL
      AND p.purchase_date >= :from_iso
      AND p.purchase_date <= :to_iso
) t
WHERE t.open_balance > 0
"""
    total = await uow.scalar(
        sql, {"from_iso": from_iso, "to_iso": to_iso}
    )
    return int(total or 0)


_PAYABLES_AGG_SQL = """
SELECT
    COUNT(*) AS payable_count,
    COALESCE(SUM(GREATEST(
        COALESCE((
            SELECT SUM(pl.line_total) FROM purchase_lines pl WHERE pl.purchase_id = p.id
        ), 0)
        + COALESCE((
            SELECT ps.amount FROM purchase_shipping ps WHERE ps.purchase_id = p.id
        ), 0)
        - COALESCE((
            SELECT SUM(pp.amount) FROM purchase_payments pp WHERE pp.purchase_id = p.id
        ), 0),
        0
    )), 0) AS total_open
FROM purchases p
WHERE p.lifecycle_status <> 'cancelled'
  AND p.posted_at IS NOT NULL
  AND p.purchase_date >= :from_iso
  AND p.purchase_date <= :to_iso
"""


async def fetch_payables_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    """Return ``{payable_count, total_open}`` for the comparison block."""
    row = await uow.first_row(
        _PAYABLES_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"payable_count": 0, "total_open": 0.0}
    return {
        "payable_count": int(row.get("payable_count") or 0),
        "total_open": float(row.get("total_open") or 0),
    }


# ---------------------------------------------------------------------------
# Supplier Receivables per purchase
#
# The supplier-receivables report surfaces open Supplier Receivable
# obligations. Per V1.9, Supplier Receivable is created when a paid
# purchase is cancelled/returned (Event 12, Event 15 paid branch).
# The ``supplier_repayments`` table is the authoritative ledger for
# this obligation: ``amount`` is the ceiling (what the supplier owes
# the business) and ``received_amount`` is the cumulative cash
# recovered. The open balance is ``amount - received_amount``.
#
# The list surfaces one row per ``supplier_repayments`` entry whose
# period fell in the window and whose open_balance > 0.
# ---------------------------------------------------------------------------

_SUPPLIER_RECEIVABLES_SQL = """
SELECT
    sr.id                  AS supplier_repayment_id,
    sr.purchase_id,
    p.reference_no         AS purchase_reference_no,
    p.supplier_id,
    sr.amount              AS obligation,
    sr.received_amount     AS received,
    GREATEST(sr.amount - sr.received_amount, 0) AS open_balance,
    sr.payment_method_id,
    sr.repayment_date,
    sr.reason,
    sr.created_at
FROM supplier_repayments sr
JOIN purchases p ON p.id = sr.purchase_id
WHERE sr.repayment_date >= :from_iso
  AND sr.repayment_date <= :to_iso
ORDER BY sr.repayment_date DESC, sr.id ASC
LIMIT :limit OFFSET :offset
"""


async def list_supplier_receivables_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime,
    to_iso: datetime,
) -> list[dict[str, Any]]:
    offset = (page - 1) * per_page
    rows = await uow.fetch_all(
        _SUPPLIER_RECEIVABLES_SQL,
        {"from_iso": from_iso, "to_iso": to_iso, "limit": per_page, "offset": offset},
    )
    return [r for r in rows if float(r.get("open_balance") or 0) > 0]


async def count_supplier_receivables_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> int:
    sql = """
SELECT COUNT(*) FROM (
    SELECT GREATEST(sr.amount - sr.received_amount, 0) AS open_balance
    FROM supplier_repayments sr
    WHERE sr.repayment_date >= :from_iso
      AND sr.repayment_date <= :to_iso
) t
WHERE t.open_balance > 0
"""
    total = await uow.scalar(
        sql, {"from_iso": from_iso, "to_iso": to_iso}
    )
    return int(total or 0)


_SUPPLIER_RECEIVABLES_AGG_SQL = """
SELECT
    COUNT(*) AS supplier_receivable_count,
    COALESCE(SUM(GREATEST(sr.amount - sr.received_amount, 0)), 0) AS total_open
FROM supplier_repayments sr
WHERE sr.repayment_date >= :from_iso
  AND sr.repayment_date <= :to_iso
"""


async def fetch_supplier_receivables_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    row = await uow.first_row(
        _SUPPLIER_RECEIVABLES_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"supplier_receivable_count": 0, "total_open": 0.0}
    return {
        "supplier_receivable_count": int(
            row.get("supplier_receivable_count") or 0
        ),
        "total_open": float(row.get("total_open") or 0),
    }


# ---------------------------------------------------------------------------
# Customer Refund Liabilities per sale
#
# Per the V1.9 model, CRL is created when a paid sale is cancelled or
# returned (Event 5, Event 6, Event 8 paid branch). The cleanest
# per-sale CRL is:
#
#   CRL_sale = MAX(0, paid - Σ(refunds.amount))
#
# Sales returns on a *paid* sale generate CRL (Event 8); sales
# returns on an *unpaid* sale reduce AR (not CRL). Refunds are CRL
# outflows. The ``CRL = paid - refunds`` formulation is the
# authoritative GL balance (the sales-returns effect on CRL cancels
# with their effect on AR for the same sale).
#
# Only posted, non-cancelled sales are listed; only sales with
# open_crl > 0 are surfaced.
# ---------------------------------------------------------------------------

_CRL_SQL = """
SELECT
    s.id                  AS sale_id,
    s.reference_no,
    s.customer_id,
    s.sale_date,
    s.total_amount        AS total_invoiced,
    COALESCE((
        SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.sale_id = s.id
    ), 0)                 AS total_paid,
    COALESCE((
        SELECT SUM(r.amount) FROM refunds r WHERE r.sale_id = s.id
    ), 0)                 AS total_refunded,
    GREATEST(
        COALESCE((
            SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.sale_id = s.id
        ), 0)
        - COALESCE((
            SELECT SUM(r.amount) FROM refunds r WHERE r.sale_id = s.id
        ), 0),
        0
    )                     AS open_crl
FROM sales s
WHERE s.lifecycle_status <> 'cancelled'
  AND s.posted_at IS NOT NULL
  AND s.sale_date >= :from_iso
  AND s.sale_date <= :to_iso
ORDER BY s.sale_date DESC, s.id ASC
LIMIT :limit OFFSET :offset
"""


async def list_crl_report(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    from_iso: datetime,
    to_iso: datetime,
) -> list[dict[str, Any]]:
    offset = (page - 1) * per_page
    rows = await uow.fetch_all(
        _CRL_SQL,
        {"from_iso": from_iso, "to_iso": to_iso, "limit": per_page, "offset": offset},
    )
    return [r for r in rows if float(r.get("open_crl") or 0) > 0]


async def count_crl_report(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> int:
    """Count of posted, non-cancelled sales in the period with
    open_crl > 0 (the live CRL obligations)."""
    sql = """
SELECT COUNT(*) FROM (
    SELECT
        GREATEST(
            COALESCE((
                SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.sale_id = s.id
            ), 0)
            - COALESCE((
                SELECT SUM(r.amount) FROM refunds r WHERE r.sale_id = s.id
            ), 0),
            0
        ) AS open_crl
    FROM sales s
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
) t
WHERE t.open_crl > 0
"""
    total = await uow.scalar(
        sql, {"from_iso": from_iso, "to_iso": to_iso}
    )
    return int(total or 0)


_CRL_AGG_SQL = """
SELECT
    COUNT(*) AS crl_count,
    COALESCE(SUM(GREATEST(
        COALESCE((
            SELECT SUM(sp.amount) FROM sale_payments sp WHERE sp.sale_id = s.id
        ), 0)
        - COALESCE((
            SELECT SUM(r.amount) FROM refunds r WHERE r.sale_id = s.id
        ), 0),
        0
    )), 0) AS total_open
FROM sales s
WHERE s.lifecycle_status <> 'cancelled'
  AND s.posted_at IS NOT NULL
  AND s.sale_date >= :from_iso
  AND s.sale_date <= :to_iso
"""


async def fetch_crl_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    """Return ``{crl_count, total_open}`` for the comparison block."""
    row = await uow.first_row(
        _CRL_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {"crl_count": 0, "total_open": 0.0}
    return {
        "crl_count": int(row.get("crl_count") or 0),
        "total_open": float(row.get("total_open") or 0),
    }


# ---------------------------------------------------------------------------
# P&L aggregate (the only F.4 endpoint without a per-document list)
#
# Per ``openapi.yaml:9076-9095`` the P&L response is:
#
#   data: { revenue, cogs, gross_profit, other_income,
#           operating_expenses, net_profit }
#   comparison: { previous_period, delta, delta_pct }  # when compare_to
#
# Revenue/COGS/Gross: reuses the D-8 derivation from F.1 sales
# (Σ sales.total_amount - Σ posted sales_returns.selling; symmetric
# for COGS using sale_lines.cogs_total_snapshot). Other income and
# operating expenses are Σ manual_finance_entries.amount (lifecycle
# = 'posted' to avoid double-counting the cancelling reversing
# cash_movement per F.3 decision) joined to financial_categories
# by entry_type.
# ---------------------------------------------------------------------------

_PNL_AGG_SQL = """
WITH sales_net AS (
    SELECT
        COALESCE(SUM(b.total_amount), 0) AS gross_revenue,
        COALESCE(SUM(b.cogs_total), 0)   AS gross_cogs
    FROM (
        SELECT
            s.id,
            s.total_amount,
            COALESCE((
                SELECT SUM(sl.cogs_total_snapshot)
                FROM sale_lines sl
                WHERE sl.sale_id = s.id
            ), 0) AS cogs_total
        FROM sales s
        WHERE s.lifecycle_status <> 'cancelled'
          AND s.posted_at IS NOT NULL
          AND s.sale_date >= :from_iso
          AND s.sale_date <= :to_iso
    ) b
),
returns AS (
    SELECT
        COALESCE(SUM(sr.total_selling_price_returned) FILTER (
            WHERE sr.lifecycle_status = 'posted'
        ), 0) AS selling_returned,
        COALESCE(SUM(sr.total_cost_returned) FILTER (
            WHERE sr.lifecycle_status = 'posted'
        ), 0) AS cost_returned
    FROM sales_returns sr
    JOIN sales s ON s.id = sr.sale_id
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
),
manual_income AS (
    SELECT COALESCE(SUM(mfe.amount), 0) AS total
    FROM manual_finance_entries mfe
    JOIN financial_categories fc ON fc.id = mfe.category_id
    WHERE fc.entry_type = 'income'
      AND mfe.lifecycle_status = 'posted'
      AND mfe.entry_date >= :from_iso
      AND mfe.entry_date <= :to_iso
),
manual_expense AS (
    SELECT COALESCE(SUM(mfe.amount), 0) AS total
    FROM manual_finance_entries mfe
    JOIN financial_categories fc ON fc.id = mfe.category_id
    WHERE fc.entry_type = 'expense'
      AND mfe.lifecycle_status = 'posted'
      AND mfe.entry_date >= :from_iso
      AND mfe.entry_date <= :to_iso
)
SELECT
    GREATEST(sn.gross_revenue - COALESCE(r.selling_returned, 0), 0) AS revenue,
    GREATEST(sn.gross_cogs    - COALESCE(r.cost_returned, 0),    0) AS cogs,
    GREATEST(
        sn.gross_revenue - COALESCE(r.selling_returned, 0)
        - (sn.gross_cogs - COALESCE(r.cost_returned, 0)),
        0
    )                                                             AS gross_profit,
    mi.total                                                      AS other_income,
    me.total                                                      AS operating_expenses
FROM sales_net sn,
     returns r,
     manual_income mi,
     manual_expense me
"""


async def fetch_pnl_aggregates(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    """Return the six P&L headline metrics for the window.

    Mirrors the ``ProfitLossResponse.data`` shape from
    ``openapi.yaml:9078-9095`` exactly:

    ``{revenue, cogs, gross_profit, other_income,
       operating_expenses, net_profit}``

    ``net_profit`` is computed at the service layer as
    ``gross_profit + other_income - operating_expenses``
    (the SQL returns the five components; net is derived).
    """
    row = await uow.first_row(
        _PNL_AGG_SQL,
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    if row is None:
        return {
            "revenue": 0.0,
            "cogs": 0.0,
            "gross_profit": 0.0,
            "other_income": 0.0,
            "operating_expenses": 0.0,
            "net_profit": 0.0,
        }
    revenue = float(row.get("revenue") or 0)
    cogs = float(row.get("cogs") or 0)
    gross_profit = float(row.get("gross_profit") or 0)
    other_income = float(row.get("other_income") or 0)
    operating_expenses = float(row.get("operating_expenses") or 0)
    return {
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "other_income": other_income,
        "operating_expenses": operating_expenses,
        "net_profit": gross_profit + other_income - operating_expenses,
    }
