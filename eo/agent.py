"""A small tool-calling agent. Each context layer = a set of tools. Turn layers off to watch it stumble."""
import json
import time

from openai import OpenAI

from . import catalog, kg, ontology, semantic
from .config import MODEL, TODAY

client = OpenAI()


def fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


S = {"type": "string"}
TOOLS = {
    "raw": [
        fn("list_tables", "List every table and its columns in the warehouse (no descriptions).", {}),
        fn("run_sql", "Run a read-only SQLite query against the warehouse.", {"sql": S}, ["sql"]),
    ],
    "catalog": [
        fn("search_catalog", "DATA CATALOG: find datasets with owner, freshness, certification, lineage and column docs.",
           {"query": S}, ["query"]),
    ],
    "semantic": [
        fn("get_metric", "SEMANTIC LAYER: the governed definition of a business metric (revenue, orders, "
           "cancellation_rate, active_customers, gross_bookings).", {"name": S}, ["name"]),
        fn("query_metric", "SEMANTIC LAYER: compute a governed metric for a month vs a previous month, optionally "
           "grouped by a dimension (state, segment, product, category, customer) and filtered by state codes / segments.",
           {"metric": S, "current": {"type": "string", "description": "YYYY-MM"},
            "previous": {"type": "string", "description": "YYYY-MM"},
            "group_by": {"type": "string", "enum": ["state", "segment", "product", "category", "customer", "month"]},
            "states": {"type": "array", "items": S, "description": "state codes, e.g. ['KA','TN']"},
            "segments": {"type": "array", "items": S}}, ["metric", "current"]),
    ],
    "ontology": [
        fn("ontology_lookup", "ENTERPRISE ONTOLOGY: resolve a business term (region, customer type, product type...) "
           "to the company's official concept: definition, is-a hierarchy, member states, relationships, data mapping, rules.",
           {"term": S}, ["term"]),
    ],
    "kg": [
        fn("trace_relationships", "KNOWLEDGE GRAPH: multi-hop trace for a region's change between two months: "
           "Region <- State <- Customer -> Order -> Product <- Supplier -> Warehouse. Finds which products and supply "
           "lanes are connected to the drop. Region must be the ontology label, e.g. 'South India'.",
           {"region": S, "current": S, "previous": S}, ["region", "current", "previous"]),
        fn("run_cypher", "KNOWLEDGE GRAPH: read-only Cypher against Neo4j. Schema:\n" + kg.SCHEMA, {"query": S}, ["query"]),
    ],
}
LAYER_OF = {t["function"]["name"]: layer for layer, ts in TOOLS.items() for t in ts}

IMPL = {
    "list_tables": lambda a: semantic.list_tables(),
    "run_sql": lambda a: semantic.run_sql(a["sql"]),
    "search_catalog": lambda a: catalog.search_catalog(a["query"]),
    "get_metric": lambda a: semantic.get_metric(a["name"]),
    "query_metric": lambda a: semantic.query_metric(**_resolve_region(a)),
    "ontology_lookup": lambda a: ontology.ontology_lookup(a["term"]),
    "trace_relationships": lambda a: kg.trace_relationships(**a),
    "run_cypher": lambda a: kg.run_cypher(a["query"]),
}

def _resolve_region(a: dict) -> dict:
    """Semantic layer + ontology working together: a region name becomes the ontology's member states."""
    a = dict(a)
    region = a.pop("region", None)
    if region:
        hit = ontology.ontology_lookup(region)
        if not hit.get("state_codes"):
            raise ValueError(f"'{region}' is not a region in the ontology")
        a["states"] = hit["state_codes"]
    return a


# only offered when the Ontology layer is on: without it, the agent has to guess state codes itself
REGION_ARG = {"type": "string", "description": "a region name, e.g. 'South India'. Resolved to states by the ontology."}

NAMES = {"catalog": "Data Catalog", "semantic": "Semantic Layer", "ontology": "Enterprise Ontology", "kg": "Knowledge Graph"}


def system_prompt(layers: set[str]) -> str:
    on = [NAMES[l] for l in NAMES if l in layers]
    base = (f"You are the analytics agent of Anytime Appliances (Indian consumer-appliance distributor). "
            f"Today is {TODAY}. Amounts are INR; report them in ₹ lakh (1 lakh = 100,000). "
            "Use tools; never invent numbers. Be concise: 6-12 lines of markdown.\n")
    if not on:
        return base + "You only have raw database access (table names + SQL). Answer as best you can."
    return base + (
        f"Context layers available: {', '.join(on)}. Work like this:\n"
        "- Data Catalog: pick certified, fresh datasets; ignore deprecated/sandbox ones.\n"
        "- Enterprise Ontology: resolve EVERY business term BEFORE querying, one ontology_lookup per term (e.g. 'key account' AND 'South' are two lookups); apply what each maps to (e.g. segments) as filters. Never guess which states a region contains. For a region question, pass region=... to query_metric and quote its 'total'.\n"
        "- Semantic Layer: compute metrics only with the governed definitions; take reporting periods (\"last month\", MoM) from its reporting_calendar.\n"
        "- Knowledge Graph: trace multi-hop relationships (customers → products → suppliers → warehouses) to explain WHY.\n"
        "Skip steps whose layer is not available. Say clearly that graph connections are not proof of causation "
        "and suggest what to verify. End with a line 'Context used:' naming which layer gave which fact."
        + (f"\nReporting calendar (Semantic Layer): {json.dumps(semantic.SEM['reporting_calendar'])}. "
           "Unless the user names other months, compare current_month vs previous_month." if "semantic" in layers else ""))


def _for_model(result: dict) -> str:
    r = {k: v for k, v in result.items() if k not in ("graph",)}
    return json.dumps(r, default=str)[:12000]


def run(question: str, layers: set[str], model: str = MODEL):
    """Yields UI events: step, tool_call, tool_result, answer, done."""
    tools = TOOLS["raw"] + [t for l in NAMES if l in layers for t in TOOLS[l]]
    if "ontology" in layers:
        tools = [json.loads(json.dumps(t)) for t in tools]
        for t in tools:
            if t["function"]["name"] == "query_metric":
                t["function"]["parameters"]["properties"]["region"] = REGION_ARG
    msgs = [{"role": "system", "content": system_prompt(layers)}, {"role": "user", "content": question}]
    t0 = time.time()
    for _ in range(12):
        yield {"type": "thinking"}
        resp = client.chat.completions.create(model=model, messages=msgs, tools=tools)
        msg = resp.choices[0].message
        msgs.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            yield {"type": "answer", "text": msg.content or "", "seconds": round(time.time() - t0, 1)}
            return
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool_call", "id": call.id, "name": name, "layer": LAYER_OF.get(name, "raw"), "args": args}
            try:
                result = IMPL[name](args)
            except Exception as e:  # surface the error to the model and the audience
                result = {"error": f"{type(e).__name__}: {e}"}
            yield {"type": "tool_result", "id": call.id, "name": name, "layer": LAYER_OF.get(name, "raw"), "result": result}
            msgs.append({"role": "tool", "tool_call_id": call.id, "content": _for_model(result)})
    yield {"type": "answer", "text": "_Stopped after 12 steps._", "seconds": round(time.time() - t0, 1)}
