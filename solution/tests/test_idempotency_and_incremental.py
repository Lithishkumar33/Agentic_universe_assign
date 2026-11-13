from __future__ import annotations

import csv
import json
import pytest
from pathlib import Path

from solution.database import DatabaseManager
from solution.ingestion.pipeline import IngestionPipeline
from solution.reporting.exporter import export_reports
from solution.schema import SchemaCatalog


class TestIdempotencyAndIncremental:
    def test_drop_idempotency(self, tmp_path):
        db_path = tmp_path / "test_idem.db"
        schemas_dir = Path(__file__).resolve().parent.parent.parent / "assignment-a-data" / "schemas"
        catalog = SchemaCatalog(schemas_dir)
        db = DatabaseManager(db_path)
        pipeline = IngestionPipeline(db, catalog)

        # Create dummy drop
        drop_dir = tmp_path / "drop_1"
        leads_dir = drop_dir / "leads" / "motor_renewal"
        leads_dir.mkdir(parents=True, exist_ok=True)
        events_dir = drop_dir / "events"
        events_dir.mkdir(parents=True, exist_ok=True)

        csv_file = leads_dir / "batch_001.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            f.write("customer_name,phone,policy_number,preferred_language\n")
            f.write("Aarav Sharma,+919876543210,MOT/2026/100001,en\n")

        jsonl_file = events_dir / "events_0001.jsonl"
        with open(jsonl_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "event_id": "evt_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_01",
                "occurred_at": "2026-10-01T10:00:00Z",
                "phone": "+919876543210",
                "payload": {}
            }) + "\n")

        # Ingest first time
        res1 = pipeline.ingest_drop(drop_dir)
        assert res1["status"] == "success"

        # Ingest second time -> must be skipped
        res2 = pipeline.ingest_drop(drop_dir)
        assert res2["status"] == "already_ingested"

        # Stats should not have doubled
        assert db.get_stat("lead_rows_total") == 1
        assert db.get_stat("events_total") == 1

    def test_orphan_event_linked_when_lead_arrives_later(self, tmp_path):
        db_path = tmp_path / "test_orphan.db"
        schemas_dir = Path(__file__).resolve().parent.parent.parent / "assignment-a-data" / "schemas"
        catalog = SchemaCatalog(schemas_dir)
        db = DatabaseManager(db_path)
        pipeline = IngestionPipeline(db, catalog)

        # Drop 1: contains event for +919876543210, but NO lead!
        drop1 = tmp_path / "drop_1"
        (drop1 / "events").mkdir(parents=True, exist_ok=True)
        with open(drop1 / "events" / "events.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "event_id": "evt_orphan_01",
                "event_type": "call.initiated",
                "workflow_id": "motor_renewal",
                "call_id": "call_orphan_01",
                "occurred_at": "2026-10-01T10:00:00Z",
                "phone": "+919876543210",
                "payload": {}
            }) + "\n")

        pipeline.ingest_drop(drop1)

        # In drop 1, event is an orphan
        with db.get_connection() as conn:
            cur = conn.execute("SELECT lead_id FROM interaction_ledger WHERE event_id = 'evt_orphan_01'")
            assert cur.fetchone()["lead_id"] is None

        # Drop 2: lead arrives!
        drop2 = tmp_path / "drop_2"
        (drop2 / "leads" / "motor_renewal").mkdir(parents=True, exist_ok=True)
        with open(drop2 / "leads" / "motor_renewal" / "batch_002.csv", "w", newline="", encoding="utf-8") as f:
            f.write("customer_name,phone,policy_number,preferred_language\n")
            f.write("Aarav Sharma,+919876543210,MOT/2026/100001,en\n")

        pipeline.ingest_drop(drop2)

        # After drop 2, event must be linked to lead!
        with db.get_connection() as conn:
            cur = conn.execute("SELECT lead_id FROM interaction_ledger WHERE event_id = 'evt_orphan_01'")
            lead_id = cur.fetchone()["lead_id"]
            assert lead_id is not None
            cur_lead = conn.execute("SELECT phone_masked FROM leads WHERE id = ?", (lead_id,))
            assert cur_lead.fetchone()["phone_masked"] == "+91******3210"
