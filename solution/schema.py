"""
Schema definitions, header normalisation, field parsers and disposition catalogue.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}


def normalize_header(h: str) -> str:
    """
    To normalise a header: lower-case it, trim it, and replace every run of '_' or
    whitespace with a single space.
    """
    if not h:
        return ""
    h = h.lower().strip()
    return re.sub(r"[\s_]+", " ", h)


def parse_phone(raw_value: str) -> Optional[str]:
    """
    Normalises an Indian mobile number.
    Strip every non-digit. If 12 digits starting with 91, drop 91.
    If 11 digits starting with 0, drop 0.
    The result is valid only if it is exactly 10 digits and starts with 6, 7, 8 or 9.
    Returns canonical format +91XXXXXXXXXX, or None if invalid.
    """
    if not raw_value:
        return None
    digits = re.sub(r"\D", "", raw_value)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None


def parse_date(raw_value: str) -> Optional[str]:
    """
    Accepts YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, and DD-Mon-YYYY (e.g. 01-Dec-2026;
    month name is case-insensitive). Dates are day-first. Impossible dates are invalid.
    Returns ISO date string YYYY-MM-DD, or None if invalid.
    """
    if not raw_value:
        return None
    val = raw_value.strip()
    # 1. YYYY-MM-DD
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", val):
        try:
            dt = datetime.strptime(val, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 2. DD/MM/YYYY (day-first)
    if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", val):
        try:
            dt = datetime.strptime(val, "%d/%m/%Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 3. DD-MM-YYYY (day-first)
    if re.match(r"^\d{1,2}-\d{1,2}-\d{4}$", val):
        try:
            dt = datetime.strptime(val, "%d-%m-%Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # 4. DD-Mon-YYYY (case-insensitive month)
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", val)
    if m:
        day_str, mon_str, year_str = m.groups()
        mon_lower = mon_str.lower()
        if mon_lower in MONTH_MAP:
            try:
                dt = datetime(int(year_str), MONTH_MAP[mon_lower], int(day_str))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                return None

    return None


def parse_decimal(raw_value: str) -> Optional[float]:
    """
    Optionally prefixed by ₹, Rs, Rs., or INR (case-insensitive, optional space).
    Commas are thousands separators. Must be >= 0. Round to 2 places.
    Returns float rounded to 2 places, or None if invalid.
    """
    if not raw_value:
        return None
    val = raw_value.strip()
    # Strip prefix
    val_clean = re.sub(r"^(?:₹|rs\.|rs|inr)\s*", "", val, flags=re.IGNORECASE).strip()
    val_clean = val_clean.replace(",", "")
    try:
        f = float(val_clean)
        if f >= 0:
            return round(f, 2)
    except ValueError:
        return None
    return None


@dataclass
class FieldSpec:
    name: str
    field_type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    values: List[str] = field(default_factory=list)
    default: Optional[str] = None
    aliases: List[str] = field(default_factory=list)


@dataclass
class WorkflowSchema:
    workflow_id: str
    description: str
    fields: List[FieldSpec]
    reference_field: str
    header_to_field: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowSchema:
        wf_id = data["workflow_id"]
        desc = data.get("description", "")
        fields: List[FieldSpec] = []
        ref_field = ""
        header_map: Dict[str, str] = {}

        for f in data.get("fields", []):
            spec = FieldSpec(
                name=f["name"],
                field_type=f["type"],
                required=f.get("required", False),
                pii=f.get("pii", False),
                reference=f.get("reference", False),
                values=[v.lower() for v in f.get("values", [])],
                default=f.get("default"),
                aliases=f.get("aliases", []),
            )
            fields.append(spec)
            if spec.reference:
                ref_field = spec.name

            # Map normalised field name
            header_map[normalize_header(spec.name)] = spec.name
            # Map normalised aliases
            for alias in spec.aliases:
                header_map[normalize_header(alias)] = spec.name

        return cls(
            workflow_id=wf_id,
            description=desc,
            fields=fields,
            reference_field=ref_field,
            header_to_field=header_map,
        )


@dataclass
class DispositionSpec:
    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


class SchemaCatalog:
    """
    Catalog of all workflow schemas and dispositions.
    """

    def __init__(self, schemas_dir: Path):
        self.schemas_dir = schemas_dir
        self.schemas: Dict[str, WorkflowSchema] = {}
        self.dispositions: Dict[Tuple[str, str], DispositionSpec] = {}
        self._load()

    def _load(self):
        # Load workflow JSON schemas
        for json_file in sorted(self.schemas_dir.glob("*.json")):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                schema = WorkflowSchema.from_dict(data)
                self.schemas[schema.workflow_id] = schema

        # Load dispositions CSV
        disp_file = self.schemas_dir / "dispositions.csv"
        if disp_file.exists():
            with open(disp_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    wf = row["workflow_id"].strip()
                    code = row["code"].strip()
                    spec = DispositionSpec(
                        workflow_id=wf,
                        code=code,
                        label=row["label"].strip(),
                        is_terminal=row["is_terminal"].strip().lower() == "true",
                        is_success=row["is_success"].strip().lower() == "true",
                    )
                    self.dispositions[(wf, code)] = spec

    def get_schema(self, workflow_id: str) -> Optional[WorkflowSchema]:
        return self.schemas.get(workflow_id)

    def get_disposition(self, workflow_id: str, code: str) -> Optional[DispositionSpec]:
        return self.dispositions.get((workflow_id, code))

    def is_valid_disposition(self, workflow_id: str, code: str) -> bool:
        return (workflow_id, code) in self.dispositions
