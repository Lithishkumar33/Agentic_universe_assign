"""
Configuration and settings for the Interaction Ledger & Reporting Pipeline.
"""
from __future__ import annotations

import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = Path(os.environ.get("AU_SCHEMAS_DIR", BASE_DIR / "assignment-a-data" / "schemas"))
DEFAULT_DB_PATH = Path(os.environ.get("AU_DATABASE_PATH", BASE_DIR / "ledger.db"))

# PII Encryption and Blind Index Salt
# In production, these are supplied via environment variables or secret managers (e.g. AWS KMS / HashiCorp Vault).
PII_SALT = os.environ.get("AU_PII_SALT", "agentic-universe-takehome-2026-pii-salt")
PII_SECRET_KEY = os.environ.get("AU_PII_SECRET_KEY", "uG-H7jL_91WlFp_19oK7s9sYFw8E4lGg4YjL_91WlFp=")
