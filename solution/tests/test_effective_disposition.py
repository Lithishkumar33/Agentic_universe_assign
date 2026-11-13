from __future__ import annotations

import pytest
from pathlib import Path
from solution.database import DatabaseManager
from solution.reporting.metrics import MetricsEngine
from solution.schema import SchemaCatalog


class TestEffectiveDisposition:
    @pytest.fixture
    def setup_engine(self, tmp_path):
        db_path = tmp_path / "test_disp.db"
        schemas_dir = Path(__file__).resolve().parent.parent.parent / "assignment-a-data" / "schemas"
        catalog = SchemaCatalog(schemas_dir)
        db = DatabaseManager(db_path)
        engine = MetricsEngine(db, catalog)
        return db, engine

    def test_effective_disposition_precedence(self, setup_engine):
        db, engine = setup_engine

        with db.transaction() as conn:
            # Insert test lead
            conn.execute(
                """
                INSERT INTO leads (id, workflow_id, phone_hash, phone_masked, phone_encrypted,
                                  name_encrypted, batch_id, external_ref, fields_json, raw_fields_json,
                                  created_at, updated_at)
                VALUES (1, 'motor_renewal', 'hash1', '+91******1234', 'enc1', 'encname',
                        'batch_001', 'REF001', '{}', '{}', '2026-10-01', '2026-10-01');
                """
            )

            # Call 1: has disposition.set = 'callback_requested'
            conn.execute(
                """
                INSERT INTO interaction_ledger
                (event_id, event_type, workflow_id, call_id, occurred_at_raw, occurred_at_ist,
                 occurred_date_ist, occurred_epoch, lead_id, disposition_code, is_valid_disposition,
                 payload_json, drop_name)
                VALUES
                ('e1', 'call.initiated', 'motor_renewal', 'c1', '2026-10-01 10:00:00', '2026-10-01T10:00:00+05:30', '2026-10-01', 1000.0, 1, NULL, NULL, '{}', 'drop_1'),
                ('e2', 'call.connected', 'motor_renewal', 'c1', '2026-10-01 10:00:10', '2026-10-01T10:00:10+05:30', '2026-10-01', 1010.0, 1, NULL, NULL, '{}', 'drop_1'),
                ('e3', 'disposition.set', 'motor_renewal', 'c1', '2026-10-01 10:02:00', '2026-10-01T10:02:00+05:30', '2026-10-01', 1120.0, 1, 'callback_requested', 1, '{}', 'drop_1');
                """
            )

            # Call 2: has disposition.set = 'callback_requested', then override = 'renewed'
            conn.execute(
                """
                INSERT INTO interaction_ledger
                (event_id, event_type, workflow_id, call_id, occurred_at_raw, occurred_at_ist,
                 occurred_date_ist, occurred_epoch, lead_id, disposition_code, is_valid_disposition,
                 payload_json, drop_name)
                VALUES
                ('e4', 'call.initiated', 'motor_renewal', 'c2', '2026-10-02 10:00:00', '2026-10-02T10:00:00+05:30', '2026-10-02', 2000.0, 1, NULL, NULL, '{}', 'drop_1'),
                ('e5', 'disposition.set', 'motor_renewal', 'c2', '2026-10-02 10:05:00', '2026-10-02T10:05:00+05:30', '2026-10-02', 2300.0, 1, 'callback_requested', 1, '{}', 'drop_1'),
                ('e6', 'disposition.override', 'motor_renewal', 'c2', '2026-10-02 12:00:00', '2026-10-02T12:00:00+05:30', '2026-10-02', 3000.0, 1, 'renewed', 1, '{}', 'drop_1');
                """
            )

            # Call 3: override has unknown disposition code -> should be ignored, falling back to disposition.set
            conn.execute(
                """
                INSERT INTO interaction_ledger
                (event_id, event_type, workflow_id, call_id, occurred_at_raw, occurred_at_ist,
                 occurred_date_ist, occurred_epoch, lead_id, disposition_code, is_valid_disposition,
                 payload_json, drop_name)
                VALUES
                ('e7', 'call.initiated', 'motor_renewal', 'c3', '2026-10-03 10:00:00', '2026-10-03T10:00:00+05:30', '2026-10-03', 4000.0, 1, NULL, NULL, '{}', 'drop_1'),
                ('e8', 'disposition.set', 'motor_renewal', 'c3', '2026-10-03 10:05:00', '2026-10-03T10:05:00+05:30', '2026-10-03', 4300.0, 1, 'not_interested', 1, '{}', 'drop_1'),
                ('e9', 'disposition.override', 'motor_renewal', 'c3', '2026-10-03 12:00:00', '2026-10-03T12:00:00+05:30', '2026-10-03', 5000.0, 1, 'UNKNOWN_CODE', 0, '{}', 'drop_1');
                """
            )

        with db.get_connection() as conn:
            facts = engine.compute_all_call_facts(conn)
            assert facts["c1"].effective_disposition == "callback_requested"
            assert facts["c1"].is_terminal is False
            assert facts["c1"].is_success is False

            assert facts["c2"].effective_disposition == "renewed"
            assert facts["c2"].is_terminal is True
            assert facts["c2"].is_success is True

            assert facts["c3"].effective_disposition == "not_interested"
            assert facts["c3"].is_terminal is True
            assert facts["c3"].is_success is False
