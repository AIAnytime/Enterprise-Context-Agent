"""Layer 1 · Data Catalog: find the right dataset (owner, freshness, certification, lineage)."""
import yaml

from .config import LAYERS

CATALOG = yaml.safe_load((LAYERS / "catalog.yaml").read_text())


def search_catalog(query: str) -> dict:
    words = [w for w in query.lower().replace(",", " ").split() if len(w) > 2]

    def score(d):
        text = " ".join([d["name"], d["description"], " ".join(d.get("glossary_terms", [])),
                         " ".join(d.get("columns", {}))]).lower()
        hit = sum(w in text for w in words)
        return hit + (2 if d["certified"] else 0) - (3 if d["status"] == "deprecated" else 0)

    ranked = sorted(CATALOG["datasets"], key=score, reverse=True)
    keep = ["name", "description", "owner", "certified", "status", "updated_at", "lineage", "quality_score", "columns"]
    return {
        "datasets": [{k: d[k] for k in keep if k in d} for d in ranked[:5]],
        "glossary": [g for g in CATALOG["glossary"] if any(w in g["term"].lower() for w in words)],
        "advice": "Prefer certified, active datasets with recent updated_at. Never report from deprecated or sandbox data.",
    }
