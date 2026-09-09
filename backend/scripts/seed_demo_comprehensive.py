#!/usr/bin/env python3
"""Comprehensive demo seed for Business-POS-System (append-only compliant, idempotent).

Run: cd backend && .venv/Scripts/python.exe scripts/seed_demo_comprehensive.py

Respects append-only triggers on stock_movements, sale_lines, cash_movements.
Idempotency is check-and-skip: checks existing demo sales count, seeds only if needed.
Deterministic (seed=42). Stock-aware: sales never exceed on-hand so the
inventory ledger stays non-negative and valuation stays positive.
"""

import asyncio, asyncpg, random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

DB_URL = "postgresql://postgres@127.0.0.1:5433/pos_dev"
random.seed(42)
DEMO = "DEMO"

def D(v): return Decimal(str(v))
def days_ago(n, hour=10):
    return datetime.now(timezone.utc) - timedelta(days=n, hours=10-hour)

# Categories
CATEGORIES = [
    "Ikan Segar", "Ikan Beku", "Olahan Ikan", "Udang & Crustacea",
    "Cumi & Gurita", "Kerang & Moluska", "Lainnya",
]

UNITS = [("kg","Kilogram"),("pcs","Pieces"),("pack","Pack"),("box","Box")]

# (code, name, cat_idx, unit_idx, buy, sell, low_stock, is_sellable, is_purchasable)
PRODUCTS = [
    ("IF-001","Bandeng Segar",0,0,D(28000),D(35000),D(10),True,True),
    ("IF-002","Lele Segar",0,0,D(22000),D(30000),D(15),True,True),
    ("IF-003","Nila Segar",0,0,D(30000),D(38000),D(8),True,True),
    ("IF-004","Gurame Segar",0,0,D(45000),D(58000),D(5),True,True),
    ("IF-005","Patin Segar",0,0,D(26000),D(34000),D(12),True,True),
    ("IF-006","Mas Segar",0,0,D(32000),D(42000),D(6),True,True),
    ("IF-007","Tongkol Segar",0,0,D(35000),D(45000),D(10),True,True),
    ("IF-008","Tenggiri Segar",0,0,D(55000),D(72000),D(4),True,True),
    ("IF-009","Kakap Merah Segar",0,0,D(65000),D(85000),D(3),True,True),
    ("IF-010","Bawal Putih Segar",0,0,D(48000),D(62000),D(5),True,True),
    ("IF-011","Belut Segar",0,0,D(42000),D(55000),D(5),True,True),
    ("IF-012","Sidat Segar",0,0,D(68000),D(90000),D(2),True,True),
    ("IB-001","Bandeng Beku",1,0,D(25000),D(33000),D(20),True,True),
    ("IB-002","Udang Vaname Beku",1,0,D(65000),D(85000),D(10),True,True),
    ("IB-003","Cumi Beku",1,0,D(42000),D(55000),D(8),True,True),
    ("IB-004","Fillet Nila Beku",1,0,D(38000),D(50000),D(12),True,True),
    ("IB-005","Kepiting Beku",1,0,D(85000),D(110000),D(3),True,True),
    ("IB-006","Salmon Fillet Beku",1,0,D(110000),D(145000),D(2),True,True),
    ("IB-007","Dory Fillet Beku",1,0,D(42000),D(56000),D(8),True,True),
    ("OL-001","Abon Ikan Tongkol",2,1,D(18000),D(28000),D(25),True,True),
    ("OL-002","Kerupuk Ikan",2,1,D(12000),D(20000),D(30),True,True),
    ("OL-003","Nugget Ikan",2,1,D(22000),D(32000),D(15),True,True),
    ("OL-004","Bakso Ikan",2,1,D(15000),D(25000),D(20),True,True),
    ("OL-005","Otak-Otak Ikan",2,1,D(14000),D(22000),D(18),True,True),
    ("OL-006","Pempek Ikan",2,1,D(16000),D(26000),D(15),True,True),
    ("OL-007","Sarden Kaleng",2,1,D(20000),D(30000),D(10),True,True),
    ("OL-008","Fish Stick",2,1,D(25000),D(35000),D(10),True,True),
    ("OL-009","Tempura Ikan",2,1,D(28000),D(38000),D(8),True,True),
    ("UD-001","Udang Windu Segar",3,0,D(95000),D(125000),D(3),True,True),
    ("UD-002","Udang Galah Segar",3,0,D(78000),D(100000),D(4),True,True),
    ("UD-003","Lobster Air Tawar",3,0,D(120000),D(160000),D(2),True,True),
    ("UD-004","Udang Rebon Kering",3,0,D(35000),D(48000),D(8),True,True),
    ("CU-001","Cumi Segar",4,0,D(40000),D(52000),D(8),True,True),
    ("CU-002","Gurita Segar",4,0,D(55000),D(72000),D(4),True,True),
    ("CU-003","Sotong Segar",4,0,D(38000),D(50000),D(6),True,True),
    ("KE-001","Kerang Darah",5,0,D(18000),D(26000),D(15),True,True),
    ("KE-002","Kerang Hijau",5,0,D(22000),D(30000),D(10),True,True),
    ("KE-003","Tiram Segar",5,0,D(35000),D(48000),D(5),True,True),
    ("KE-004","Siput Segar",5,0,D(15000),D(22000),D(12),True,True),
    ("LN-001","Rumput Laut Kering",6,0,D(28000),D(40000),D(10),True,True),
    ("LN-002","Teri Medan",6,0,D(45000),D(60000),D(5),True,True),
    ("LN-003","Ebi Kering",6,0,D(55000),D(75000),D(4),True,True),
    ("LN-004","Ikan Asin Jambal",6,0,D(38000),D(52000),D(6),True,True),
    ("LN-005","Petis Udang",6,1,D(20000),D(30000),D(12),True,True),
    ("LN-006","Agar-Agar Rumput Laut",6,1,D(18000),D(28000),D(10),True,True),
    ("IF-013","Mujair Segar",0,0,D(24000),D(33000),D(10),True,True),
    ("IB-008","Tuna Steak Beku",1,0,D(90000),D(125000),D(4),True,True),
]

