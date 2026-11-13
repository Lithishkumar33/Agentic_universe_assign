from __future__ import annotations

import pytest
from pathlib import Path
from solution.database import DatabaseManager
from solution.reporting.metrics import MetricsEngine
from solution.schema import SchemaCatalog


class TestBucketLogic:
    @pytest.fixture
    def setup_engine(self, tmp_path):
        db_path = tmp_path / "test_buckets.db"
        schemas_dir = Path(__file__).resolve().parent.parent.parent / "assignment-a-data" / "schemas"
        catalog = SchemaCatalog(schemas_dir)
        db = DatabaseManager(db_path)
        engine = MetricsEngine(db, catalog)
        return db, engine

    def test_all_four_buckets(self, setup_engine):
        db, engine = setup_engine

        with db.transaction() as conn:
            # Lead 1: 0 calls -> yet_to_reach_out
            conn.execute(
                """
                INSERT INTO leads (id, workflow_id, phone_hash, phone_masked, phone_encrypted,
                                  name_encrypted, batch_id, external_ref, fields_json, raw_fields_json,
                                  created_at, updated_at)
                VALUES (1, 'motor_renewal', 'h1', '+91******0001', 'e1', 'n1', 'b1', 'REF1', '{}', '{}', '2026-10-01', '2026-10-01');
                """
            )

            # Lead 2: has terminal disposition -> completed
            conn.execute(
                """
                INSERT INTO leads (id, workflow_id, phone_hash, phone_masked, phone_encrypted,
                                  name_encrypted, batch_id, external_ref, fields_json, raw_fields_json,
                                  created_at, updated_at)
                VALUES (2, 'motor_renewal', 'h2', '+91******0002', 'e2', 'n2', 'b1', 'REF2', '{}', '{}', '2026-10-01', '2026-10-01');
                """
            )
            conn.execute(
                """
                INSERT INTO interaction_ledger
                (event_id, event_type, workflow_id, call_id, occurred_at_raw, occurred_at_ist,
                 occurred_date_ist, occurred_epoch, lead_id, disposition_code, is_valid_disposition,
                 payload_json, drop_name)
                VALUES
                ('ev2_1', 'call.initiated', 'motor_renewal', 'c2', '2026-10-01 10:00:00', '2026-10-01T10:00:00+05:30', '2026-10-01', 1000.0, 2, NULL, NULL, '{}', 'd1'),
                ('ev2_2', 'disposition.set', 'motor_renewal', 'c2', '2026-10-01 10:05:00', '2026-10-01T10:05:00+05:30', '2026-10-01', 1300.0, 2, 'renewed', 1, '{}', 'd1');
                """
            )

            # Lead 3: 2 calls, both telephony_failed -> in_progress_telephony_failed
            conn.execute(
                """
                INSERT INTO leads (id, workflow_id, phone_hash, phone_masked, phone_encrypted,
                                  name_encrypted, batch_id, external_ref, fields_json, raw_fields_json,
                                  created_at, updated_at)
                VALUES (3, 'motor_renewal', 'h3', '+91******0003', 'e3', 'n3', 'b1', 'REF3', '{}', '{}', '2026-10-01', '2026-10-01');
                """
            )
            conn.execute(
                """
                INSERT INTO interaction_ledger
                (event_id, event_type, workflow_id, call_id, occurred_at_raw, occurred_at_ist,
                 occurred_date_ist, occurred_epoch, lead_id, disposition_code, is_valid_disposition,
                 payload_json, drop_name)
                VALUES
                ('ev3_1', 'call.initiated', 'motor_renewal', 'c3_1', '2026-10-01 10:00:00', '2026-10-01T10:00:00+05:30', '2026-10-01', 2000.0, 3, NULL, NULL, '{}', 'd1'),
                ('ev3_2', 'call.telephony_failed', 'motor_renewal', 'c3_1', '2026-10-01 10:00:05', '2026-10-01T10:00:05+05:30', '2026-10-01', 2005.0, 3, NULL, NULL, '{}', 'd1'),
                ('ev3_3', 'call.initiated', 'motor_renewal', 'c3_2', '2026-10-02 10:00:00', '2026-10-02T10:00:00+05:30', '2026-10-02', 3000.0, 3, NULL, NULL, '{}', 'd1'),
                ('ev3_4', 'call.telephony_failed', 'motor_renewal', 'c3_2', '2026-10-02 10:00:05', '2026-10-02T10:00:05+05:30', '2026-10-02', 3005.0, 3, NULL, NULL, '{}', 'd1');
                """
            )

            # Lead 4: 1 call telephony failed, 1 call dialed with non-terminal disposition -> in_progress_dialed
            conn.execute(
                """
                INSERT INTO leads (id, workflow_id, phone_hash, phone_masked, phone_encrypted,
                                  name_encrypted, batch_id, external_ref, fields_json, raw_fields_json,
                                  created_at, updated_at)
                VALUES (4, 'motor_renewal', 'h4', '+91******0004', 'e4', 'n4', 'b1', 'REF4', '{}', '{}', '2026-10-01', '2026-10-01');
                """
            )
            conn.execute(
                """
                INSERT INTO interaction_ledger
                (event_id, event_type, workflow_id, call_id, occurred_at_raw, occurred_at_ist,
                 occurred_date_ist, occurred_epoch, lead_id, disposition_code, is_valid_disposition,
                 payload_json, drop_name)
                VALUES
                ('ev4_1', 'call.initiated', 'motor_renewal', 'c4_1', '2026-10-01 10:00:00', '2026-10-01T10:00:00+05:30', '2026-10-01', 4000.0, 4, NULL, NULL, '{}', 'd1'),
                ('ev4_2', 'call.telephony_failed', 'motor_renewal', 'c4_1', '2026-10-01 10:00:05', '2026-10-01T10:00:05+05:30', '2026-10-01', 4005.0, 4, NULL, NULL, '{}', 'd1'),
                ('ev4_3', 'call.initiated', 'motor_renewal', 'c4_2', '2026-10-02 10:00:00', '2026-10-02T10:00:00+05:30', '2026-10-02', 5000.0, 4, NULL, NULL, '{}', 'd1'),
                ('ev4_4', 'disposition.set', 'motor_renewal', 'c4_2', '2026-10-02 10:05:00', '2026-10-02T10:05:00+05:30', '2026-10-02', 5300.0, 4, 'callback_requested', 1, '{}', 'd1');
                """
            )

        with db.get_connection() as conn:
            call_facts = engine.compute_all_call_facts(conn)
            lead_facts = engine.compute_lead_facts(conn, call_facts)

            buckets = {lf["external_ref"]: lf["bucket"] for lf in lead_facts}
            assert buckets["REF1"] == "yet_to_reach_out"
            assert buckets["REF2"] == "completed"
            assert buckets["REF3"] == "in_progress_telephony_failed"
            assert buckets["REF4"] == "in_progress_dialed"
