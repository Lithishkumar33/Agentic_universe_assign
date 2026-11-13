"""
Event ingestion module: JSON Lines stream parsing, deduplication, timestamp normalisation,
disposition validation, and PII protection.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from solution.crypto import hash_phone, mask_phone
from solution.schema import SchemaCatalog, parse_phone

IST = timezone(timedelta(hours=5, minutes=30))


def parse_event_timestamp(ts_str: str) -> Tuple[str, str, float]:
    """
    Parses event timestamps which come in three forms:
    1. UTC: '2026-10-05T13:20:21Z'
    2. Offset: '2026-10-01T10:40:19+05:30'
    3. Naive: '2026-10-03 10:01:00' (naive means IST per spec)

    Returns:
        - occurred_at_ist: ISO-8601 string in IST (e.g. '2026-10-05T18:50:21+05:30')
        - occurred_date_ist: 'YYYY-MM-DD'
        - occurred_epoch: unix timestamp float
    """
    s = ts_str.strip()
    if s.endswith("Z"):
        dt_utc = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        dt_ist = dt_utc.astimezone(IST)
    elif "T" in s:
        dt = datetime.fromisoformat(s)
        dt_ist = dt.astimezone(IST)
    else:
        # Naive format => IST per spec
        dt_naive = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        dt_ist = dt_naive.replace(tzinfo=IST)

    return dt_ist.isoformat(), dt_ist.strftime("%Y-%m-%d"), dt_ist.timestamp()


class EventProcessor:
    def __init__(self, catalog: SchemaCatalog):
        self.catalog = catalog

    def process_event_file(
        self,
        file_path: Path,
        drop_name: str,
        seen_event_ids: set[str],
    ) -> Tuple[List[dict], int, int, int]:
        """
        Parses a single JSONL event file.
        Returns:
            - parsed_events: list of clean event records ready for ledger insertion
            - total_non_empty_lines: int
            - malformed_count: int
            - duplicate_count: int
        """
        parsed_events: List[dict] = []
        total_lines = 0
        malformed_count = 0
        duplicate_count = 0

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                raw_line = line.strip()
                if not raw_line:
                    continue

                total_lines += 1

                # 1. Malformed check
                try:
                    obj = json.loads(raw_line)
                    if not isinstance(obj, dict):
                        malformed_count += 1
                        continue
                except Exception:
                    malformed_count += 1
                    continue

                event_id = obj.get("event_id")
                if not event_id or not isinstance(event_id, str):
                    malformed_count += 1
                    continue

                # 2. Duplicate check
                if event_id in seen_event_ids:
                    duplicate_count += 1
                    continue

                seen_event_ids.add(event_id)

                event_type = obj.get("event_type", "")
                workflow_id = obj.get("workflow_id", "")
                call_id = obj.get("call_id", "")
                raw_ts = obj.get("occurred_at", "")

                try:
                    at_ist, date_ist, epoch = parse_event_timestamp(raw_ts)
                except Exception:
                    # Invalid timestamp format treated as malformed
                    malformed_count += 1
                    continue

                payload = obj.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {}

                # Phone parsing & PII hashing
                raw_phone = obj.get("phone")
                phone_h: Optional[str] = None
                if raw_phone:
                    canon_phone = parse_phone(raw_phone)
                    if canon_phone:
                        phone_h = hash_phone(canon_phone)

                # Disposition check
                disp_code: Optional[str] = None
                is_valid_disp: Optional[int] = None
                if event_type.startswith("disposition."):
                    code = payload.get("disposition_code")
                    if code and isinstance(code, str):
                        disp_code = code.strip()
                        is_valid_disp = 1 if self.catalog.is_valid_disposition(workflow_id, disp_code) else 0
                    else:
                        is_valid_disp = 0

                # Protect PII at rest in stored ledger: mask phone in payload/stored record
                stored_payload = dict(payload)
                if "phone" in stored_payload:
                    stored_payload["phone"] = "+91******0000"

                parsed_events.append({
                    "event_id": event_id,
                    "event_type": event_type,
                    "workflow_id": workflow_id,
                    "call_id": call_id,
                    "occurred_at_raw": raw_ts,
                    "occurred_at_ist": at_ist,
                    "occurred_date_ist": date_ist,
                    "occurred_epoch": epoch,
                    "phone_hash": phone_h,
                    "disposition_code": disp_code,
                    "is_valid_disposition": is_valid_disp,
                    "payload_json": json.dumps(stored_payload),
                    "drop_name": drop_name,
                })

        return parsed_events, total_lines, malformed_count, duplicate_count
