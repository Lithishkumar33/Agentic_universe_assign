"""
Report exporter: writes the five report files matching the exact specification contract.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from solution.database import DatabaseManager
from solution.reporting.metrics import MetricsEngine
from solution.schema import SchemaCatalog


def write_csv_file(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})


def export_reports(db: DatabaseManager, catalog: SchemaCatalog, out_dir: Path | str):
    """
    Computes and writes all five report files to out_dir:
    1. batch_summary.csv
    2. daily_overview.csv
    3. lead_report.csv
    4. rejections.csv
    5. data_quality.json
    """
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    engine = MetricsEngine(db, catalog)

    with db.get_connection() as conn:
        call_facts = engine.compute_all_call_facts(conn)
        lead_facts = engine.compute_lead_facts(conn, call_facts)

        # 1. batch_summary.csv
        batch_summary_rows = engine.generate_batch_summary(lead_facts)
        write_csv_file(
            out_path / "batch_summary.csv",
            batch_summary_rows,
            [
                "workflow_id",
                "batch_id",
                "total_leads",
                "yet_to_reach_out",
                "in_progress_telephony_failed",
                "in_progress_dialed",
                "completed",
            ],
        )

        # 2. daily_overview.csv
        daily_overview_rows = engine.generate_daily_overview(call_facts)
        write_csv_file(
            out_path / "daily_overview.csv",
            daily_overview_rows,
            [
                "workflow_id",
                "date",
                "total_calls",
                "connected_calls",
                "connectivity_rate",
                "success_leads",
            ],
        )

        # 3. lead_report.csv
        write_csv_file(
            out_path / "lead_report.csv",
            lead_facts,
            [
                "workflow_id",
                "external_ref",
                "batch_id",
                "phone_masked",
                "bucket",
                "total_calls",
                "connected_calls",
                "last_call_at",
                "current_disposition",
                "ever_success",
            ],
        )

        # 4. rejections.csv
        rejections_rows = engine.generate_rejections_report(conn)
        write_csv_file(
            out_path / "rejections.csv",
            rejections_rows,
            ["workflow_id", "batch_id", "row_number", "reason"],
        )

        # 5. data_quality.json
        dq_data = engine.generate_data_quality_report(conn)
        with open(out_path / "data_quality.json", "w", encoding="utf-8") as f:
            json.dump(dq_data, f, indent=2)

    print(f"[ReportExporter] Successfully generated all 5 reports into {out_path}/")
