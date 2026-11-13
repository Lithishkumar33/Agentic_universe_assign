"""
Database manager and storage schema for SQLite.
Ensures ACID transactions, WAL mode performance, and schema constraints.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from solution.config import DEFAULT_DB_PATH

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA cache_size = -64000;
PRAGMA temp_store = MEMORY;

-- Tracks ingested drops for idempotency
CREATE TABLE IF NOT EXISTS ingested_drops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_name TEXT UNIQUE NOT NULL,
    ingested_at TEXT NOT NULL
);

-- Tracks individual files ingested
CREATE TABLE IF NOT EXISTS ingested_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_name TEXT NOT NULL,
    file_path TEXT UNIQUE NOT NULL,
    file_type TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    ingested_at TEXT NOT NULL
);

-- Validated leads (PII protected at rest)
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    phone_hash TEXT NOT NULL,
    phone_masked TEXT NOT NULL,
    phone_encrypted TEXT NOT NULL,
    name_encrypted TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    raw_fields_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workflow_id, phone_hash)
);

CREATE INDEX IF NOT EXISTS idx_leads_identity ON leads(workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS idx_leads_ref ON leads(workflow_id, external_ref);
CREATE INDEX IF NOT EXISTS idx_leads_batch ON leads(workflow_id, batch_id);

-- Rejected lead rows (No PII)
CREATE TABLE IF NOT EXISTS lead_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drop_name TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    UNIQUE(drop_name, workflow_id, batch_id, row_number)
);

CREATE INDEX IF NOT EXISTS idx_rejections_sort ON lead_rejections(workflow_id, batch_id, row_number);

-- Append-only interaction ledger
CREATE TABLE IF NOT EXISTS interaction_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    occurred_at_raw TEXT NOT NULL,
    occurred_at_ist TEXT NOT NULL,
    occurred_date_ist TEXT NOT NULL,
    occurred_epoch REAL NOT NULL,
    phone_hash TEXT,
    lead_id INTEGER REFERENCES leads(id),
    disposition_code TEXT,
    is_valid_disposition INTEGER,
    payload_json TEXT NOT NULL,
    drop_name TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_call_id ON interaction_ledger(call_id);
CREATE INDEX IF NOT EXISTS idx_ledger_lead_id ON interaction_ledger(lead_id);
CREATE INDEX IF NOT EXISTS idx_ledger_phone_hash ON interaction_ledger(workflow_id, phone_hash);
CREATE INDEX IF NOT EXISTS idx_ledger_occurred_date ON interaction_ledger(workflow_id, occurred_date_ist);
CREATE INDEX IF NOT EXISTS idx_ledger_call_type ON interaction_ledger(workflow_id, event_type, lead_id);

-- Pipeline data quality stats tracking
CREATE TABLE IF NOT EXISTS pipeline_stats (
    stat_key TEXT PRIMARY KEY,
    stat_value INTEGER NOT NULL
);
"""


class DatabaseManager:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None  # autocommit mode, transactions handled explicitly
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        conn = self.get_connection()
        conn.execute("BEGIN IMMEDIATE;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)

    def is_drop_ingested(self, drop_name: str) -> bool:
        with self.get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM ingested_drops WHERE drop_name = ?", (drop_name,))
            return cur.fetchone() is not None

    def record_drop_ingested(self, conn: sqlite3.Connection, drop_name: str, ingested_at: str):
        conn.execute(
            "INSERT INTO ingested_drops (drop_name, ingested_at) VALUES (?, ?)",
            (drop_name, ingested_at)
        )

    def get_stat(self, key: str, default: int = 0) -> int:
        with self.get_connection() as conn:
            cur = conn.execute("SELECT stat_value FROM pipeline_stats WHERE stat_key = ?", (key,))
            row = cur.fetchone()
            return row["stat_value"] if row else default

    def increment_stat(self, conn: sqlite3.Connection, key: str, amount: int = 1):
        conn.execute(
            """
            INSERT INTO pipeline_stats (stat_key, stat_value)
            VALUES (?, ?)
            ON CONFLICT(stat_key) DO UPDATE SET stat_value = stat_value + ?;
            """,
            (key, amount, amount)
        )

    def set_stat(self, conn: sqlite3.Connection, key: str, value: int):
        conn.execute(
            """
            INSERT INTO pipeline_stats (stat_key, stat_value)
            VALUES (?, ?)
            ON CONFLICT(stat_key) DO UPDATE SET stat_value = ?;
            """,
            (key, value, value)
        )
