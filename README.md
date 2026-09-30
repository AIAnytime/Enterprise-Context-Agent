# Enterprise Context Agent

**An AI agent that uses four layers of enterprise context. Turn each layer on or off and watch the agent get the answer wrong or right.**

| # | Layer | Question it answers for the agent | In this repo |
|---|---|---|---|
| 1 | **Data Catalog** | *Which data should we use?* | `layers/catalog.yaml`: owners, freshness, lineage, certification, glossary |
| 2 | **Semantic Layer** | *Which definition, and how is it calculated?* | `layers/semantic.yaml`: governed metrics compiled to SQL |
| 3 | **Enterprise Ontology** | *What does it mean? What are the rules?* | `layers/ontology.ttl`: RDF / OWL, queried with **rdflib** + SPARQL |
| 4 | **Knowledge Graph** | *What is connected to what?* | **Neo4j**: customers, orders, products, suppliers, warehouses |

Having access to raw data doesn't mean an agent understands the business. This project shows that live.

---

## The question

> **"Why did South India revenue drop in September compared to August?"**

The synthetic warehouse belongs to *Anytime Appliances*, a fictional Indian appliance distributor, and it has a hidden story built in. A supplier delivered ACs and refrigerators to the Chennai distribution centre 12–14 days late, so dealers cancelled orders or didn't reorder. Nothing in the sales table mentions suppliers.

Each layer protects against a different mistake:

| Trap in the data | Without the layer | Layer that fixes it |
|---|---|---|
| Three tables called "sales" (a deprecated backup and an analyst's sandbox) | Uses the wrong table | Data Catalog |
| `SUM(gross_amount)` includes GST and cancelled orders | Reports **−16%** | Semantic Layer (governed revenue: **−34.5%**) |
| Which states make up "South India"? Is Goa included? What is a "key account"? | Guesses | Enterprise Ontology |
| The cause is several hops away: customers → product → supplier → warehouse | Gives numbers but no reason | Knowledge Graph |

## What you get

- **Agent console** (FastAPI + one HTML page)
  - A toggle for each layer
  - A live tool trace, colour-coded by layer
  - An interactive graph (vis-network)
  - Charts (Chart.js)
- **⚔ Compare mode:** the same question with **no layers** vs **all four**, side by side
- **Real multi-hop Cypher**, SPARQL reasoning over is-a and part-of relationships, and SQL compiled from metric definitions

## Quickstart

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and Docker. The LLM can be any OpenAI-compatible endpoint that supports tool calling.

```bash
git clone https://github.com/AIAnytime/Enterprise-Context-Agent.git
cd Enterprise-Context-Agent

# 1. configure the LLM
cat > .env <<'EOF'
OPENAI_API_KEY=your-key
MODEL=gpt-5.4-mini
# OPENAI_BASE_URL=https://your-openai-compatible-gateway/v1   (optional)
EOF

# 2. start Neo4j
docker compose up -d                       # browser: http://localhost:7474  (neo4j / ontology-demo)

# 3. build the data and load the graph
uv run python -m eo.generate               # SQLite warehouse → data/warehouse.db
uv run python -m eo.kg                     # loads Neo4j (region membership comes from the ontology)

# 4. run the console
uv run uvicorn eo.server:app --port 8000   # → http://localhost:8000
```

> `MODEL` defaults to `openai/gpt-5.4-mini`, which is the naming used by OpenRouter-style gateways. When calling OpenAI directly, set `MODEL=gpt-5.4-mini` (or another tool-calling model).
> To change the Neo4j password, add `NEO4J_PASSWORD=...` to `.env`. Docker Compose and the app both read it.

## Try these

| Question | What to watch |
|---|---|
| Why did South India revenue drop in September 2026 compared to August? | All layers on, then press **⚔ Compare** |
| How did our key accounts in the South do month-on-month? | Turn **Ontology** off: the agent no longer knows that key account = Platinum + Gold |
| Is Goa part of South India for our reporting? | The ontology says Goa is **West** |
| Which suppliers are connected to the products whose South India revenue fell the most? | The Knowledge Graph trace draws the path |

## How it works

```
                ┌──────────────── Agent (OpenAI-compatible tool calling) ───────────────┐
 question ───▶  │ tools are grouped by layer; a layer that's switched off loses its tools │
                └───┬───────────────┬────────────────┬─────────────────┬────────────────┘
                    │               │                │                 │
            search_catalog   get_metric        ontology_lookup   trace_relationships
                    │        query_metric            │           run_cypher
                    ▼               ▼                ▼                 ▼
             catalog.yaml    semantic.yaml → SQL   ontology.ttl     Neo4j
                    │               │           (rdflib/SPARQL)       ▲
                    └──── SQLite warehouse ◀────────┴── states/regions ┘
                     (+ raw tools: list_tables, run_sql, always available)
```

The layers depend on each other:

- The **ontology** turns "South India" into state codes.
- The **semantic layer** turns those codes into one governed number and returns the total itself, so the model never adds up rows.
- The **knowledge graph** is loaded using region membership read from the ontology.

```
eo/
  generate.py   synthetic warehouse with the hidden story
  catalog.py    Layer 1: dataset search ranked by certification and status
  semantic.py   Layer 2: metric definitions → SQL, month-on-month comparisons, totals
  ontology.py   Layer 3: rdflib + SPARQL (is-a chains, region members, rules)
  kg.py         Layer 4: Neo4j loader, multi-hop trace, read-only Cypher
  agent.py      tool-calling loop; tools are exposed according to which layers are on
  server.py     FastAPI endpoints + streaming (SSE)
layers/         the three context files: edit these, not the code
web/index.html  the console
```

## Notes

- All data is **synthetic**. Companies, people and numbers are fictional.
- The knowledge graph shows **connections, not proof of cause**, and the agent is told to say so.
- Queries are read-only: `run_sql` allows only `SELECT`/`WITH`, and `run_cypher` rejects write keywords.

## License

**PolyForm Noncommercial 1.0.0.** See [LICENSE](LICENSE).
It's free for personal, educational and research use. **Commercial use requires a separate written agreement with, or prior written consent from, AI Anytime.** For commercial licensing, get in touch via [github.com/AIAnytime](https://github.com/AIAnytime).

---

Built for the **AI Anytime** YouTube channel.
