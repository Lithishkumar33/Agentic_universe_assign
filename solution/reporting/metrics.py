"""
Metrics engine: computes derived facts, call dispositions, buckets, and report tables.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from solution.database import DatabaseManager
from solution.schema import SchemaCatalog


@dataclass
class CallFact:
    call_id: str
    workflow_id: str
    lead_id: int
    initiated_at: str          # ISO-8601 IST
    initiated_date: str        # YYYY-MM-DD IST
    initiated_epoch: float
    is_connected: bool
    is_telephony_failed: bool
    effective_disposition: Optional[str]
    is_terminal: bool
    is_success: bool


class MetricsEngine:
    def __init__(self, db: DatabaseManager, catalog: SchemaCatalog):
        self.db = db
        self.catalog = catalog

    def compute_all_call_facts(self, conn) -> Dict[str, CallFact]:
        """
        Derives all call-level facts from the append-only ledger:
        1. A call is counted only if its call.initiated event is present and linked.
        2. A call is connected if it has a call.connected event.
        3. A call is telephony_failed if it has a call.telephony_failed event.
        4. Effective disposition: latest disposition.override by occurred_at (if in catalog),
           else latest disposition.set by occurred_at (if in catalog), else None.
        """
        # Fetch all linked events
        cur = conn.execute(
            """
            SELECT event_id, event_type, workflow_id, call_id, lead_id,
                   occurred_at_ist, occurred_date_ist, occurred_epoch,
                   disposition_code, is_valid_disposition
            FROM interaction_ledger
            WHERE lead_id IS NOT NULL
            ORDER BY occurred_epoch ASC, event_id ASC;
            """
        )
        rows = cur.fetchall()

        # Group by call_id
        calls_events: Dict[str, List[sqlite3.Row]] = {}
        for r in rows:
            calls_events.setdefault(r["call_id"], []).append(r)

        call_facts: Dict[str, CallFact] = {}

        for call_id, evs in calls_events.items():
            # Must have call.initiated
            init_events = [e for e in evs if e["event_type"] == "call.initiated"]
            if not init_events:
                continue

            first_init = init_events[0]
            lead_id = first_init["lead_id"]
            wf_id = first_init["workflow_id"]
            init_at = first_init["occurred_at_ist"]
            init_date = first_init["occurred_date_ist"]
            init_epoch = first_init["occurred_epoch"]

            is_conn = any(e["event_type"] == "call.connected" for e in evs)
            is_tel_fail = any(e["event_type"] == "call.telephony_failed" for e in evs)

            # Effective disposition:
            # Check valid disposition.override events
            overrides = [
                e for e in evs
                if e["event_type"] == "disposition.override" and e["is_valid_disposition"] == 1
            ]
            eff_disp: Optional[str] = None
            if overrides:
                # Latest by occurred_epoch
                overrides.sort(key=lambda x: (x["occurred_epoch"], x["event_id"]))
                eff_disp = overrides[-1]["disposition_code"]
            else:
                sets = [
                    e for e in evs
                    if e["event_type"] == "disposition.set" and e["is_valid_disposition"] == 1
                ]
                if sets:
                    sets.sort(key=lambda x: (x["occurred_epoch"], x["event_id"]))
                    eff_disp = sets[-1]["disposition_code"]

            is_terminal = False
            is_success = False
            if eff_disp:
                disp_spec = self.catalog.get_disposition(wf_id, eff_disp)
                if disp_spec:
                    is_terminal = disp_spec.is_terminal
                    is_success = disp_spec.is_success

            call_facts[call_id] = CallFact(
                call_id=call_id,
                workflow_id=wf_id,
                lead_id=lead_id,
                initiated_at=init_at,
                initiated_date=init_date,
                initiated_epoch=init_epoch,
                is_connected=is_conn,
                is_telephony_failed=is_tel_fail,
                effective_disposition=eff_disp,
                is_terminal=is_terminal,
                is_success=is_success,
            )

        return call_facts

    def compute_lead_facts(
        self, conn, call_facts: Dict[str, CallFact]
    ) -> List[Dict[str, Any]]:
        """
        Computes per-lead derived metrics and bucket:
        1. yet_to_reach_out: no calls.
        2. completed: any call's effective disposition is terminal.
        3. in_progress_telephony_failed: every call has a call.telephony_failed event.
        4. in_progress_dialed: everything else.
        """
        # Fetch all leads
        cur = conn.execute(
            """
            SELECT id, workflow_id, batch_id, external_ref, phone_masked
            FROM leads
            ORDER BY workflow_id ASC, external_ref ASC;
            """
        )
        leads = cur.fetchall()

        # Group calls by lead_id
        lead_calls: Dict[int, List[CallFact]] = {}
        for c in call_facts.values():
            lead_calls.setdefault(c.lead_id, []).append(c)

        lead_results = []
        for lead in leads:
            lid = lead["id"]
            calls = lead_calls.get(lid, [])

            # Sort calls by initiated_epoch ascending
            calls.sort(key=lambda c: (c.initiated_epoch, c.call_id))

            tot_calls = len(calls)
            conn_calls = sum(1 for c in calls if c.is_connected)

            # Bucket logic (in strict precedence order)
            if tot_calls == 0:
                bucket = "yet_to_reach_out"
            elif any(c.is_terminal for c in calls):
                bucket = "completed"
            elif all(c.is_telephony_failed for c in calls):
                bucket = "in_progress_telephony_failed"
            else:
                bucket = "in_progress_dialed"

            # last_call_at: latest call.initiated time as ISO-8601 in IST
            last_call_at = calls[-1].initiated_at if calls else ""

            # current_disposition: effective disposition of most recent call that has one
            current_disp = ""
            for c in reversed(calls):
                if c.effective_disposition:
                    current_disp = c.effective_disposition
                    break

            ever_success = any(c.is_success for c in calls)

            lead_results.append({
                "lead_id": lid,
                "workflow_id": lead["workflow_id"],
                "external_ref": lead["external_ref"],
                "batch_id": lead["batch_id"],
                "phone_masked": lead["phone_masked"],
                "bucket": bucket,
                "total_calls": tot_calls,
                "connected_calls": conn_calls,
                "last_call_at": last_call_at,
                "current_disposition": current_disp,
                "ever_success": "true" if ever_success else "false",
            })

        return lead_results

    def generate_batch_summary(self, lead_facts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Produces batch_summary.csv:
        workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,in_progress_dialed,completed
        Sorted by workflow_id, batch_id
        """
        batches: Dict[Tuple[str, str], Dict[str, int]] = {}
        for lf in lead_facts:
            key = (lf["workflow_id"], lf["batch_id"])
            if key not in batches:
                batches[key] = {
                    "total_leads": 0,
                    "yet_to_reach_out": 0,
                    "in_progress_telephony_failed": 0,
                    "in_progress_dialed": 0,
                    "completed": 0,
                }
            batches[key]["total_leads"] += 1
            bucket = lf["bucket"]
            if bucket in batches[key]:
                batches[key][bucket] += 1

        rows = []
        for (wf, batch), counts in sorted(batches.items()):
            rows.append({
                "workflow_id": wf,
                "batch_id": batch,
                "total_leads": counts["total_leads"],
                "yet_to_reach_out": counts["yet_to_reach_out"],
                "in_progress_telephony_failed": counts["in_progress_telephony_failed"],
                "in_progress_dialed": counts["in_progress_dialed"],
                "completed": counts["completed"],
            })
        return rows

    def generate_daily_overview(self, call_facts: Dict[str, CallFact]) -> List[Dict[str, Any]]:
        """
        Produces daily_overview.csv:
        workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads
        Sorted by workflow_id, date.
        Only dates with at least one call.
        connectivity_rate formatted with exactly 4 decimals.
        success_leads = distinct leads with at least one call on that date with is_success=true.
        """
        daily_groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for c in call_facts.values():
            key = (c.workflow_id, c.initiated_date)
            if key not in daily_groups:
                daily_groups[key] = {
                    "total_calls": 0,
                    "connected_calls": 0,
                    "success_leads": set(),
                }
            daily_groups[key]["total_calls"] += 1
            if c.is_connected:
                daily_groups[key]["connected_calls"] += 1
            if c.is_success:
                daily_groups[key]["success_leads"].add(c.lead_id)

        rows = []
        for (wf, dt), data in sorted(daily_groups.items()):
            tot = data["total_calls"]
            conn = data["connected_calls"]
            rate = (conn / tot) if tot > 0 else 0.0
            rows.append({
                "workflow_id": wf,
                "date": dt,
                "total_calls": tot,
                "connected_calls": conn,
                "connectivity_rate": f"{rate:.4f}",
                "success_leads": len(data["success_leads"]),
            })
        return rows

    def generate_rejections_report(self, conn) -> List[Dict[str, Any]]:
        """
        Produces rejections.csv:
        workflow_id,batch_id,row_number,reason
        Sorted by workflow_id, batch_id, row_number (numerical)
        """
        cur = conn.execute(
            """
            SELECT workflow_id, batch_id, row_number, reason
            FROM lead_rejections
            ORDER BY workflow_id ASC, batch_id ASC, row_number ASC;
            """
        )
        return [dict(r) for r in cur.fetchall()]

    def generate_data_quality_report(self, conn) -> Dict[str, Any]:
        """
        Produces data_quality.json:
        Reflects cumulative and final state of pipeline data quality.
        """
        lead_rows_total = self.db.get_stat("lead_rows_total")
        lead_rows_rejected = self.db.get_stat("lead_rows_rejected")
        duplicate_lead_rows_merged = self.db.get_stat("duplicate_lead_rows_merged")
        field_warnings = self.db.get_stat("field_warnings")
        events_total = self.db.get_stat("events_total")
        malformed_events = self.db.get_stat("malformed_events")
        duplicate_events_dropped = self.db.get_stat("duplicate_events_dropped")

        # Query rejections by reason
        cur = conn.execute(
            """
            SELECT reason, COUNT(*) as cnt
            FROM lead_rejections
            GROUP BY reason
            ORDER BY reason ASC;
            """
        )
        rejections_by_reason: Dict[str, int] = {r["reason"]: r["cnt"] for r in cur.fetchall()}

        # Final state orphan events (valid events in ledger with lead_id IS NULL)
        cur = conn.execute(
            "SELECT COUNT(*) as cnt FROM interaction_ledger WHERE lead_id IS NULL;"
        )
        orphan_events = cur.fetchone()["cnt"]

        # Final state unknown disposition events (linked disposition.* events with invalid code)
        cur = conn.execute(
            """
            SELECT COUNT(*) as cnt
            FROM interaction_ledger
            WHERE lead_id IS NOT NULL
              AND event_type LIKE 'disposition.%'
              AND is_valid_disposition = 0;
            """
        )
        unknown_disposition_events = cur.fetchone()["cnt"]

        return {
            "lead_rows_total": lead_rows_total,
            "lead_rows_rejected": lead_rows_rejected,
            "rejections_by_reason": rejections_by_reason,
            "duplicate_lead_rows_merged": duplicate_lead_rows_merged,
            "field_warnings": field_warnings,
            "events_total": events_total,
            "malformed_events": malformed_events,
            "duplicate_events_dropped": duplicate_events_dropped,
            "orphan_events": orphan_events,
            "unknown_disposition_events": unknown_disposition_events,
        }
