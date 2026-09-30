"""Layer 2 · Semantic Layer: governed metric definitions compiled to SQL."""
import re
import sqlite3

import yaml

from .config import DB_PATH, LAYERS

SEM = yaml.safe_load((LAYERS / "semantic.yaml").read_text())


def _db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def get_metric(name: str) -> dict:
    m = SEM["metrics"].get(name)
    if not m:
        return {"error": f"unknown metric '{name}'", "available": list(SEM["metrics"])}
    return {"metric": name, **m, "dimensions": list(SEM["dimensions"]), "reporting_calendar": SEM["reporting_calendar"]}


def query_metric(metric: str, current: str, previous: str | None = None, group_by: str | None = None,
                 states: list[str] | None = None, segments: list[str] | None = None, limit: int = 20) -> dict:
    """Compute a governed metric for `current` month (YYYY-MM), optionally vs `previous`, grouped by a dimension."""
    m = SEM["metrics"].get(metric)
    if not m:
        return {"error": f"unknown metric '{metric}'", "available": list(SEM["metrics"])}
    if group_by and group_by not in SEM["dimensions"]:
        return {"error": f"unknown dimension '{group_by}'", "available": list(SEM["dimensions"])}
    months = [x for x in (current, previous) if x]
    if not all(re.fullmatch(r"\d{4}-\d{2}", x) for x in months):
        return {"error": "months must be YYYY-MM"}

    month = SEM["dimensions"]["month"]
    dim = SEM["dimensions"][group_by] if group_by else "'all'"
    where = list(m["filters"]) + [f"{month} IN ({','.join('?' * len(months))})"]
    params: list = list(months)
    if states:
        where.append(f"c.state_code IN ({','.join('?' * len(states))})")
        params += states
    if segments:
        where.append(f"c.segment IN ({','.join('?' * len(segments))})")
        params += segments
    sql = (f"SELECT {dim} AS key, {month} AS month, {m['expr']} AS value\n"
           f"FROM {SEM['source']}\n  " + "\n  ".join(SEM["joins"]) +
           "\nWHERE " + "\n  AND ".join(where) + "\nGROUP BY key, month")

    rows = _db().execute(sql, params).fetchall()
    table: dict = {}
    for r in rows:
        table.setdefault(r["key"], {})[r["month"]] = r["value"] or 0
    out = []
    for key, v in table.items():
        cur, prev = v.get(current, 0), v.get(previous, 0) if previous else None
        row = {"key": key, "current": round(cur, 1)}
        if previous:
            row["previous"] = round(prev, 1)
            row["change"] = round(cur - prev, 1)
            row["change_pct"] = round(100 * (cur - prev) / prev, 1) if prev else None
        out.append(row)
    out.sort(key=lambda r: r.get("change", -r["current"]) if previous else -r["current"])
    # the layer computes the total itself (a separate query, so non-additive metrics are right too)
    total = (query_metric(metric, current, previous, None, states, segments)["rows"] or [None])[0] if group_by else None
    return {
        "metric": metric, "definition": m["description"], "unit": m["unit"],
        "current": current, "previous": previous, "group_by": group_by,
        "scope": {"states": states or "ALL INDIA (no state filter applied)", "segments": segments or "all"},
        "total": total, "rows": out[:limit], "sql": sql, "params": params,
        "note": "Use 'total' for the overall figure; do not add up rows yourself.",
    }


def run_sql(sql: str) -> dict:
    """Raw SQL, no semantics. What an agent does when there is no semantic layer."""
    if not re.match(r"\s*(select|with)\b", sql, re.I):
        return {"error": "read-only: SELECT/WITH only"}
    try:
        cur = _db().execute(sql)
        rows = [dict(r) for r in cur.fetchmany(50)]
        return {"rows": rows, "sql": sql}
    except Exception as e:  # show the model its own mistake
        return {"error": str(e), "sql": sql}


def list_tables() -> dict:
    db = _db()
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: [c[1] for c in db.execute(f"PRAGMA table_info({t})")] for t in names}
