"""Layer 3 · Enterprise Ontology (RDF/OWL via rdflib): meaning, hierarchy, relationships, rules."""
from rdflib import Graph, Namespace
from rdflib.namespace import OWL, RDF, RDFS, SKOS

from .config import LAYERS

EO = Namespace("https://aianytime.in/ontology/retail#")
G = Graph()
G.parse(LAYERS / "ontology.ttl")
G.bind("eo", EO)
G.bind("skos", SKOS)

PREFIXES = """PREFIX eo: <https://aianytime.in/ontology/retail#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
"""


def _q(sparql: str, **bindings):
    return list(G.query(PREFIXES + sparql, initBindings=bindings))


def _short(u) -> str:
    return str(u).split("#")[-1]


def find_concept(term: str):
    t = term.lower().strip()
    rows = _q("""SELECT DISTINCT ?c ?lab WHERE {
        { ?c rdfs:label ?lab } UNION { ?c skos:altLabel ?lab } UNION { ?c eo:code ?lab } }""")
    exact = [r.c for r in rows if str(r.lab).lower() == t]
    partial = [r.c for r in rows if t in str(r.lab).lower() or str(r.lab).lower() in t]
    return (exact or partial or [None])[0]


def ontology_lookup(term: str) -> dict:
    """Resolve a business term: what it IS, its is-a chain, relationships, data mapping and rules."""
    c = find_concept(term)
    if c is None:
        return {"term": term, "found": False,
                "concepts": sorted({_short(s) for s in G.subjects(RDF.type, OWL.Class)})}
    out: dict = {"term": term, "found": True, "concept": _short(c),
                 "label": str(G.value(c, RDFS.label) or _short(c)),
                 "definition": str(G.value(c, SKOS.definition) or ""),
                 "aliases": sorted(str(a) for a in G.objects(c, SKOS.altLabel)),
                 "maps_to_data": str(G.value(c, EO.mapsTo) or "")}
    kind = G.value(c, RDF.type)
    if kind == OWL.Class:
        # is-a reasoning via property paths: every ancestor and every descendant
        out["is_a"] = [str(r.l) for r in _q("SELECT ?l WHERE { ?c rdfs:subClassOf+ ?s . ?s rdfs:label ?l }", c=c)]
        out["subtypes"] = [f"{r.l} ({r.m})" if r.m else str(r.l) for r in _q(
            "SELECT ?l ?m WHERE { ?s rdfs:subClassOf+ ?c . ?s rdfs:label ?l OPTIONAL { ?s eo:mapsTo ?m } }", c=c)]
        rels = _q("""SELECT ?pl ?dl ?rl WHERE { ?p a owl:ObjectProperty ; rdfs:label ?pl ; rdfs:domain ?d ; rdfs:range ?r .
                     ?d rdfs:label ?dl . ?r rdfs:label ?rl . ?c rdfs:subClassOf* ?x . FILTER(?d = ?x || ?r = ?x) }""", c=c)
        out["relationships"] = [f"{r.dl} —{r.pl}→ {r.rl}" for r in rels]
    elif kind == EO.Region:
        members = _q("SELECT ?code ?l WHERE { ?s eo:partOf+ ?c ; eo:code ?code ; rdfs:label ?l } ORDER BY ?code", c=c)
        out["member_states"] = [{"code": str(r.code), "name": str(r.l)} for r in members]
        out["state_codes"] = [str(r.code) for r in members]
    elif kind == EO.State:
        out["region"] = str(G.value(G.value(c, EO.partOf), RDFS.label))
    rules = _q("""SELECT ?l ?o WHERE { ?r a eo:BusinessRule ; rdfs:label ?l ; eo:owner ?o ; eo:appliesTo ?t .
                  { ?c rdfs:subClassOf* ?t } UNION { ?c a ?t } }""", c=c)
    out["business_rules"] = sorted({f"{r.l} (owner: {r.o})" for r in rules})
    return out


def schema_graph() -> dict:
    """Classes + relationships for the Ontology tab (TBox only, plus regions)."""
    nodes, edges = [], []
    for c in G.subjects(RDF.type, OWL.Class):
        if c == EO.BusinessRule:
            continue
        nodes.append({"id": _short(c), "label": str(G.value(c, RDFS.label)), "group": "class"})
        for sup in G.objects(c, RDFS.subClassOf):
            edges.append({"from": _short(c), "to": _short(sup), "label": "is-a", "kind": "isa"})
    for p in G.subjects(RDF.type, OWL.ObjectProperty):
        d, r = G.value(p, RDFS.domain), G.value(p, RDFS.range)
        edges.append({"from": _short(d), "to": _short(r), "label": str(G.value(p, RDFS.label)), "kind": "rel"})
    for reg in G.subjects(RDF.type, EO.Region):
        nodes.append({"id": _short(reg), "label": str(G.value(reg, RDFS.label)), "group": "region"})
        edges.append({"from": _short(reg), "to": "Region", "label": "instance of", "kind": "inst"})
    return {"nodes": nodes, "edges": edges}


def region_map() -> dict[str, str]:
    """state code -> region label, straight from the ontology (used to build the KG)."""
    return {str(r.code): str(r.rl) for r in _q(
        "SELECT ?code ?rl WHERE { ?s a eo:State ; eo:code ?code ; eo:partOf ?r . ?r rdfs:label ?rl }")}