CUSTOMERS = [
    "PT Sinar Bahari","CV Ocean Fresh","Rumah Makan Padang Jaya","Warung Seafood Pantai",
    "Hotel Grand Surabaya","Restoran Nelayan","Supermarket Segar Jaya","PT Indo Fishery",
    "Koperasi Nelayan Mandiri","Toko Hasil Laut Bu Siti","Warung Makan Keluarga",
    "Catering Nusantara","PT Maritim Abadi","Cold Storage Jakarta","Agen Ikan Hias Bali",
    "Restoran Chinese Food","Hotel Bintang Lima","Pasar Tradisional Blok M","Distributor Frozen Food",
    "Warung Tegal Pak Ujang","Kedai Kopi & Seafood","PT Ekspor Perikanan","CV Aqua Farm",
    "Rumah Makan Ampera","Food Court Mall Kelapa Gading","Supplier Hotel Bandung",
    "Toko Oleh-Oleh Pesisir","Nelayan Group Cilacap","Pabrik Kerupuk Ikan","Gudang Beku Semarang",
    "Warung Nasi Liwet","Kafe Tepi Pantai","Restoran Jepang Sakura","Delivery Seafood Online",
    "Komunitas Nelayan Lamongan","Pengepul Ikan Muara Baru","Frozen Food Agent Bogor",
    "Katering Pernikahan","Hotel Resort Anyer","Toko Kelontong Pantai",
]

SUPPLIERS = [
    "Nelayan Kelompok Mina Jaya","CV Sumber Laut Lestari","PT Perikanan Nusantara",
    "Cold Storage Surya Abadi","Pangkalan Pendaratan Ikan Muara Baru","Koperasi Tani Nelayan",
    "PT Indo Marine Supply","Distributor Ikan Segar Tangerang","Pabrik Es Balok Cirebon",
    "Tambak Udang Sidoarjo","Penyedia Kemasan Seafood","PT Logistik Maritim",
    "Gudang Pendingin Bekasi","Supplier Alat Tangkap","Agen Bahan Baku Olahan",
]

STAFF = [
    ("demo_admin","Admin POS Demo","admin.demo@pos.id",1),
    ("demo_kasir_01","Siti Nurhaliza","siti.demo@pos.id",2),
    ("demo_kasir_02","Budi Santoso","budi.demo@pos.id",2),
    ("demo_kasir_03","Dewi Anggraini","dewi.demo@pos.id",2),
    ("demo_gudang_01","Ahmad Fauzi","ahmad.demo@pos.id",2),
    ("demo_supervisor","Rina Wati","rina.demo@pos.id",2),
]

