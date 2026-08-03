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
    """Run db/research_schema.sql to create all tables (idempotent — every
    CREATE TABLE/INDEX/VIEW statement is IF NOT EXISTS). Then apply any
    column-add migrations that CREATE TABLE IF NOT EXISTS can't express —
    those need their own existence check since SQLite's ALTER TABLE ADD
    COLUMN has no IF NOT EXISTS form."""
    conn = get_research_connection()
    schema_path = os.path.join(os.path.dirname(__file__), "research_schema.sql")
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    _add_column_if_missing(conn, "research_decisions", "market_context_id",
                            "INTEGER REFERENCES market_context(id)")
    conn.commit()
    conn.close()


def _add_column_if_missing(conn, table, column, column_def):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
