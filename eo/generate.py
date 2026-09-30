"""Build the demo warehouse (SQLite) with a hidden story baked in:

South India revenue drops in Sep 2026 because Supplier B (Sri Venkateswara
Compressors) shipped ACs and fridges late to DC-Chennai -> stock-outs ->
dealers cancelled / didn't reorder. Nothing in the sales table says "supplier".
"""
import random
import sqlite3
from datetime import date, timedelta

from .config import DB_PATH

rng = random.Random(42)

REGION_OF = {**dict.fromkeys(["KA", "TN", "KL", "AP", "TG", "PY"], "South"),
             **dict.fromkeys(["DL", "HR", "UP", "PB"], "North"),
             **dict.fromkeys(["MH", "GJ", "GA"], "West"),
             **dict.fromkeys(["WB", "OD"], "East")}
DC = {"South": "DC-Chennai", "North": "DC-Delhi", "West": "DC-Mumbai", "East": "DC-Kolkata"}

CITIES = {
    "KA": ["Bengaluru", "Mysuru", "Hubballi"], "TN": ["Chennai", "Coimbatore", "Madurai"],
    "KL": ["Kochi", "Thiruvananthapuram"], "AP": ["Visakhapatnam", "Vijayawada"],
    "TG": ["Hyderabad", "Warangal"], "PY": ["Puducherry"],
    "DL": ["New Delhi"], "HR": ["Gurugram", "Faridabad"], "UP": ["Noida", "Lucknow"], "PB": ["Ludhiana"],
    "MH": ["Mumbai", "Pune", "Nagpur"], "GJ": ["Ahmedabad", "Surat"], "GA": ["Panaji"],
    "WB": ["Kolkata"], "OD": ["Bhubaneswar"],
}
CUSTOMERS_PER_STATE = {"KA": 5, "TN": 5, "KL": 3, "AP": 3, "TG": 4, "PY": 2,
                       "DL": 4, "HR": 3, "UP": 4, "PB": 2, "MH": 5, "GJ": 4, "GA": 3, "WB": 4, "OD": 2}
PREFIX = ["Sri Lakshmi", "Balaji", "Ganesh", "Sai", "Om", "Shree", "New India", "Royal", "Kaveri",
          "Deccan", "Metro", "Galaxy", "Everest", "Sunrise", "Annapurna", "Krishna", "Vijaya", "Star"]
SUFFIX = ["Electronics", "Home Appliances", "Traders", "Digital World", "Agencies", "Retail", "Enterprises"]

PRODUCTS = [  # id, name, category, price
    ("P01", "CoolBreeze Inverter AC 1.5T", "Cooling", 38000),
    ("P02", "FrostKing Refrigerator 260L", "Cooling", 27000),
    ("P03", "AquaPure RO Purifier", "Kitchen", 14000),
    ("P04", "SpinMax Washing Machine 7kg", "Home", 24000),
    ("P05", "BrewBuddy Mixer Grinder", "Kitchen", 4500),
    ("P06", "Zephyr Ceiling Fan", "Home", 3200),
    ("P07", "SmartView 43in TV", "Home", 29000),
    ("P08", "AirLite Air Cooler", "Cooling", 9000),
]
SUPPLIERS = [
    ("S_A", "Northstar Components", "Gurugram"),
    ("S_B", "Sri Venkateswara Compressors", "Chennai"),
    ("S_C", "Malabar Filters", "Kochi"),
    ("S_D", "Deccan Motors", "Pune"),
    ("S_E", "Ganga Electronics", "Noida"),
]
# who ships which product to which DC
def supplier_for(pid: str, dc: str) -> str:
    if pid in ("P01", "P02"):
        return "S_B" if dc == "DC-Chennai" else "S_A"
    return {"P03": "S_C", "P05": "S_C", "P04": "S_D", "P06": "S_D", "P07": "S_E", "P08": "S_E"}[pid]

MONTHS = ["2026-06", "2026-07", "2026-08", "2026-09"]
HIT = {"P01", "P02"}  # products hit by the South stock-out in September


def month_days(m: str):
    y, mm = map(int, m.split("-"))
    d = date(y, mm, 1)
    last = 29 if m == "2026-09" else 28
    return [d + timedelta(days=i) for i in range(last)]


