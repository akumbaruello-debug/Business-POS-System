#!/usr/bin/env python3
"""Extended demo seed — fills every domain seed_demo_comprehensive.py leaves empty.

Run: cd backend && .venv/Scripts/python.exe scripts/seed_demo_extended.py

Drives the app's own services (SortingService, ProductionRunService, SaleService,
PurchaseService, RefundService, SupplierRepaymentService, ProductAttributeService,
UnitService) so business rules, ledger invariants, audit_log and
product_price_history are maintained by the application rather than hand-written SQL.

Idempotent: each domain skips when its table already has rows.
"""

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.audit.service import AuditContext
from app.db import UnitOfWork, init_db

ACTOR = 1  # owner
D = Decimal
CTX = AuditContext(user_id=ACTOR)  # real audit rows for every seeded operation
results: list[tuple[str, str]] = []


def ik() -> str:
    """Fresh Idempotency-Key per call — several services require one."""
    return str(uuid.uuid4())


def note(domain: str, msg: str) -> None:
    results.append((domain, msg))
    print(f"[{domain}] {msg}", flush=True)


async def count(table: str) -> int:
    async with UnitOfWork() as uow:
        return int(await uow.scalar(f"SELECT count(*) FROM {table}") or 0)


# ---------------------------------------------------------------- 1. units
async def seed_units() -> None:
    from app.services.units import UnitService

    async with UnitOfWork() as uow:
        # kg is the base mass unit: dimension + factor 1 so it satisfies the
        # "mass unit with conversion factor" rule that sorting outputs require.
        await uow.execute(
            "UPDATE units SET dimension='mass', factor_to_base=COALESCE(factor_to_base,1) "
            "WHERE code='kg'"
        )
        kg = await uow.first_row("SELECT id FROM units WHERE code='kg'")
        existing = {str(r["code"]) for r in await uow.fetch_all("SELECT code FROM units")}
        await uow.commit()
    kg_id = int(kg["id"])
    wanted = [
        ("karung", "Karung (50 kg)", 50.0),
        ("ton", "Ton (1000 kg)", 1000.0),
        ("keranjang", "Keranjang (25 kg)", 25.0),
    ]
    made = 0
    for code, name, factor in wanted:
        if code in existing:
            continue
        try:
            async with UnitOfWork() as uow:
                await UnitService(uow).create(
                    code=code, name=name, is_active=True, dimension="mass",
                    base_unit_id=kg_id, factor_to_base=factor, ctx=CTX,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("units", f"{code}: {type(exc).__name__}: {str(exc)[:120]}")
    note("units", f"kg configured as mass base; mass units created: {made}")


# --------------------------------------------------- 2. product attributes
async def seed_product_attributes() -> None:
    from app.services.product_attributes import ProductAttributeService

    if await count("product_attributes") > 0:
        note("product_attributes", "already seeded, skip")
        return
    values = [
        ("jenis", "Ikan Segar"), ("jenis", "Ikan Beku"), ("jenis", "Olahan Ikan"),
        ("jenis", "Udang & Crustacea"), ("jenis", "Cumi & Moluska"),
        ("size", "Kecil"), ("size", "Sedang"), ("size", "Besar"), ("size", "Jumbo"),
        ("kualitas", "Grade A"), ("kualitas", "Grade B"), ("kualitas", "Grade C"),
    ]
    made = 0
    for kind, name in values:
        try:
            async with UnitOfWork() as uow:
                await ProductAttributeService(uow).create(
                    kind=kind, name=name, is_active=True, ctx=CTX,
                    idempotency_key=ik(), request_body=None,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("product_attributes", f"{kind}/{name}: {type(exc).__name__}: {str(exc)[:100]}")
    note("product_attributes", f"attribute values created: {made}")


# ------------------------------------------------ 3. purchase cost categories
async def seed_purchase_cost_categories() -> None:
    if await count("purchase_cost_categories") > 0:
        note("purchase_cost_categories", "already seeded, skip")
        return
    # capitalizable -> financial_category_id NULL; expense -> must be set
    rows = [
        ("Freight In", True, None),
        ("Insurance", True, None),
        ("Cold-chain Handling", False, 8),
        ("Port & Clearance Fees", False, 10),
    ]
    async with UnitOfWork() as uow:
        for name, cap, fin in rows:
            await uow.execute(
                """INSERT INTO purchase_cost_categories
                   (name,capitalizable,financial_category_id,is_active,created_by,updated_by)
                   VALUES (:n,:c,:f,TRUE,:u,:u)""",
                {"n": name, "c": cap, "f": fin, "u": ACTOR},
            )
        await uow.commit()
    note("purchase_cost_categories", f"created {len(rows)}")


# ------------------------------------------------------- 4. purchase costs
async def seed_purchase_costs() -> None:
    """Costs attach only to DRAFT purchases, then the purchase is posted."""
    from app.services.purchases import PurchaseService

    if await count("purchase_costs") > 0:
        note("purchase_costs", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        cats = await uow.fetch_all(
            "SELECT id,name,capitalizable,financial_category_id FROM purchase_cost_categories WHERE is_active ORDER BY id"
        )
        prods = await uow.fetch_all(
            """SELECT id,unit_id,purchase_price FROM products
               WHERE is_purchasable AND is_active ORDER BY id LIMIT 30"""
        )
        supplier = await uow.first_row("SELECT id FROM contacts WHERE type='supplier' ORDER BY id")
    if not cats or len(prods) < 3:
        note("purchase_costs", "no categories / purchasable products, skip")
        return
    cap_cat = next((c for c in cats if c["capitalizable"]), None)
    exp_cat = next((c for c in cats if not c["capitalizable"]), None)

    made = 0
    for i in range(6):
        picked = [prods[(i * 3 + k) % len(prods)] for k in range(3)]
        lines = [
            {"product_id": int(p["id"]), "receipt_type": "classified",
             "unit_id": int(p["unit_id"]) if p["unit_id"] else None,
             "quantity": float(D("12") + D(str(i * 2 + k))),
             "unit_price": float(D(str(p["purchase_price"])))}
            for k, p in enumerate(picked)
        ]
        try:
            async with UnitOfWork() as uow:
                svc = PurchaseService(uow)
                created = await svc.create_draft(
                    principal_user_id=ACTOR,
                    supplier_id=int(supplier["id"]) if supplier else None,
                    purchase_date=datetime.now(timezone.utc) - timedelta(days=20 - i * 2),
                    notes="Pembelian dengan biaya akuisisi (demo)",
                    reference_no=f"DEMO-COST-{i + 1:03d}",
                    lines=lines, shipping=None,
                    idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
                pch = created[0] if isinstance(created, tuple) else created
                pid = int(pch["id"])

            plan = []
            if cap_cat:
                plan.append((int(cap_cat["id"]), D("210000") + D(str(i * 25000))))
            if exp_cat and i % 2 == 0:
                plan.append((int(exp_cat["id"]), D("135000")))
            for cat_id, amount in plan:
                async with UnitOfWork() as uow:
                    await uow.execute(
                        """INSERT INTO purchase_costs(purchase_id,category_id,amount,
                             capitalizable_snapshot,financial_category_id_snapshot,
                             allocation_method,description,created_by)
                           SELECT :pid,c.id,:amount,c.capitalizable,c.financial_category_id,
                                  'line_value',:d,:uid
                           FROM purchase_cost_categories c WHERE c.id=:cid""",
                        {"pid": pid, "cid": cat_id, "amount": amount,
                         "d": "Biaya akuisisi demo", "uid": ACTOR},
                    )
                    await PurchaseService(uow)._recompute_purchase_cost_allocations(pid)
                    await uow.commit()

            async with UnitOfWork() as uow:
                await PurchaseService(uow).post_purchase(
                    pid, principal_user_id=ACTOR, if_match=None,
                    idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("purchase_costs", f"purchase {i}: {type(exc).__name__}: {str(exc)[:150]}")
    note("purchase_costs", f"purchases with costs posted: {made}")


# ------------------------------------------------------- 5. sales returns
async def seed_sales_returns() -> None:
    from app.services.sales import SaleService

    if await count("sales_returns") > 0:
        note("sales_returns", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        sales = await uow.fetch_all(
            """SELECT s.id,
                      (SELECT l.id FROM sale_lines l WHERE l.sale_id=s.id
                        AND l.quantity > 0.5 ORDER BY l.id LIMIT 1) AS line_id,
                      (SELECT l.quantity FROM sale_lines l WHERE l.sale_id=s.id
                        AND l.quantity > 0.5 ORDER BY l.id LIMIT 1) AS qty
               FROM sales s
               WHERE s.lifecycle_status IN ('posted','completed')
               ORDER BY s.id LIMIT 200"""
        )
    cands = [s for s in sales if s["line_id"] and s["qty"] and D(str(s["qty"])) > 0.5]
    made = 0
    for s in cands[:14]:
        qty = (D(str(s["qty"])) * D("0.25")).quantize(D("0.0001")) or D("0.25")
        try:
            async with UnitOfWork() as uow:
                await SaleService(uow).create_return(
                    int(s["id"]), principal_user_id=ACTOR,
                    reason="Barang tidak sesuai pesanan (demo)",
                    return_lines=[{"sale_line_id": int(s["line_id"]), "quantity": float(qty)}],
                    if_match=None, idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("sales_returns", f"sale {s['id']}: {type(exc).__name__}: {str(exc)[:110]}")
    note("sales_returns", f"returns created: {made}")


# ------------------------------------------------------------- 6. refunds
async def seed_refunds() -> None:
    from app.services.refunds import RefundService

    if await count("refunds") > 0:
        note("refunds", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        sales = await uow.fetch_all(
            """SELECT s.id,
                      COALESCE((SELECT sum(sp.amount) FROM sale_payments sp WHERE sp.sale_id=s.id),0)
                      - COALESCE((SELECT sum(r.amount) FROM refunds r WHERE r.sale_id=s.id),0) AS refundable
               FROM sales s
               WHERE s.lifecycle_status IN ('posted','completed')
                 AND EXISTS (SELECT 1 FROM sale_payments sp WHERE sp.sale_id=s.id)
               ORDER BY s.id LIMIT 120"""
        )
    made = 0
    for s in sales:
        ref = D(str(s["refundable"] or 0))
        if ref < D("20000"):
            continue
        amount = (ref * D("0.2")).quantize(D("1"))
        try:
            async with UnitOfWork() as uow:
                await RefundService(uow).create_refund(
                    principal_user_id=ACTOR, sale_id=int(s["id"]), amount=amount,
                    payment_method_id=1, refund_date=None,
                    reason="Kelebihan bayar / komplain pelanggan (demo)",
                    if_match=None, idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
            made += 1
            if made >= 12:
                break
        except Exception as exc:  # noqa: BLE001
            note("refunds", f"sale {s['id']}: {type(exc).__name__}: {str(exc)[:110]}")
    note("refunds", f"refunds created: {made}")


# ---------------------------------------------------- 7. purchase returns
async def seed_purchase_returns() -> None:
    from app.services.purchases import PurchaseService

    if await count("purchase_returns") > 0:
        note("purchase_returns", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        rows = await uow.fetch_all(
            """SELECT p.id,
                      (SELECT l.id FROM purchase_lines l WHERE l.purchase_id=p.id
                        AND l.quantity > 0.5 ORDER BY l.id LIMIT 1) AS line_id,
                      (SELECT l.quantity FROM purchase_lines l WHERE l.purchase_id=p.id
                        AND l.quantity > 0.5 ORDER BY l.id LIMIT 1) AS qty
               FROM purchases p
               WHERE p.lifecycle_status IN ('posted','completed')
               ORDER BY p.id LIMIT 120"""
        )
    cands = [r for r in rows if r["line_id"] and r["qty"] and D(str(r["qty"])) > 0.5]
    made = 0
    for r in cands[:10]:
        qty = (D(str(r["qty"])) * D("0.2")).quantize(D("0.0001")) or D("0.2")
        try:
            async with UnitOfWork() as uow:
                await PurchaseService(uow).create_return(
                    int(r["id"]), principal_user_id=ACTOR,
                    reason="Barang rusak saat pengiriman (demo)",
                    return_lines=[{"purchase_line_id": int(r["line_id"]), "quantity": float(qty)}],
                    if_match=None, idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("purchase_returns", f"purchase {r['id']}: {type(exc).__name__}: {str(exc)[:110]}")
    note("purchase_returns", f"returns created: {made}")


# ------------------------------------------------ 8. supplier repayments
async def seed_supplier_repayments() -> None:
    from app.services.supplier_repayments import SupplierRepaymentService

    if await count("supplier_repayments") > 0:
        note("supplier_repayments", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        rets = await uow.fetch_all(
            """SELECT pr.id, pr.purchase_id, pr.total_value_returned
               FROM purchase_returns pr
               WHERE pr.lifecycle_status IN ('posted','completed')
               ORDER BY pr.id LIMIT 20"""
        )
    made = 0
    for r in rets:
        amount = (D(str(r["total_value_returned"])) * D("0.6")).quantize(D("1"))
        if amount <= 0:
            continue
        try:
            async with UnitOfWork() as uow:
                await SupplierRepaymentService(uow).create_repayment(
                    principal_user_id=ACTOR, purchase_id=int(r["purchase_id"]),
                    purchase_return_id=int(r["id"]), amount=amount,
                    payment_method_id=2, repayment_date=None,
                    reason="Pengembalian dana supplier (demo)",
                    if_match=None, idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("supplier_repayments", f"return {r['id']}: {type(exc).__name__}: {str(exc)[:110]}")
    note("supplier_repayments", f"repayments created: {made}")


# ------------------------------------------------------ 9. purchase shipping
async def seed_purchase_shipping() -> None:
    """Shipping is draft-only, so build draft purchases with shipping, then post."""
    from app.services.purchases import PurchaseService

    if await count("purchase_shipping") > 0:
        note("purchase_shipping", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        prods = await uow.fetch_all(
            "SELECT id,unit_id,purchase_price FROM products WHERE is_purchasable AND is_active ORDER BY id LIMIT 24"
        )
        supplier = await uow.first_row("SELECT id FROM contacts WHERE type='supplier' ORDER BY id")
    if len(prods) < 2:
        note("purchase_shipping", "no purchasable products, skip")
        return
    made = 0
    for i in range(6):
        picked = [prods[(i * 2 + k) % len(prods)] for k in range(2)]
        lines = [
            {"product_id": int(p["id"]), "receipt_type": "classified",
             "unit_id": int(p["unit_id"]) if p["unit_id"] else None,
             "quantity": float(D("15") + D(str(i + k))),
             "unit_price": float(D(str(p["purchase_price"])))}
            for k, p in enumerate(picked)
        ]
        ship = {"amount": float(D("145000") + D(str(i * 20000))),
                "paid_in_cash": (i % 2 == 0),
                "supplier_id": int(supplier["id"]) if supplier else None,
                "description": "Ongkos angkut demo"}
        try:
            async with UnitOfWork() as uow:
                created = await PurchaseService(uow).create_draft(
                    principal_user_id=ACTOR,
                    supplier_id=int(supplier["id"]) if supplier else None,
                    purchase_date=datetime.now(timezone.utc) - timedelta(days=15 - i),
                    notes="Pembelian dengan ongkos kirim (demo)",
                    reference_no=f"DEMO-SHIP-{ik()[:8]}",
                    lines=lines, shipping=ship,
                    idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
                pch = created[0] if isinstance(created, tuple) else created
                pid = int(pch["id"])
            async with UnitOfWork() as uow:
                await PurchaseService(uow).post_purchase(
                    pid, principal_user_id=ACTOR, if_match=None,
                    idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("purchase_shipping", f"purchase {i}: {type(exc).__name__}: {str(exc)[:130]}")
    note("purchase_shipping", f"purchases with shipping posted: {made}")


# ------------------------------------------- 9b. product flags & attributes
async def seed_product_flags() -> None:
    """Link attribute values to products and flag processed goods as producible."""
    from app.services.product_attributes import ProductAttributeService

    async with UnitOfWork() as uow:
        kinds = await uow.fetch_all(
            "SELECT id,kind,name FROM product_attributes WHERE is_active ORDER BY id"
        )
        prods = await uow.fetch_all("SELECT id,code FROM products ORDER BY id")
    by_name = {str(a["name"]): int(a["id"]) for a in kinds}
    for extra in ("Kerang & Moluska", "Lainnya"):
        if extra not in by_name:
            try:
                async with UnitOfWork() as uow:
                    row = await ProductAttributeService(uow).create(
                        kind="jenis", name=extra, is_active=True, ctx=CTX,
                        idempotency_key=ik(), request_body=None,
                    )
                    await uow.commit()
                by_name[extra] = int(row["id"])
            except Exception as exc:  # noqa: BLE001
                note("product_flags", f"jenis {extra}: {type(exc).__name__}: {str(exc)[:90]}")

    jenis_for = {
        "IF": "Ikan Segar", "IB": "Ikan Beku", "OL": "Olahan Ikan",
        "UD": "Udang & Crustacea", "CU": "Cumi & Moluska", "KE": "Kerang & Moluska",
        "LN": "Lainnya",
    }
    sizes = [by_name.get(x) for x in ("Kecil", "Sedang", "Besar", "Jumbo")]
    grades = [by_name.get(x) for x in ("Grade A", "Grade B", "Grade C")]
    sizes = [s for s in sizes if s]
    grades = [g for g in grades if g]
    linked = producible = partial = 0
    seen: dict[int, int] = {}  # jenis_id -> how many products already took a triple
    async with UnitOfWork() as uow:
        # ux_products_dim_combination is UNIQUE on the full (jenis,size,kualitas)
        # triple, so assign each product a distinct triple and leave the overflow
        # with jenis only (partial triples are exempt from the index).
        await uow.execute("UPDATE products SET jenis_id=NULL, size_id=NULL, kualitas_id=NULL")
        for p in prods:
            code = str(p["code"])
            prefix = code.split("-")[0]
            jenis = by_name.get(jenis_for.get(prefix, ""))
            sets, params = [], {"id": int(p["id"])}
            if jenis:
                sets.append("jenis_id=:jenis"); params["jenis"] = jenis
                n = seen.get(jenis, 0)
                seen[jenis] = n + 1
                if sizes and grades and n < len(sizes) * len(grades):
                    sets.append("size_id=:size"); params["size"] = sizes[n % len(sizes)]
                    sets.append("kualitas_id=:grade")
                    params["grade"] = grades[(n // len(sizes)) % len(grades)]
                else:
                    partial += 1
            if prefix == "OL":
                sets.append("is_producible=TRUE"); producible += 1
            if not sets:
                continue
            await uow.execute(f"UPDATE products SET {', '.join(sets)} WHERE id=:id", params)
            linked += 1
        await uow.commit()
    note("product_flags",
         f"linked {linked} products to attributes; producible={producible}; jenis-only={partial}")


# ------------------------------------------------------- 10. production runs
async def seed_production() -> None:
    from app.production.service import ProductionRunService

    if await count("production_runs") > 0:
        note("production", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        rows = await uow.fetch_all(
            """SELECT ps.product_id, ps.on_hand_quantity AS quantity
               FROM product_stock ps WHERE ps.on_hand_quantity > 20
               ORDER BY ps.on_hand_quantity DESC LIMIT 40"""
        )
        outs = await uow.fetch_all(
            "SELECT id,name FROM products WHERE is_sellable AND is_producible AND is_active ORDER BY id"
        )
        costs = await uow.fetch_all("SELECT id FROM cost_types ORDER BY id")
    if len(rows) < 2 or not outs:
        note("production", f"insufficient stock/producible outputs (stock={len(rows)}, outs={len(outs)}), skip")
        return
    stock = [(int(r["product_id"]), D(str(r["quantity"]))) for r in rows]
    cost_ids = [int(c["id"]) for c in costs] or [1]
    made = 0
    for i in range(6):
        inp_a = stock[i % len(stock)]
        inp_b = stock[(i + 7) % len(stock)]
        if inp_a[0] == inp_b[0]:
            continue
        out_prod = next(
            (c for c in [int(o["id"]) for o in outs] if c not in (inp_a[0], inp_b[0])),
            None,
        )
        if out_prod is None:
            continue
        try:
            async with UnitOfWork() as uow:
                svc = ProductionRunService(uow)
                run = await svc.create_draft(
                    principal_user_id=ACTOR,
                    run_date=datetime.now(timezone.utc) - timedelta(days=i * 3),
                    output_product_id=out_prod, output_quantity=D("40") + D(str(i * 5)),
                    notes="Produksi olahan demo",
                    inputs=[{"product_id": inp_a[0], "quantity": float(min(D("6"), inp_a[1] / 4))},
                            {"product_id": inp_b[0], "quantity": float(min(D("4"), inp_b[1] / 4))}],
                    cost_lines=[{"cost_type_id": cost_ids[0], "amount": 250000.0,
                                 "paid_in_cash": True, "description": "Tenaga kerja demo"},
                                {"cost_type_id": cost_ids[-1], "amount": 120000.0,
                                 "paid_in_cash": False, "description": "Overhead demo"}],
                    ctx=CTX, idempotency_key=ik(), request_body=None,
                )
                await uow.commit()
                run_id = int(run["id"]) if isinstance(run, dict) else int(run[0]["id"])
            async with UnitOfWork() as uow:
                await ProductionRunService(uow).post(
                    run_id, principal_user_id=ACTOR, if_match=None,
                    idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("production", f"run {i}: {type(exc).__name__}: {str(exc)[:130]}")
    note("production", f"runs posted: {made}")


# --------------------------------------------------------- 11. sorting events
async def seed_sorting() -> None:
    from app.inventory.sorting_service import SortingService
    from app.services.purchases import PurchaseService

    if await count("sorting_events") > 0:
        note("sorting", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        unit = await uow.first_row(
            "SELECT id,code FROM units WHERE dimension='mass' AND factor_to_base IS NOT NULL ORDER BY factor_to_base LIMIT 1"
        )
        prods = await uow.fetch_all(
            """SELECT p.id,p.name FROM products p JOIN units u ON u.id=p.unit_id
               WHERE p.is_sellable AND p.is_active
                 AND u.dimension='mass' AND u.factor_to_base IS NOT NULL
               ORDER BY p.id LIMIT 60"""
        )
        supplier = await uow.first_row("SELECT id FROM contacts WHERE type='supplier' ORDER BY id")
        loss_cat = await uow.first_row("SELECT id FROM financial_categories WHERE entry_type='expense' ORDER BY id")
    if not unit or not prods:
        note("sorting", "no mass unit with factor or no products, skip")
        return
    unit_id = int(unit["id"])

    # Reuse any unsorted receipt batches already present (an earlier partial run
    # may have created them), then top up with fresh unsorted purchases.
    async with UnitOfWork() as uow:
        batches = await uow.fetch_all(
            """SELECT b.id, b.original_quantity FROM pending_receipt_batches b
               WHERE NOT EXISTS (SELECT 1 FROM sorting_events e
                                 WHERE e.pending_receipt_batch_id=b.id)
               ORDER BY b.id"""
        )
    made = 0
    idx = 0
    while len(batches) < 5 and idx < 8:
        idx += 1
        try:
            async with UnitOfWork() as uow:
                created = await PurchaseService(uow).create_draft(
                    supplier_id=int(supplier["id"]) if supplier else None,
                    purchase_date=datetime.now(timezone.utc) - timedelta(days=6),
                    notes="Pembelian ikan campur (belum disortir) demo",
                    reference_no=f"DEMO-UNSORT-{ik()[:8]}",
                    lines=[{"product_id": None, "receipt_type": "unsorted", "unit_id": unit_id,
                            "quantity": 4.0, "unit_price": 320000.0}],
                    shipping=None, principal_user_id=ACTOR,
                    idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
                pch = created[0] if isinstance(created, tuple) else created
                purchase_id = int(pch["id"])
            async with UnitOfWork() as uow:
                await PurchaseService(uow).post_purchase(
                    purchase_id, principal_user_id=ACTOR, if_match=None,
                    idempotency_key=ik(), ctx=CTX, request_body=None,
                )
                await uow.commit()
        except Exception as exc:  # noqa: BLE001
            note("sorting", f"unsorted purchase {idx}: {type(exc).__name__}: {str(exc)[:130]}")
            continue
        async with UnitOfWork() as uow:
            fresh = await uow.first_row(
                """SELECT b.id, b.original_quantity FROM pending_receipt_batches b
                   WHERE NOT EXISTS (SELECT 1 FROM sorting_events e
                                     WHERE e.pending_receipt_batch_id=b.id)
                   ORDER BY b.id DESC LIMIT 1"""
            )
        if fresh:
            batches.append(fresh)

    for i, batch in enumerate(batches[:5]):
        try:
            base_qty = D(str(batch["original_quantity"]))
            a = int(prods[(i * 5) % len(prods)]["id"])
            b = int(prods[(i * 5 + 3) % len(prods)]["id"])
            loss = (base_qty * D("0.05")).quantize(D("0.0001"))
            rest = base_qty - loss
            qa = (rest * D("0.65")).quantize(D("0.0001"))
            qb = rest - qa
            async with UnitOfWork() as uow:
                ev = await SortingService(uow).create_draft(
                    {"pending_receipt_batch_id": int(batch["id"]),
                     "input_quantity": float(base_qty), "loss_quantity": float(loss),
                     "remaining_quantity": 0.0,
                     "financial_category_id": int(loss_cat["id"]) if loss_cat else None,
                     "reason": "Sortir mutu ikan demo",
                     "outputs": [{"product_id": a, "quantity": float(qa)},
                                 {"product_id": b, "quantity": float(qb)}]},
                    user_id=ACTOR, ctx=CTX,
                )
                await uow.commit()
                event_id = int(ev["id"])
            async with UnitOfWork() as uow:
                await SortingService(uow).post(
                    event_id, user_id=ACTOR, if_match=None, idempotency_key=ik(),
                    ctx=CTX, request_body=None,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("sorting", f"event {i}: {type(exc).__name__}: {str(exc)[:130]}")
    note("sorting", f"sorting events posted: {made}")


# ------------------------------------------- 12. user capability overrides
async def seed_capability_overrides() -> None:
    if await count("user_capability_overrides") > 0:
        note("user_capability_overrides", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        staff = await uow.first_row(
            "SELECT id FROM users WHERE username='demo_supervisor' OR username<>'owner' ORDER BY id LIMIT 1"
        )
        caps = await uow.fetch_all(
            """SELECT c.id FROM capabilities c
               WHERE NOT EXISTS (
                 SELECT 1 FROM user_capability_overrides o
                 WHERE o.user_id=:u AND o.capability_id=c.id)
               ORDER BY c.id LIMIT 3""",
            {"u": int(staff["id"]) if staff else ACTOR},
        )
        target = int(staff["id"]) if staff else ACTOR
        for i, c in enumerate(caps):
            await uow.execute(
                """INSERT INTO user_capability_overrides
                   (user_id,capability_id,is_granted,granted_by)
                   VALUES (:u,:c,:g,:b) ON CONFLICT DO NOTHING""",
                {"u": target, "c": int(c["id"]), "g": i != 0, "b": ACTOR},
            )
        await uow.commit()
    note("user_capability_overrides", f"overrides created: {len(caps)} for user {target}")


# ------------------------------------------------- 12. product price history
async def seed_price_history() -> None:
    """Real price changes through ProductService (BR-PRODUCT-002 writes history)."""
    from app.concurrency.master_etag import etag_and_version_from_updated_at
    from app.services.products import ProductService

    if await count("product_price_history") > 0:
        note("product_price_history", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        prods = await uow.fetch_all(
            "SELECT id,selling_price,purchase_price,updated_at FROM products WHERE is_active ORDER BY id LIMIT 12"
        )
    made = 0
    for i, p in enumerate(prods):
        bump = D("1.05") if i % 2 == 0 else D("0.97")
        try:
            etag, _ = etag_and_version_from_updated_at(p["updated_at"])
            async with UnitOfWork() as uow:
                await ProductService(uow).update(
                    int(p["id"]),
                    selling_price=float((D(str(p["selling_price"])) * bump).quantize(D("1"))),
                    purchase_price=float((D(str(p["purchase_price"])) * bump).quantize(D("1"))),
                    if_match=etag, ctx=CTX,
                )
                await uow.commit()
            made += 1
        except Exception as exc:  # noqa: BLE001
            note("product_price_history", f"product {p['id']}: {type(exc).__name__}: {str(exc)[:120]}")
    note("product_price_history", f"price changes recorded: {made}")


# ------------------------------------------------ 13. sorting event reversal
async def seed_sorting_reversal() -> None:
    """Reversal requires a posted event with zero physical loss."""
    from app.inventory.sorting_service import SortingService
    from app.services.purchases import PurchaseService

    if await count("sorting_event_reversals") > 0:
        note("sorting_event_reversals", "already seeded, skip")
        return
    async with UnitOfWork() as uow:
        unit = await uow.first_row(
            "SELECT id FROM units WHERE dimension='mass' AND factor_to_base IS NOT NULL "
            "AND factor_to_base > 1 ORDER BY factor_to_base LIMIT 1"
        )
        prods = await uow.fetch_all(
            """SELECT p.id FROM products p JOIN units u ON u.id=p.unit_id
               WHERE p.is_sellable AND p.is_active
                 AND u.dimension='mass' AND u.factor_to_base IS NOT NULL
               ORDER BY p.id LIMIT 20"""
        )
        supplier = await uow.first_row("SELECT id FROM contacts WHERE type='supplier' ORDER BY id")
    if not unit or len(prods) < 2:
        note("sorting_event_reversals", "no mass unit / products, skip")
        return
    try:
        async with UnitOfWork() as uow:
            created = await PurchaseService(uow).create_draft(
                supplier_id=int(supplier["id"]) if supplier else None,
                purchase_date=datetime.now(timezone.utc) - timedelta(days=2),
                notes="Pembelian campur untuk demo pembatalan sortir",
                reference_no=f"DEMO-REV-{ik()[:8]}",
                lines=[{"product_id": None, "receipt_type": "unsorted",
                        "unit_id": int(unit["id"]), "quantity": 2.0, "unit_price": 300000.0}],
                shipping=None, principal_user_id=ACTOR,
                idempotency_key=ik(), ctx=CTX, request_body=None,
            )
            await uow.commit()
            pch = created[0] if isinstance(created, tuple) else created
            pid = int(pch["id"])
        async with UnitOfWork() as uow:
            await PurchaseService(uow).post_purchase(
                pid, principal_user_id=ACTOR, if_match=None,
                idempotency_key=ik(), ctx=CTX, request_body=None,
            )
            await uow.commit()
        async with UnitOfWork() as uow:
            batch = await uow.first_row(
                """SELECT b.id, b.original_quantity FROM pending_receipt_batches b
                   WHERE NOT EXISTS (SELECT 1 FROM sorting_events e
                                     WHERE e.pending_receipt_batch_id=b.id)
                   ORDER BY b.id DESC LIMIT 1"""
            )
        if not batch:
            note("sorting_event_reversals", "no free batch, skip")
            return
        qty = D(str(batch["original_quantity"]))
        half = (qty / 2).quantize(D("0.0001"))
        async with UnitOfWork() as uow:
            ev = await SortingService(uow).create_draft(
                {"pending_receipt_batch_id": int(batch["id"]),
                 "input_quantity": float(qty), "loss_quantity": 0.0,
                 "remaining_quantity": 0.0, "financial_category_id": None,
                 "reason": "Sortir tanpa susut (demo pembatalan)",
                 "outputs": [{"product_id": int(prods[0]["id"]), "quantity": float(half)},
                             {"product_id": int(prods[1]["id"]), "quantity": float(qty - half)}]},
                user_id=ACTOR, ctx=CTX,
            )
            await uow.commit()
            event_id = int(ev["id"])
        async with UnitOfWork() as uow:
            await SortingService(uow).post(
                event_id, user_id=ACTOR, if_match=None, idempotency_key=ik(),
                ctx=CTX, request_body=None,
            )
            await uow.commit()
        async with UnitOfWork() as uow:
            await SortingService(uow).reverse(
                event_id, user_id=ACTOR, reason="Salah pilih produk hasil sortir (demo)",
                if_match=None, ctx=CTX,
            )
            await uow.commit()
        note("sorting_event_reversals", f"reversed event {event_id}")
    except Exception as exc:  # noqa: BLE001
        note("sorting_event_reversals", f"{type(exc).__name__}: {str(exc)[:150]}")


DOMAINS = [
    ("units", seed_units),
    ("product_attributes", seed_product_attributes),
    ("purchase_cost_categories", seed_purchase_cost_categories),
    ("purchase_costs", seed_purchase_costs),
    ("sales_returns", seed_sales_returns),
    ("refunds", seed_refunds),
    ("purchase_returns", seed_purchase_returns),
    ("supplier_repayments", seed_supplier_repayments),
    ("purchase_shipping", seed_purchase_shipping),
    ("product_flags", seed_product_flags),
    ("production", seed_production),
    ("sorting", seed_sorting),
    ("user_capability_overrides", seed_capability_overrides),
    ("product_price_history", seed_price_history),
    ("sorting_event_reversals", seed_sorting_reversal),
]


async def main() -> None:
    only = sys.argv[1:] or None
    await init_db()
    for name, fn in DOMAINS:
        if only and name not in only:
            continue
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001
            note(name, f"DOMAIN FAILED: {type(exc).__name__}: {str(exc)[:200]}")

    print("\n=== EXTENDED SEED SUMMARY ===")
    for t in ("units", "product_attributes", "product_price_history", "purchase_cost_categories",
              "purchase_costs", "purchase_cost_allocations", "sales_returns", "sales_return_lines",
              "refunds", "purchase_returns", "purchase_return_lines", "supplier_repayments",
              "purchase_shipping", "production_runs", "production_inputs", "production_outputs",
              "production_cost_lines", "sorting_events", "sorting_event_outputs",
              "pending_receipt_batches", "pending_inventory_movements",
              "user_capability_overrides", "audit_log"):
        print(f"  {t}: {await count(t)}")
    failures = [r for r in results if "FAILED" in r[1] or "Error" in r[1]]
    print(f"\nfailures reported: {len(failures)}")


if __name__ == "__main__":
    asyncio.run(main())