async def main():
    conn = await asyncpg.connect(DB_URL)

    # 0. Check if already seeded (idempotency check-then-skip)
    existing_sales = await conn.fetchval("SELECT count(*) FROM sales WHERE notes = $1", DEMO)
    if existing_sales and existing_sales >= 150:
        print(f"Demo data already seeded ({existing_sales} sales found). Skipping seed.")
        await print_summary(conn)
        await conn.close()
        return

    # 1. Prerequisites
    roles = await conn.fetch("SELECT id,name FROM roles ORDER BY id")
    role_map = {r['name']: r['id'] for r in roles}
    owner_role = role_map.get('Owner') or role_map.get('owner') or 1
    staff_role = role_map.get('Staff') or role_map.get('staff') or 2

    pm_rows = await conn.fetch("SELECT id,code FROM payment_methods WHERE is_active=true ORDER BY id")
    pm_ids = [r['id'] for r in pm_rows]
    pm_codes = [r['code'] for r in pm_rows]
    owner_id = (await conn.fetchrow("SELECT id FROM users WHERE role_id=$1 LIMIT 1", owner_role))['id']
    print(f"owner_id={owner_id}, payment_methods={pm_codes}")

    # 2. Units
    unit_ids = {}
    for code, name in UNITS:
        row = await conn.fetchrow("""
            INSERT INTO units (code, name, is_active) VALUES ($1,$2,true)
            ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name RETURNING id
        """, code, name)
        unit_ids[code] = row['id']
    print(f"Units: {len(unit_ids)}")

    # 3. Categories
    cat_ids = {}
    for name in CATEGORIES:
        row = await conn.fetchrow("""
            INSERT INTO categories (name, is_active, created_at, updated_at)
            VALUES ($1, true, now(), now())
            ON CONFLICT (name) DO UPDATE SET is_active=true RETURNING id
        """, name)
        cat_ids[name] = row['id']
    print(f"Categories: {len(cat_ids)}")

    # 4. Demo users
    user_ids = {'owner': owner_id}
    for uname, fname, email, role_idx in STAFF:
        rid = owner_role if role_idx == 1 else staff_role
        row = await conn.fetchrow("""
            INSERT INTO users (username,full_name,email,password_hash,is_active,role_id,created_by,updated_by)
            VALUES ($1,$2,$3,'$2a$12$demohash',true,$4,$5,$5)
            ON CONFLICT (username) DO UPDATE SET full_name=EXCLUDED.full_name, is_active=true
            RETURNING id
        """, uname, fname, email, rid, owner_id)
        user_ids[uname] = row['id']
    print(f"Users: {len(user_ids)}")

    # 5. Products
    product_ids = {}
    cat_list = list(cat_ids.values())
    unit_list = list(unit_ids.values())
    for code, name, cat_idx, u_idx, buy, sell, low, is_s, is_p in PRODUCTS:
        cid = cat_list[cat_idx] if cat_idx < len(cat_list) else cat_list[0]
        uid = unit_list[u_idx] if u_idx < len(unit_list) else unit_list[0]
        row = await conn.fetchrow("""
            INSERT INTO products (code,name,category_id,unit_id,purchase_price,selling_price,
                low_stock_threshold,is_sellable,is_purchasable,is_active,notes,created_by,updated_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,true,$10,$11,$11)
            ON CONFLICT (code) WHERE code IS NOT NULL
            DO UPDATE SET name=EXCLUDED.name, selling_price=EXCLUDED.selling_price,
                purchase_price=EXCLUDED.purchase_price, is_active=true, notes=EXCLUDED.notes,
                category_id=EXCLUDED.category_id, unit_id=EXCLUDED.unit_id
            RETURNING id
        """, code, name, cid, uid, buy, sell, low, is_s, is_p, DEMO, owner_id)
        product_ids[code] = row['id']
    print(f"Products: {len(product_ids)}")

    # 6. Customers & Suppliers
    customer_ids = []
    for cname in CUSTOMERS:
        existing = await conn.fetchrow("SELECT id FROM contacts WHERE type='customer' AND name=$1", cname)
        if existing:
            customer_ids.append(existing['id'])
            await conn.execute("UPDATE contacts SET is_active=true, notes=$1 WHERE id=$2", DEMO, existing['id'])
        else:
            row = await conn.fetchrow("""
                INSERT INTO contacts (type,name,phone,email,address,notes,is_active,created_by)
                VALUES ('customer',$1,$2,$3,$4,$5,true,$6) RETURNING id
            """, cname, f"+62 81{random.randint(10000000,99999999)}",
                f"{cname.lower().replace(' ','.')[:30]}@example.com",
                f"Jl. Pasar No.{random.randint(1,200)}, Jakarta", DEMO, owner_id)
            customer_ids.append(row['id'])

    supplier_ids = []
    for sname in SUPPLIERS:
        existing = await conn.fetchrow("SELECT id FROM contacts WHERE type='supplier' AND name=$1", sname)
        if existing:
            supplier_ids.append(existing['id'])
            await conn.execute("UPDATE contacts SET is_active=true, notes=$1 WHERE id=$2", DEMO, existing['id'])
        else:
            row = await conn.fetchrow("""
                INSERT INTO contacts (type,name,phone,email,address,notes,is_active,created_by)
                VALUES ('supplier',$1,$2,$3,$4,$5,true,$6) RETURNING id
            """, sname, f"+62 85{random.randint(10000000,99999999)}",
                f"{sname.lower().replace(' ','.')[:30]}@supplier.com",
                f"Kawasan Industri No.{random.randint(1,50)}, Surabaya", DEMO, owner_id)
            supplier_ids.append(row['id'])
    print(f"Customers: {len(customer_ids)}, Suppliers: {len(supplier_ids)}")

    # Product metadata map for fast lookup: code -> (pid, buy, sell, low_threshold)
    prod_meta = {}
    for code, name, cat_idx, u_idx, buy, sell, low, is_s, is_p in PRODUCTS:
        prod_meta[code] = (product_ids[code], buy, sell, low)

    # 7. Opening stock via opening_balance stock_movements (bounded per product)
    available = {}  # code -> Decimal on-hand tracker
    inflow_qty = {}   # code -> cumulative positive-movement quantity
    inflow_cost = {}  # code -> cumulative positive-movement cost
    init_sm_count = 0
    for i, (pcode, _pid) in enumerate(prod_meta.items()):
        pid, buy, sell, low = prod_meta[pcode]
        if i % 7 == 0:
            qty = D(0)
        elif i % 5 == 0:
            qty = D(random.randint(4, 12))
        elif i % 3 == 0:
            qty = D(random.randint(20, 60))
        else:
            qty = D(random.randint(60, 200))
        available[pcode] = qty
        if qty > 0:
            total_cost = qty * buy
            inflow_qty[pcode] = qty
            inflow_cost[pcode] = total_cost
            await conn.execute("""
                INSERT INTO stock_movements (product_id,movement_date,trigger,quantity,
                    unit_cost_at_movement,total_cost,reference_type,reference_id,reason,created_by)
                VALUES ($1,$2,'opening_balance',$3,$4,$5,NULL,NULL,$6,$7)
            """, pid, days_ago(85, 8), qty, buy, total_cost,
                f"DEMO: Opening balance {qty} {pcode}", owner_id)
            init_sm_count += 1
    print(f"Initial stock movements inserted: {init_sm_count}")

    # 7b. Opening cash capital injection (INV-03: cash must stay >= 0)
    capital_amt = D(500000000)
    capital_pm = pm_ids[0] if pm_ids else 1
    cap_cat = await conn.fetchval("SELECT id FROM financial_categories WHERE entry_type='income' LIMIT 1")
    if cap_cat:
        mfe_cap = await conn.fetchval("""
            INSERT INTO manual_finance_entries (category_id,entry_date,amount,payment_method_id,notes,
                lifecycle_status,created_by,version)
            VALUES ($1,$2,$3,$4,$5,'posted',$6,1)
            RETURNING id
        """, cap_cat, days_ago(85, 7), capital_amt, capital_pm, "DEMO: Modal Awal", owner_id)
        await conn.execute("""
            INSERT INTO cash_movements (movement_date,amount,direction,trigger,
                payment_method_id,reference_type,reference_id,created_by)
            VALUES ($1,$2,'in','manual_income',$3,'manual_finance_entry',$4,$5)
        """, days_ago(85, 7), capital_amt, capital_pm, mfe_cap, owner_id)
        print(f"Opening capital injected: {capital_amt} IDR")

    # 8. Purchases over 85 days (before sales to establish positive stock basis)
    purchase_count = 0
    purchase_dates = [days_ago(d, 9) for d in range(85, 0, -2)][:42]
    purchasable = [(c, m) for c, m in prod_meta.items() if any(p[0] == c and p[8] for p in PRODUCTS)]
    for pur_dt in purchase_dates:
        supplier = random.choice(supplier_ids)
        n_items = random.randint(2, 6)
        chosen = random.sample(purchasable, min(n_items, len(purchasable)))
        ref_no = f"PO-{pur_dt.strftime('%Y%m%d')}-{purchase_count+1:03d}"
        pur_row = await conn.fetchrow("""
            INSERT INTO purchases (reference_no,supplier_id,purchase_date,received_date,
                lifecycle_status,notes,created_by,posted_at,posted_by,version)
            VALUES ($1,$2,$3,$3,'completed',$4,$5,$3,$5,1)
            RETURNING id
        """, ref_no, supplier, pur_dt, DEMO, owner_id)
        purchase_id = pur_row['id']
        total = D(0)

        for ln, (pcode, (pid, buy, _sell, _low)) in enumerate(chosen, 1):
            unit_cost = (buy * D('0.88')).quantize(D('0.01'))
            qty_d = D(random.randint(15, 60))
            line_subtotal = (qty_d * unit_cost).quantize(D('0.01'))
            shipping = D(0) if random.random() > 0.4 else D(random.randint(1000, 6000))
            line_total = (line_subtotal + shipping).quantize(D('0.01'))
            total += line_total
            available[pcode] = available.get(pcode, D(0)) + qty_d
            inflow_qty[pcode] = inflow_qty.get(pcode, D(0)) + qty_d
            inflow_cost[pcode] = inflow_cost.get(pcode, D(0)) + line_subtotal

            await conn.execute("""
                INSERT INTO purchase_lines (purchase_id,product_id,quantity,unit_price,
                    line_subtotal,allocated_shipping,line_total,line_number)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """, purchase_id, pid, qty_d, unit_cost, line_subtotal, shipping, line_total, ln)

            await conn.execute("""
                INSERT INTO stock_movements (product_id,movement_date,trigger,quantity,
                    unit_cost_at_movement,total_cost,reference_type,reference_id,reason,created_by)
                VALUES ($1,$2,'purchase_receipt',$3,$4,$5,'purchase',$6,$7,$8)
            """, pid, pur_dt, qty_d, unit_cost, line_subtotal, purchase_id,
                f"DEMO: Purchase {purchase_id}", owner_id)

        pm_id = random.choice(pm_ids)
        await conn.execute("""
            INSERT INTO purchase_payments (purchase_id,payment_method_id,amount,payment_date,created_by)
            VALUES ($1,$2,$3,$4,$5)
        """, purchase_id, pm_id, total, pur_dt, owner_id)
        await conn.execute("""
            INSERT INTO cash_movements (movement_date,amount,direction,trigger,
                payment_method_id,reference_type,reference_id,created_by)
            VALUES ($1,$2,'out','supplier_payment',$3,'purchase',$4,$5)
        """, pur_dt, -total, pm_id, purchase_id, owner_id)
        purchase_count += 1
    print(f"Purchases created: {purchase_count}")

    # 9. Sales over 85 days — stock-aware so on-hand never goes negative
    cashiers = [user_ids[u] for u in ['demo_kasir_01','demo_kasir_02','demo_kasir_03','demo_supervisor'] if u in user_ids]
    if not cashiers: cashiers = [owner_id]

    sale_count = 0
    for day_offset in range(85, 0, -1):
        dt = days_ago(day_offset, 10)
        weekday = dt.weekday()
        if weekday >= 5: n_sales = random.randint(3, 6)
        elif weekday == 0: n_sales = random.randint(1, 3)
        else: n_sales = random.randint(2, 4)

        for _ in range(n_sales):
            cust_id = random.choice(customer_ids) if random.random() > 0.15 else None
            cashier = random.choice(cashiers)

            # pick products that actually have stock
            in_stock = [(c, m) for c, m in prod_meta.items() if available.get(c, D(0)) > 0]
            if not in_stock:
                break
            n_items = random.randint(1, 4)
            chosen = random.sample(in_stock, min(n_items, len(in_stock)))

            lines = []
            total = D(0)
            for pcode, (pid, buy, sell, low) in chosen:
                max_qty = int(min(available[pcode], D(10)))
                if max_qty < 1:
                    continue
                qty = D(random.randint(1, max_qty))
                lt = sell * qty
                # Cost basis mirrors the production posting rule
                # (SaleRepository.moving_average_unit_cost): weighted
                # average of positive inflows, so outflows at MAUC can
                # never drive inventory_value negative on positive qty.
                iq, ic = inflow_qty.get(pcode, D(0)), inflow_cost.get(pcode, D(0))
                cost = (ic / iq).quantize(D("0.0001")) if iq > 0 else buy
                lines.append((pid, pcode, qty, sell, lt, cost))
                total += lt

            if not lines:
                continue

            discount = D(0)
            if random.random() > 0.7:
                discount = D(int(float(total) * random.uniform(0.02, 0.08)))
            final = total - discount

            sale_dt = days_ago(day_offset, random.randint(8, 20))
            ref_no = f"INV-{sale_dt.strftime('%Y%m%d')}-{sale_count+1:04d}"

            sale_row = await conn.fetchrow("""
                INSERT INTO sales (reference_no,customer_id,sale_date,total_amount,discount_amount,
                    lifecycle_status,notes,created_by,posted_at,posted_by,version)
                VALUES ($1,$2,$3,$4,$5,'completed',$6,$7,$3,$7,1)
                RETURNING id
            """, ref_no, cust_id, sale_dt, total, discount, DEMO, cashier)
            sid = sale_row['id']

            for ln, (pid, pcode, qty, price, lt, cost) in enumerate(lines, 1):
                qty_d = D(qty)
                cogs = qty_d * cost
                await conn.execute("""
                    INSERT INTO sale_lines (sale_id,product_id,quantity,unit_price,line_total,
                        unit_cost_snapshot,cogs_total_snapshot,line_number,is_negative_stock_fallback,
                        discount_amount)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,false,0)
                """, sid, pid, qty_d, price, lt, cost, cogs, ln)

                await conn.execute("""
                    INSERT INTO stock_movements (product_id,movement_date,trigger,quantity,
                        unit_cost_at_movement,total_cost,reference_type,reference_id,reason,created_by)
                    VALUES ($1,$2,'sale',$3,$4,$5,'sale',$6,$7,$8)
                """, pid, sale_dt, D(-qty), cost, D(-qty) * cost, sid, f"DEMO: Sale {sid}", cashier)
                available[pcode] = available[pcode] - qty_d

            pm_id = random.choice(pm_ids)
            await conn.execute("""
                INSERT INTO sale_payments (sale_id,payment_method_id,amount,payment_date,created_by)
                VALUES ($1,$2,$3,$4,$5)
            """, sid, pm_id, final, sale_dt, cashier)

            await conn.execute("""
                INSERT INTO cash_movements (movement_date,amount,direction,trigger,
                    payment_method_id,reference_type,reference_id,created_by)
                VALUES ($1,$2,'in','sale_payment',$3,'sale',$4,$5)
            """, sale_dt, final, pm_id, sid, cashier)

            sale_count += 1

    print(f"Sales created: {sale_count}")

    # 10. Low-stock notifications based on ACTUAL remaining stock
    notif_count = 0
    for pcode, (pid, buy, sell, thr) in prod_meta.items():
        cur = available.get(pcode, D(0))
        if thr and cur <= thr:
            await conn.execute("""
                INSERT INTO notifications (user_id,category,severity,title,body,
                    reference_type,reference_id,is_read,created_at)
                VALUES ($1,'low_stock','warning',$2,$3,'product',$4,false,now())
            """, owner_id, f"DEMO: Stok rendah {pcode}",
                f"Produk {pcode} tersisa {cur} (batas {thr})", pid)
            notif_count += 1
    print(f"Notifications: {notif_count}")

    # 11. Manual finance entries (expenses & income)
    expense_cats = await conn.fetch("SELECT id FROM financial_categories WHERE entry_type='expense' LIMIT 5")
    income_cats = await conn.fetch("SELECT id FROM financial_categories WHERE entry_type='income' LIMIT 3")
    fin_count = 0
    for _ in range(15):
        if expense_cats and (random.random() > 0.4 or not income_cats):
            cat_id = random.choice(expense_cats)['id']
            etype = 'expense'
        else:
            cat_id = random.choice(income_cats)['id'] if income_cats else None
            if not cat_id: continue
            etype = 'income'
        amt = D(random.randint(100000, 3000000))
        dt = days_ago(random.randint(1, 60), random.randint(9, 17))
        pm_id = random.choice(pm_ids)
        row = await conn.fetchrow("""
            INSERT INTO manual_finance_entries (category_id,entry_date,amount,payment_method_id,notes,
                lifecycle_status,created_by,version)
            VALUES ($1,$2,$3,$4,$5,'posted',$6,1)
            RETURNING id
        """, cat_id, dt, amt, pm_id, DEMO, owner_id)
        mfe_id = row['id']

        direction = 'out' if etype == 'expense' else 'in'
        trigger = 'manual_expense' if direction == 'out' else 'manual_income'
        cash_amt = -amt if direction == 'out' else amt
        await conn.execute("""
            INSERT INTO cash_movements (movement_date,amount,direction,trigger,
                payment_method_id,reference_type,reference_id,created_by)
            VALUES ($1,$2,$3,$4,$5,'manual_finance_entry',$6,$7)
        """, dt, cash_amt, direction, trigger, pm_id, mfe_id, owner_id)
        fin_count += 1
    print(f"Manual finance entries: {fin_count}")

    await print_summary(conn)
    await conn.close()
    print("\nDone.")

