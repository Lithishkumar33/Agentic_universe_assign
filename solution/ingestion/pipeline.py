"""
Pipeline orchestrator: coordinates ingestion of drops, handles incremental linking,
guarantees idempotency, and maintains data quality statistics.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from solution.crypto import encrypt_pii, hash_phone, mask_phone
from solution.database import DatabaseManager
from solution.ingestion.events import EventProcessor
from solution.ingestion.leads import LeadProcessor
from solution.schema import SchemaCatalog


class IngestionPipeline:
    def __init__(self, db: DatabaseManager, catalog: SchemaCatalog):
        self.db = db
        self.catalog = catalog
        self.lead_processor = LeadProcessor(catalog)
        self.event_processor = EventProcessor(catalog)

    def ingest_drop(self, drop_path: Path | str) -> Dict[str, Any]:
        """
        Ingests a drop directory containing leads/ and events/.
        Fully idempotent: re-running on an already ingested drop changes nothing.
        """
        drop_dir = Path(drop_path).resolve()
        drop_name = drop_dir.name

        # Check idempotency
        if self.db.is_drop_ingested(drop_name):
            print(f"[IngestionPipeline] Drop '{drop_name}' already ingested. Skipping.")
            return {"status": "already_ingested"}

        print(f"[IngestionPipeline] Ingesting drop: {drop_name} from {drop_dir}")

        ingested_at = datetime.now(timezone.utc).isoformat()

        # We execute ingestion within managed transactions
        with self.db.transaction() as conn:
            # 1. Process Leads
            leads_dir = drop_dir / "leads"
            if leads_dir.exists() and leads_dir.is_dir():
                # Preload existing lead identities for O(1) in-memory checks
                cur = conn.execute("SELECT id, workflow_id, phone_hash, batch_id, external_ref, raw_fields_json, name_encrypted FROM leads")
                lead_cache: Dict[Tuple[str, str], dict] = {
                    (r["workflow_id"], r["phone_hash"]): dict(r) for r in cur.fetchall()
                }

                # Process workflows in sorted order
                for wf_dir in sorted(leads_dir.iterdir()):
                    if not wf_dir.is_dir():
                        continue
                    wf_id = wf_dir.name
                    schema = self.catalog.get_schema(wf_id)
                    if not schema:
                        continue

                    # Process CSV batches in filename order
                    for csv_file in sorted(wf_dir.glob("*.csv")):
                        (
                            valid_rows,
                            rejections,
                            total_rows,
                            rejected_cnt,
                            warnings_cnt,
                        ) = self.lead_processor.process_lead_file(csv_file, wf_id, drop_name)

                        # Update data quality counters
                        self.db.increment_stat(conn, "lead_rows_total", total_rows)
                        self.db.increment_stat(conn, "lead_rows_rejected", rejected_cnt)
                        self.db.increment_stat(conn, "field_warnings", warnings_cnt)

                        # Record rejections
                        if rejections:
                            conn.executemany(
                                """
                                INSERT OR IGNORE INTO lead_rejections
                                (drop_name, workflow_id, batch_id, row_number, reason)
                                VALUES (:drop_name, :workflow_id, :batch_id, :row_number, :reason);
                                """,
                                rejections,
                            )

                        # Ingest and merge valid leads
                        for row in valid_rows:
                            self._merge_or_insert_lead_cached(conn, row, lead_cache)

            # 2. Reconcile previously unlinked orphan events with newly inserted leads
            self._reconcile_orphan_events(conn)

            # 3. Process Events
            events_dir = drop_dir / "events"
            if events_dir.exists() and events_dir.is_dir():
                # Preload existing event_ids for fast duplicate check
                cur = conn.execute("SELECT event_id FROM interaction_ledger")
                seen_event_ids: Set[str] = {r[0] for r in cur.fetchall()}

                for event_file in sorted(events_dir.glob("*.jsonl")):
                    (
                        parsed_events,
                        total_lines,
                        malformed_cnt,
                        duplicate_cnt,
                    ) = self.event_processor.process_event_file(event_file, drop_name, seen_event_ids)

                    self.db.increment_stat(conn, "events_total", total_lines)
                    self.db.increment_stat(conn, "malformed_events", malformed_cnt)
                    self.db.increment_stat(conn, "duplicate_events_dropped", duplicate_cnt)

                    if parsed_events:
                        self._insert_events_to_ledger(conn, parsed_events)

            # 4. Final reconciliation for call_ids across drops / out of order overrides
            self._reconcile_orphan_events(conn)

            # 5. Mark drop as successfully ingested
            self.db.record_drop_ingested(conn, drop_name, ingested_at)

        print(f"[IngestionPipeline] Drop '{drop_name}' ingested successfully.")
        return {"status": "success"}

    def _merge_or_insert_lead_cached(self, conn, row_dict: dict, lead_cache: Dict[Tuple[str, str], dict]):
        wf_id = row_dict["workflow_id"]
        batch_id = row_dict["batch_id"]
        fields = row_dict["fields"]
        schema = row_dict["schema"]

        canonical_phone = fields["phone"]
        p_hash = hash_phone(canonical_phone)
        identity = (wf_id, p_hash)

        now_str = datetime.now(timezone.utc).isoformat()

        if identity in lead_cache:
            # Merged duplicate!
            self.db.increment_stat(conn, "duplicate_lead_rows_merged", 1)
            existing = lead_cache[identity]
            lead_id = existing["id"]
            existing_raw = json.loads(existing["raw_fields_json"])

            # Overwrite only non-null fields (last write wins)
            for k, v in fields.items():
                if v is not None:
                    existing_raw[k] = v

            # Apply defaults to fields that are still null
            merged_fields = dict(existing_raw)
            for fspec in schema.fields:
                if merged_fields.get(fspec.name) is None and fspec.default is not None:
                    merged_fields[fspec.name] = fspec.default

            ref_val = merged_fields.get(schema.reference_field, existing["external_ref"])
            cust_name = merged_fields.get("customer_name")
            name_enc = encrypt_pii(cust_name) if cust_name else existing["name_encrypted"]

            conn.execute(
                """
                UPDATE leads
                SET external_ref = ?,
                    fields_json = ?,
                    raw_fields_json = ?,
                    name_encrypted = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    ref_val,
                    json.dumps(merged_fields),
                    json.dumps(existing_raw),
                    name_enc,
                    now_str,
                    lead_id,
                ),
            )
            # Update cache entry
            existing["external_ref"] = ref_val
            existing["raw_fields_json"] = json.dumps(existing_raw)
            existing["name_encrypted"] = name_enc
        else:
            # First valid row for this identity creates the lead
            raw_fields = {k: v for k, v in fields.items() if v is not None}
            final_fields = dict(raw_fields)
            for fspec in schema.fields:
                if final_fields.get(fspec.name) is None and fspec.default is not None:
                    final_fields[fspec.name] = fspec.default

            ref_val = final_fields.get(schema.reference_field, "")
            cust_name = final_fields.get("customer_name", "")

            p_masked = mask_phone(canonical_phone)
            p_enc = encrypt_pii(canonical_phone)
            name_enc = encrypt_pii(cust_name)

            cur = conn.execute(
                """
                INSERT INTO leads
                (workflow_id, phone_hash, phone_masked, phone_encrypted, name_encrypted,
                 batch_id, external_ref, fields_json, raw_fields_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    wf_id,
                    p_hash,
                    p_masked,
                    p_enc,
                    name_enc,
                    batch_id,
                    ref_val,
                    json.dumps(final_fields),
                    json.dumps(raw_fields),
                    now_str,
                    now_str,
                ),
            )
            new_id = cur.lastrowid
            lead_cache[identity] = {
                "id": new_id,
                "workflow_id": wf_id,
                "phone_hash": p_hash,
                "batch_id": batch_id,
                "external_ref": ref_val,
                "raw_fields_json": json.dumps(raw_fields),
                "name_encrypted": name_enc,
            }

    def _insert_events_to_ledger(self, conn, events: List[dict]):
        """
        Inserts clean events into interaction_ledger and links to leads.
        """
        # Resolve lead_id where possible
        cur = conn.execute("SELECT id, workflow_id, phone_hash FROM leads")
        lead_map = {(r["workflow_id"], r["phone_hash"]): r["id"] for r in cur.fetchall()}

        # Also get call_id to lead_id map for events without phone
        cur = conn.execute("SELECT call_id, lead_id FROM interaction_ledger WHERE lead_id IS NOT NULL")
        call_lead_map = {r["call_id"]: r["lead_id"] for r in cur.fetchall()}

        ledger_rows = []
        for ev in events:
            wf_id = ev["workflow_id"]
            p_hash = ev["phone_hash"]
            call_id = ev["call_id"]

            lead_id: Optional[int] = None
            if p_hash and (wf_id, p_hash) in lead_map:
                lead_id = lead_map[(wf_id, p_hash)]
                call_lead_map[call_id] = lead_id
            elif call_id in call_lead_map:
                lead_id = call_lead_map[call_id]

            ledger_rows.append({
                "event_id": ev["event_id"],
                "event_type": ev["event_type"],
                "workflow_id": wf_id,
                "call_id": call_id,
                "occurred_at_raw": ev["occurred_at_raw"],
                "occurred_at_ist": ev["occurred_at_ist"],
                "occurred_date_ist": ev["occurred_date_ist"],
                "occurred_epoch": ev["occurred_epoch"],
                "phone_hash": p_hash,
                "lead_id": lead_id,
                "disposition_code": ev["disposition_code"],
                "is_valid_disposition": ev["is_valid_disposition"],
                "payload_json": ev["payload_json"],
                "drop_name": ev["drop_name"],
            })

        conn.executemany(
            """
            INSERT OR IGNORE INTO interaction_ledger
            (event_id, event_type, workflow_id, call_id, occurred_at_raw, occurred_at_ist,
             occurred_date_ist, occurred_epoch, phone_hash, lead_id, disposition_code,
             is_valid_disposition, payload_json, drop_name)
            VALUES (:event_id, :event_type, :workflow_id, :call_id, :occurred_at_raw, :occurred_at_ist,
                    :occurred_date_ist, :occurred_epoch, :phone_hash, :lead_id, :disposition_code,
                    :is_valid_disposition, :payload_json, :drop_name);
            """,
            ledger_rows,
        )

    def _reconcile_orphan_events(self, conn):
        """
        Reconciles unlinked events against leads:
        1. Link unlinked events with phone_hash to matching leads
        2. Link unlinked events by call_id where other events of that call have lead_id
        """
        # Link by phone_hash
        conn.execute(
            """
            UPDATE interaction_ledger
            SET lead_id = (
                SELECT leads.id
                FROM leads
                WHERE leads.workflow_id = interaction_ledger.workflow_id
                  AND leads.phone_hash = interaction_ledger.phone_hash
            )
            WHERE lead_id IS NULL AND phone_hash IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM leads
                  WHERE leads.workflow_id = interaction_ledger.workflow_id
                    AND leads.phone_hash = interaction_ledger.phone_hash
              );
            """
        )

        # Link overrides or unlinked events by call_id
        conn.execute(
            """
            UPDATE interaction_ledger
            SET lead_id = (
                SELECT l2.lead_id
                FROM interaction_ledger l2
                WHERE l2.call_id = interaction_ledger.call_id
                  AND l2.lead_id IS NOT NULL
                LIMIT 1
            )
            WHERE lead_id IS NULL
              AND EXISTS (
                  SELECT 1 FROM interaction_ledger l2
                  WHERE l2.call_id = interaction_ledger.call_id
                    AND l2.lead_id IS NOT NULL
              );
            """
        )
