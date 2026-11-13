"""
Command-line interface for the Interaction Ledger & Reporting Pipeline.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path so solution can be run directly
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from solution.config import DEFAULT_DB_PATH, SCHEMAS_DIR
from solution.database import DatabaseManager
from solution.ingestion.pipeline import IngestionPipeline
from solution.reporting.exporter import export_reports
from solution.schema import SchemaCatalog


def main():
    parser = argparse.ArgumentParser(
        description="Agentic Universe - Interaction Ledger & Reporting Pipeline"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database file (default: ledger.db in repo root)",
    )
    parser.add_argument(
        "--schemas",
        type=Path,
        default=SCHEMAS_DIR,
        help="Path to schemas directory (default: assignment-a-data/schemas)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest subcommand
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a drop of leads and events")
    ingest_parser.add_argument(
        "drop_dir",
        type=Path,
        help="Path to drop folder containing leads/ and events/ (e.g. data/drop_1)",
    )

    # Report subcommand
    report_parser = subparsers.add_parser("report", help="Generate all 5 reporting files")
    report_parser.add_argument(
        "--out",
        type=Path,
        default=Path("reports"),
        help="Output directory for generated reports (default: reports)",
    )

    args = parser.parse_args()

    # Initialise catalog and database
    catalog = SchemaCatalog(args.schemas)
    db = DatabaseManager(args.db)

    if args.command == "ingest":
        pipeline = IngestionPipeline(db, catalog)
        pipeline.ingest_drop(args.drop_dir)

    elif args.command == "report":
        export_reports(db, catalog, args.out)


if __name__ == "__main__":
    main()
