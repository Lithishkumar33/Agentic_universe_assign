from __future__ import annotations

import csv
import json
import pytest
from pathlib import Path

from solution.database import DatabaseManager
from solution.ingestion.pipeline import IngestionPipeline
from solution.reporting.exporter import export_reports
from solution.schema import SchemaCatalog


class TestEndToEnd:
    def test_pipeline_on_generated_data(self, tmp_path):
        repo_root = Path(__file__).resolve().parent.parent.parent
        data_dir = repo_root / "data"

        if not (data_dir / "drop_1").exists() or not (data_dir / "drop_2").exists():
            pytest.skip("data/drop_1 and data/drop_2 not found; skipping full dataset test")

        db_path = tmp_path / "e2e_test.db"
        schemas_dir = repo_root / "assignment-a-data" / "schemas"
        out_dir = tmp_path / "reports_e2e"

        catalog = SchemaCatalog(schemas_dir)
        db = DatabaseManager(db_path)
        pipeline = IngestionPipeline(db, catalog)

        # Ingest drops
        pipeline.ingest_drop(data_dir / "drop_1")
        pipeline.ingest_drop(data_dir / "drop_2")

        # Generate reports
        export_reports(db, catalog, out_dir)

        # Verify all 5 files exist
        assert (out_dir / "batch_summary.csv").exists()
        assert (out_dir / "daily_overview.csv").exists()
        assert (out_dir / "lead_report.csv").exists()
        assert (out_dir / "rejections.csv").exists()
        assert (out_dir / "data_quality.json").exists()

        # Check batch_summary.csv
        with open(out_dir / "batch_summary.csv", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert len(reader) > 0
            for r in reader:
                tot = int(r["total_leads"])
                sum_buckets = (
                    int(r["yet_to_reach_out"])
                    + int(r["in_progress_telephony_failed"])
                    + int(r["in_progress_dialed"])
                    + int(r["completed"])
                )
                assert tot == sum_buckets

        # Check daily_overview.csv
        with open(out_dir / "daily_overview.csv", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert len(reader) > 0
            for r in reader:
                tot_c = int(r["total_calls"])
                conn_c = int(r["connected_calls"])
                assert conn_c <= tot_c
                rate = float(r["connectivity_rate"])
                assert 0.0 <= rate <= 1.0
                assert len(r["connectivity_rate"].split(".")[1]) == 4

        # Check lead_report.csv
        with open(out_dir / "lead_report.csv", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert len(reader) > 0
            for r in reader:
                assert r["phone_masked"].startswith("+91******")
                assert len(r["phone_masked"]) == 13
                assert r["ever_success"] in ("true", "false")
                assert r["bucket"] in (
                    "yet_to_reach_out",
                    "completed",
                    "in_progress_telephony_failed",
                    "in_progress_dialed",
                )

        # Check rejections.csv
        with open(out_dir / "rejections.csv", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            for r in reader:
                assert int(r["row_number"]) >= 1
                assert r["reason"]

        # Check data_quality.json
        with open(out_dir / "data_quality.json", encoding="utf-8") as f:
            dq = json.load(f)
            assert dq["lead_rows_total"] > 0
            assert dq["events_total"] > 0
            sum_reasons = sum(dq["rejections_by_reason"].values())
            assert sum_reasons == dq["lead_rows_rejected"]
