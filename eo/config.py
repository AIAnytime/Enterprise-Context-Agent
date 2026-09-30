import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT / ".env")

LAYERS = ROOT / "layers"
DB_PATH = ROOT / "data" / "warehouse.db"

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "ontology-demo")

MODEL = os.getenv("MODEL", "openai/gpt-5.4-mini")
TODAY = "2026-09-30"
