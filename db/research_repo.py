"""
Research Intelligence Platform DB access (docs/48 + docs/49) — physically
separate SQLite file from the live-trading DB (config.settings.DB_PATH).
Mirrors db/repository.py's get_connection()/init_db() convention.
"""

import os
import sqlite3

from config.settings import RESEARCH_DB_PATH


def get_research_connection():
    conn = sqlite3.connect(RESEARCH_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_research_db():
    """Run db/research_schema.sql to create Phase 1 tables (idempotent — all
    CREATE TABLE/INDEX statements are IF NOT EXISTS)."""
    conn = get_research_connection()
    schema_path = os.path.join(os.path.dirname(__file__), "research_schema.sql")
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
