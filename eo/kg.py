"""Layer 4 · Knowledge Graph (Neo4j): real entities connected along the ontology's blueprint."""
import re
import sqlite3
from functools import cache

from neo4j import GraphDatabase

from .config import DB_PATH, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from .ontology import region_map


@cache
def driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def _run(cypher: str, **params):
    with driver().session() as s:
        return [r.data() for r in s.run(cypher, **params)]


def load():
    """SQLite rows -> graph. Region membership comes from the ONTOLOGY, not from a spreadsheet."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = lambda q: [dict(r) for r in db.execute(q)]  # noqa: E731
    regions = region_map()
    dc_region = {"DC-Chennai": "South India", "DC-Delhi": "North India", "DC-Mumbai": "West India", "DC-Kolkata": "East India"}

    lanes = rows("""SELECT supplier_id, product_id, warehouse, strftime('%Y_%m', promised_date) m,
                    AVG(julianday(COALESCE(delivered_date, '2026-09-30')) - julianday(promised_date)) delay,
                    SUM(delivered_date IS NULL) pending
                    FROM shipments GROUP BY 1, 2, 3, 4""")
    lane_props: dict = {}
    for l in lanes:
        k = (l["supplier_id"], l["product_id"], l["warehouse"])
        lane_props.setdefault(k, {})[f"delay_{l['m']}"] = round(l["delay"], 1)
        lane_props[k][f"pending_{l['m']}"] = l["pending"]

    stmts = [
        ("MATCH (n) DETACH DELETE n", {}),
        ("UNWIND $rows AS r MERGE (s:State {code: r.code}) MERGE (g:Region {name: r.region}) MERGE (s)-[:PART_OF]->(g)",
         {"rows": [{"code": c, "region": r} for c, r in regions.items()]}),
        ("UNWIND $rows AS r MERGE (w:Warehouse {name: r.w}) WITH w, r MATCH (g:Region {name: r.region}) MERGE (w)-[:SERVES]->(g)",
         {"rows": [{"w": w, "region": r} for w, r in dc_region.items()]}),
        ("UNWIND $rows AS r CREATE (c:Customer {id: r.customer_id, name: r.name, city: r.city, segment: r.segment}) "
         "WITH c, r MATCH (s:State {code: r.state_code}), (w:Warehouse {name: r.warehouse}) "
         "CREATE (c)-[:LOCATED_IN]->(s), (c)-[:SERVED_BY]->(w)", {"rows": rows("SELECT * FROM customers")}),
        ("UNWIND $rows AS r CREATE (:Product {id: r.product_id, name: r.name, category: r.category})",
         {"rows": rows("SELECT * FROM products")}),
        ("UNWIND $rows AS r CREATE (:Supplier {id: r.supplier_id, name: r.name, city: r.city})",
         {"rows": rows("SELECT * FROM suppliers")}),
        ("UNWIND $rows AS r MATCH (c:Customer {id: r.customer_id}), (p:Product {id: r.product_id}) "
         "CREATE (c)-[:PLACED]->(o:Order {id: r.order_id, date: r.order_date, month: substring(r.order_date, 0, 7), "
         "net_amount: r.net_amount, cancelled: r.cancel_flag = 'Y'})-[:CONTAINS {qty: r.qty}]->(p)",
         {"rows": rows("SELECT * FROM sales_data")}),
        ("UNWIND $rows AS r MATCH (s:Supplier {id: r.s}), (p:Product {id: r.p}), (w:Warehouse {name: r.w}) "
         "MERGE (s)-[:SUPPLIES]->(p) CREATE (s)-[l:SHIPS_TO {product: r.p}]->(w) SET l += r.props",
         {"rows": [{"s": s, "p": p, "w": w, "props": v} for (s, p, w), v in lane_props.items()]}),
    ]
    with driver().session() as s:
        for q, params in stmts:
            s.run(q, **params).consume()
    return _run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC")


def trace_relationships(region: str, current: str, previous: str) -> dict:
    """Multi-hop: Region <- State <- Customer -> Order -> Product <- Supplier -> Warehouse -> Region."""
    products = _run("""
        MATCH (:Region {name: $region})<-[:PART_OF]-(:State)<-[:LOCATED_IN]-(c:Customer)-[:PLACED]->(o:Order)-[:CONTAINS]->(p:Product)
        WHERE o.month IN [$cur, $prev]
        WITH p, c,
             sum(CASE WHEN o.month = $cur AND NOT o.cancelled THEN o.net_amount ELSE 0 END) AS cur,
             sum(CASE WHEN o.month = $prev AND NOT o.cancelled THEN o.net_amount ELSE 0 END) AS prev,
             sum(CASE WHEN o.month = $cur AND o.cancelled THEN 1 ELSE 0 END) AS cancelled_now
        WITH p, sum(cur) AS cur, sum(prev) AS prev, sum(cancelled_now) AS cancelled_orders,
             count(CASE WHEN cur < prev THEN 1 END) AS customers_down, count(c) AS customers,
             collect(CASE WHEN cur < prev THEN c.name END)[0..6] AS example_customers
        RETURN p.id AS product_id, p.name AS product, round(cur) AS current, round(prev) AS previous,
               round(cur - prev) AS change, customers_down, customers, cancelled_orders, example_customers
        ORDER BY change ASC LIMIT 4""", region=region, cur=current, prev=previous)
    top = [p["product_id"] for p in products if p["change"] < 0][:2]
    ck, pk = f"delay_{current.replace('-', '_')}", f"delay_{previous.replace('-', '_')}"
    lanes = _run("""
        MATCH (s:Supplier)-[l:SHIPS_TO]->(w:Warehouse)-[:SERVES]->(g:Region)
        WHERE l.product IN $pids
        RETURN s.name AS supplier, l.product AS product_id, w.name AS warehouse, g.name AS serves_region,
               l[$ck] AS avg_delay_days_current, l[$pk] AS avg_delay_days_previous,
               l['pending_' + $cm] AS shipments_not_delivered
        ORDER BY avg_delay_days_current DESC""", pids=top, ck=ck, pk=pk, cm=current.replace("-", "_"))
    graph = _trace_graph(region, top, current, previous)
    return {"region": region, "current": current, "previous": previous,
            "products_with_biggest_drop": products, "supply_lanes_for_those_products": lanes,
            "caveat": "A graph shows connections, not proof of cause. Verify with time-series and the supplier.",
            "graph": graph}


def _trace_graph(region, pids, cur, prev):
    rows = _run("""
        MATCH (g:Region {name: $region})<-[:PART_OF]-(st:State)<-[:LOCATED_IN]-(c:Customer)-[:PLACED]->(o:Order)-[:CONTAINS]->(p:Product)
        WHERE p.id IN $pids AND o.month IN [$cur, $prev]
        WITH g, st, c, p, sum(CASE WHEN o.month = $cur AND NOT o.cancelled THEN o.net_amount ELSE 0 END)
                          - sum(CASE WHEN o.month = $prev AND NOT o.cancelled THEN o.net_amount ELSE 0 END) AS delta
        WHERE delta < 0
        WITH g, st, c, p, delta ORDER BY delta ASC LIMIT 10
        MATCH (c)-[:SERVED_BY]->(w:Warehouse)<-[l:SHIPS_TO {product: p.id}]-(s:Supplier)
        RETURN g.name AS region, st.code AS state, c.id AS cid, c.name AS customer, p.id AS pid, p.name AS product,
               w.name AS warehouse, s.name AS supplier, delta, l[$ck] AS delay""",
                region=region, pids=pids, cur=cur, prev=prev, ck=f"delay_{cur.replace('-', '_')}")
    nodes, edges = {}, set()
    for r in rows:
        nodes[r["region"]] = {"id": r["region"], "label": r["region"], "group": "Region"}
        nodes[r["state"]] = {"id": r["state"], "label": r["state"], "group": "State"}
        nodes[r["cid"]] = {"id": r["cid"], "label": r["customer"], "group": "Customer"}
        nodes[r["pid"]] = {"id": r["pid"], "label": r["product"], "group": "Product"}
        nodes[r["warehouse"]] = {"id": r["warehouse"], "label": r["warehouse"], "group": "Warehouse"}
        nodes[r["supplier"]] = {"id": r["supplier"], "label": f"{r['supplier']}\n⏱ {r['delay']}d late", "group": "Supplier"}
        edges |= {(r["region"], r["state"], "has"), (r["state"], r["cid"], ""),
                  (r["cid"], r["pid"], f"bought less ₹{-r['delta'] / 1e5:.1f}L"),
                  (r["pid"], r["supplier"], "SUPPLIED_BY"), (r["supplier"], r["warehouse"], "SHIPS_TO (late)")}
    return {"nodes": list(nodes.values()), "edges": [{"from": a, "to": b, "label": l} for a, b, l in edges]}


def overview(region: str = "South India") -> dict:
    """Whole-region graph for the KG tab (orders rolled up to keep it readable)."""
    rows = _run("""
        MATCH (g:Region {name: $region})<-[:PART_OF]-(st:State)<-[:LOCATED_IN]-(c:Customer)-[:SERVED_BY]->(w:Warehouse)
        OPTIONAL MATCH (c)-[:PLACED]->(o:Order)-[:CONTAINS]->(p:Product) WHERE NOT o.cancelled
        WITH g, st, c, w, p, sum(o.net_amount) AS rev
        RETURN g.name AS region, st.code AS state, c.id AS cid, c.name AS customer, c.segment AS segment,
               w.name AS warehouse, p.id AS pid, p.name AS product, rev""", region=region)
    sup = _run("""MATCH (s:Supplier)-[l:SHIPS_TO]->(w:Warehouse)-[:SERVES]->(:Region {name: $region})
                  RETURN s.name AS supplier, collect(l.product) AS pids, w.name AS warehouse""", region=region)
    nodes, edges = {}, set()
    for r in rows:
        nodes[r["region"]] = {"id": r["region"], "label": r["region"], "group": "Region"}
        nodes[r["state"]] = {"id": r["state"], "label": r["state"], "group": "State"}
        nodes[r["cid"]] = {"id": r["cid"], "label": r["customer"], "group": "Customer", "title": r["segment"]}
        nodes[r["warehouse"]] = {"id": r["warehouse"], "label": r["warehouse"], "group": "Warehouse"}
        edges |= {(r["state"], r["region"], ""), (r["cid"], r["state"], ""), (r["cid"], r["warehouse"], "")}
        if r["pid"]:
            nodes[r["pid"]] = {"id": r["pid"], "label": r["product"], "group": "Product"}
            edges.add((r["cid"], r["pid"], ""))
    for s in sup:
        nodes[s["supplier"]] = {"id": s["supplier"], "label": s["supplier"], "group": "Supplier"}
        edges.add((s["supplier"], s["warehouse"], "SHIPS_TO"))
        for pid in s["pids"]:
            edges.add((s["supplier"], pid, ""))
    return {"nodes": list(nodes.values()), "edges": [{"from": a, "to": b, "label": l} for a, b, l in edges]}


def run_cypher(query: str) -> dict:
    if re.search(r"\b(create|merge|delete|set|remove|drop|load|call)\b", query, re.I):
        return {"error": "read-only: MATCH/RETURN queries only"}
    try:
        return {"rows": _run(query)[:40], "cypher": query}
    except Exception as e:
        return {"error": str(e), "cypher": query}


SCHEMA = """(:Region {name})<-[:PART_OF]-(:State {code})<-[:LOCATED_IN]-(:Customer {id,name,city,segment})
(:Customer)-[:SERVED_BY]->(:Warehouse {name})-[:SERVES]->(:Region)
(:Customer)-[:PLACED]->(:Order {id,date,month:'YYYY-MM',net_amount,cancelled:bool})-[:CONTAINS {qty}]->(:Product {id,name,category})
(:Supplier {id,name,city})-[:SUPPLIES]->(:Product)
(:Supplier)-[:SHIPS_TO {product, delay_YYYY_MM, pending_YYYY_MM}]->(:Warehouse)"""


if __name__ == "__main__":
    for r in load():
        print(f"{r['label']:10s} {r['n']:>5}")
