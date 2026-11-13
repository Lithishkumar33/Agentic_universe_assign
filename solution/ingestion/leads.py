"""
Lead ingestion module: CSV validation, schema mapping, rejection, and idempotent duplicate merging.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from solution.crypto import encrypt_pii, hash_phone, mask_phone
from solution.schema import (
    FieldSpec,
    SchemaCatalog,
    WorkflowSchema,
    normalize_header,
    parse_date,
    parse_decimal,
    parse_phone,
)


class LeadProcessor:
    def __init__(self, catalog: SchemaCatalog):
        self.catalog = catalog

    def is_blank_row(self, row_values: List[str]) -> bool:
        """
        Blank rows (every cell empty or whitespace) are skipped and not counted.
        """
        if not row_values:
            return True
        return all(cell.strip() == "" for cell in row_values)

    def process_lead_file(
        self,
        file_path: Path,
        workflow_id: str,
        drop_name: str,
    ) -> Tuple[List[dict], List[dict], int, int, int]:
        """
        Parses and validates a single lead CSV file.
        Returns:
            - valid_rows: list of dicts with parsed lead records ready for DB merge/insert
            - rejected_rows: list of dicts with rejections (workflow_id, batch_id, row_number, reason)
            - total_non_blank_rows: int
            - rejected_count: int
            - warnings_count: int
        """
        schema = self.catalog.get_schema(workflow_id)
        if not schema:
            raise ValueError(f"Schema not found for workflow: {workflow_id}")

        batch_id = file_path.stem

        valid_rows: List[dict] = []
        rejected_rows: List[dict] = []
        total_non_blank_rows = 0
        rejected_count = 0
        total_warnings = 0

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            try:
                raw_header = next(reader)
            except StopIteration:
                return valid_rows, rejected_rows, 0, 0, 0

            # Map CSV header columns to schema fields
            col_to_field: Dict[int, str] = {}
            for col_idx, col_name in enumerate(raw_header):
                norm_name = normalize_header(col_name)
                if norm_name in schema.header_to_field:
                    col_to_field[col_idx] = schema.header_to_field[norm_name]

            row_number = 0
            for row in reader:
                if self.is_blank_row(row):
                    continue

                row_number += 1
                total_non_blank_rows += 1

                # Extract values mapped by column
                row_raw_dict: Dict[str, str] = {}
                for col_idx, cell in enumerate(row):
                    if col_idx in col_to_field:
                        fname = col_to_field[col_idx]
                        row_raw_dict[fname] = cell.strip()

                # Validate row walking fields in schema order
                rejection_reason: Optional[str] = None
                parsed_fields: Dict[str, Any] = {}
                row_warnings = 0

                for field_spec in schema.fields:
                    fname = field_spec.name
                    ftype = field_spec.field_type
                    raw_val = row_raw_dict.get(fname, "")

                    is_missing = (raw_val == "")

                    # Check required
                    if is_missing:
                        if field_spec.required:
                            rejection_reason = f"missing_required:{fname}"
                            break
                        else:
                            parsed_fields[fname] = None
                            continue

                    # Field is present -> validate according to type
                    if ftype == "phone":
                        canon_phone = parse_phone(raw_val)
                        if canon_phone is None:
                            rejection_reason = "invalid_phone"
                            break
                        parsed_fields[fname] = canon_phone

                    elif ftype == "date":
                        dt_val = parse_date(raw_val)
                        if dt_val is None:
                            if field_spec.required:
                                rejection_reason = f"invalid_date:{fname}"
                                break
                            else:
                                parsed_fields[fname] = None
                                row_warnings += 1
                        else:
                            parsed_fields[fname] = dt_val

                    elif ftype == "decimal":
                        dec_val = parse_decimal(raw_val)
                        if dec_val is None:
                            if field_spec.required:
                                rejection_reason = f"invalid_decimal:{fname}"
                                break
                            else:
                                parsed_fields[fname] = None
                                row_warnings += 1
                        else:
                            parsed_fields[fname] = dec_val

                    elif ftype == "enum":
                        val_lower = raw_val.lower()
                        if val_lower in field_spec.values:
                            parsed_fields[fname] = val_lower
                        else:
                            if field_spec.required:
                                rejection_reason = f"invalid_enum:{fname}"
                                break
                            else:
                                parsed_fields[fname] = None
                                row_warnings += 1

                    elif ftype == "string":
                        parsed_fields[fname] = raw_val

                if rejection_reason is not None:
                    rejected_count += 1
                    rejected_rows.append({
                        "drop_name": drop_name,
                        "workflow_id": workflow_id,
                        "batch_id": batch_id,
                        "row_number": row_number,
                        "reason": rejection_reason,
                    })
                    # Count warnings only for rows that are not rejected
                    continue

                total_warnings += row_warnings
                valid_rows.append({
                    "workflow_id": workflow_id,
                    "batch_id": batch_id,
                    "fields": parsed_fields,
                    "schema": schema,
                })

        return valid_rows, rejected_rows, total_non_blank_rows, rejected_count, total_warnings