def build():
    DB_PATH.parent.mkdir(exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE customers(customer_id TEXT PRIMARY KEY, name TEXT, city TEXT, state_code TEXT, segment TEXT, warehouse TEXT);
    CREATE TABLE products(product_id TEXT PRIMARY KEY, name TEXT, category TEXT, unit_price INTEGER);
    CREATE TABLE suppliers(supplier_id TEXT PRIMARY KEY, name TEXT, city TEXT);
    CREATE TABLE sales_data(order_id TEXT PRIMARY KEY, order_date TEXT, customer_id TEXT, product_id TEXT,
        qty INTEGER, net_amount REAL, gst_amount REAL, gross_amount REAL, cancel_flag TEXT);
    CREATE TABLE shipments(shipment_id TEXT PRIMARY KEY, supplier_id TEXT, product_id TEXT, warehouse TEXT,
        promised_date TEXT, delivered_date TEXT, qty INTEGER);
    CREATE TABLE sales_backup_2025(order_id TEXT, order_date TEXT, customer_id TEXT, product_id TEXT, qty INTEGER, amount REAL, status TEXT);
    CREATE TABLE sales_region_tmp(month TEXT, region TEXT, revenue REAL);
    """)

    # customers
    customers, used = [], set()
    n = 1
    for st, k in CUSTOMERS_PER_STATE.items():
        for _ in range(k):
            while True:
                name = f"{rng.choice(PREFIX)} {rng.choice(SUFFIX)}"
                if name not in used:
                    used.add(name)
                    break
            seg = rng.choices(["Platinum", "Gold", "Standard"], [2, 3, 5])[0]
            customers.append((f"C{n:03d}", name, rng.choice(CITIES[st]), st, seg, DC[REGION_OF[st]]))
            n += 1
    db.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", customers)
    db.executemany("INSERT INTO products VALUES (?,?,?,?)", PRODUCTS)
    db.executemany("INSERT INTO suppliers VALUES (?,?,?)", SUPPLIERS)

    # orders
    price = {p[0]: p[3] for p in PRODUCTS}
    weights = [16, 14, 10, 10, 8, 10, 9, 12]
    seg_scale = {"Platinum": 2.2, "Gold": 1.5, "Standard": 1.0}
    rows, oid = [], 1
    for m in MONTHS:
        days = month_days(m)
        for cid, _, _, st, seg, _ in customers:
            south = REGION_OF[st] == "South"
            n_orders = rng.randint(5, 9)
            for _ in range(n_orders):
                pid = rng.choices([p[0] for p in PRODUCTS], weights)[0]
                cancel = "Y" if rng.random() < 0.04 else "N"
                if m == "2026-09" and south and pid in HIT:
                    if rng.random() < 0.35:
                        continue  # dealer didn't even reorder
                    if rng.random() < 0.45:
                        cancel = "Y"  # ordered, but DC had no stock
                qty = max(1, int(rng.randint(4, 14) * seg_scale[seg]))
                net = qty * price[pid]
                gst = round(net * 0.18, 2)
                rows.append((f"O{oid:05d}", rng.choice(days).isoformat(), cid, pid, qty, net, gst, net + gst, cancel))
                oid += 1
    db.executemany("INSERT INTO sales_data VALUES (?,?,?,?,?,?,?,?,?)", rows)

    # inbound shipments: weekly per product per DC
    ships, sid = [], 1
    for m in MONTHS:
        for week_start in month_days(m)[::7]:
            for dc in DC.values():
                for pid, *_ in PRODUCTS:
                    sup = supplier_for(pid, dc)
                    delay = rng.randint(0, 3)
                    delivered = week_start + timedelta(days=delay)
                    if m == "2026-09" and sup == "S_B":
                        delay = rng.randint(12, 22)
                        delivered = week_start + timedelta(days=delay)
                        if delivered > date(2026, 9, 29):
                            delivered = None
                    ships.append((f"SH{sid:05d}", sup, pid, dc, week_start.isoformat(),
                                  delivered.isoformat() if delivered else None, rng.randint(80, 160)))
                    sid += 1
    db.executemany("INSERT INTO shipments VALUES (?,?,?,?,?,?,?)", ships)

    # decoys: a stale backup and a sandbox rollup with the wrong region map (Goa in South, no Sept)
    backup = []
    for m in ["2025-09", "2025-10", "2025-11", "2025-12"]:
        for i in range(400):
            c = rng.choice(customers)
            pid = rng.choice(PRODUCTS)[0]
            q = rng.randint(3, 20)
            backup.append((f"B{m}{i}", f"{m}-{rng.randint(1, 28):02d}", c[0], pid, q, q * price[pid] * 1.18,
                           rng.choice(["DONE", "DONE", "DONE", "CANCELLED"])))
    db.executemany("INSERT INTO sales_backup_2025 VALUES (?,?,?,?,?,?,?)", backup)
    old_map = {**REGION_OF, "GA": "South"}
    tmp = db.execute("""SELECT strftime('%Y-%m', s.order_date) m, c.state_code, SUM(s.gross_amount)
                        FROM sales_data s JOIN customers c USING(customer_id)
                        WHERE s.order_date < '2026-08-21' GROUP BY 1, 2""").fetchall()
    agg: dict = {}
    for m, st, v in tmp:
        agg[(m, old_map[st])] = agg.get((m, old_map[st]), 0) + v
    db.executemany("INSERT INTO sales_region_tmp VALUES (?,?,?)", [(m, r, round(v)) for (m, r), v in agg.items()])

    db.commit()
    return db


if __name__ == "__main__":
    db = build()
    for t in ["customers", "products", "suppliers", "sales_data", "shipments", "sales_backup_2025", "sales_region_tmp"]:
        print(f"{t:20s} {db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>6} rows")
    print(f"→ {DB_PATH}")