async def print_summary(conn):
    counts = {}
    for t in ['users','categories','units','products','contacts','sales','sale_lines',
              'purchases','purchase_lines','sale_payments','stock_movements','notifications','cash_movements','manual_finance_entries']:
        counts[t] = await conn.fetchval(f"SELECT count(*) FROM {t}")

    trend = await conn.fetch("""
        SELECT date_trunc('day', sale_date)::date as d, count(*) as n,
               sum(total_amount) as revenue
        FROM sales WHERE lifecycle_status <> 'cancelled' AND posted_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)

    inv = await conn.fetchrow("""
        SELECT COALESCE(SUM(pv.inventory_value),0) AS val,
               COUNT(*) AS products,
               COUNT(*) FILTER (WHERE pv.on_hand_quantity < 0) AS negative,
               COUNT(*) FILTER (WHERE pv.on_hand_quantity <= 0) AS out_of_stock,
               COUNT(*) FILTER (WHERE p.is_active AND p.low_stock_threshold IS NOT NULL
                   AND pv.on_hand_quantity <= p.low_stock_threshold) AS low_stock
        FROM product_valuation pv JOIN products p ON p.id = pv.product_id
    """)

    # Invariant guard: a product must never have positive on-hand qty
    # with negative valuation under the MAUC costing rule. Failing this
    # means the seed mis-costs an outflow (sale at a rate above the
    # weighted-average inflow cost).
    bad = await conn.fetchval("""
        SELECT COUNT(*) FROM product_valuation
        WHERE on_hand_quantity > 0 AND inventory_value < 0
    """)

    print("\n=== SEED SUMMARY ===")
    for k,v in counts.items():
        print(f"  {k}: {v}")
    print(f"  Sales trend days: {len(trend)}")
    if trend:
        print(f"  Date range: {trend[0]['d']} → {trend[-1]['d']}")
        print(f"  Total revenue: {sum(t['revenue'] or 0 for t in trend):,.0f}")
    print(f"  Inventory value: {inv['val']:,.0f} | products: {inv['products']} | negative-stock products: {inv['negative']} | out-of-stock: {inv['out_of_stock']} | low-stock: {inv['low_stock']}")

    if bad:
        raise SystemExit(
            f"SEED INVARIANT VIOLATION: {bad} products have positive on-hand "
            f"quantity but negative inventory value. Do not use this dataset."
        )
    print("  Invariant OK: no product has positive qty with negative value.")

if __name__ == "__main__":
    asyncio.run(main())
