import json

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, catalog, kg, ontology, semantic
from .config import MODEL, ROOT

app = FastAPI(title="Enterprise Context Console")
WEB = ROOT / "web"


class Ask(BaseModel):
    question: str
    layers: list[str] = ["catalog", "semantic", "ontology", "kg"]
    model: str | None = None


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.post("/api/ask")
def ask(body: Ask):
    def stream():
        try:
            for ev in agent.run(body.question, set(body.layers), body.model or MODEL):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': f'{type(e).__name__}: {e}'})}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/info")
def info():
    return {"model": MODEL}


@app.get("/api/catalog")
def get_catalog():
    return catalog.CATALOG


@app.get("/api/semantic")
def get_semantic():
    return semantic.SEM


@app.get("/api/ontology")
def get_ontology():
    return {**ontology.schema_graph(), "ttl": (ROOT / "layers" / "ontology.ttl").read_text()}


@app.get("/api/kg")
def get_kg(region: str = "South India"):
    return kg.overview(region)


@app.get("/api/dashboard")
def dashboard():
    """Numbers for the Charts tab: all computed through the semantic layer."""
    south = ontology.ontology_lookup("South India")["state_codes"]
    trend = {}
    for m in ["2026-06", "2026-07", "2026-08", "2026-09"]:
        by_state = semantic.query_metric("revenue", m, group_by="state")["rows"]
        trend[m] = {"South India": sum(r["current"] for r in by_state if r["key"] in south),
                    "Rest of India": sum(r["current"] for r in by_state if r["key"] not in south)}
    naive = semantic.query_metric("gross_bookings", "2026-09", "2026-08", states=south)["rows"][0]
    gov = semantic.query_metric("revenue", "2026-09", "2026-08", states=south)["rows"][0]
    return {
        "trend": trend,
        "by_state": semantic.query_metric("revenue", "2026-09", "2026-08", group_by="state", states=south)["rows"],
        "by_product": semantic.query_metric("revenue", "2026-09", "2026-08", group_by="product", states=south)["rows"],
        "naive_vs_governed": {"naive": naive, "governed": gov},
        "lanes": kg.trace_relationships("South India", "2026-09", "2026-08")["supply_lanes_for_those_products"],
    }


app.mount("/web", StaticFiles(directory=WEB), name="web")
