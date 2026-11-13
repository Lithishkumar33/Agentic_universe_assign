# Assignment A: Interaction Ledger & Reporting Pipeline

This directory contains the production-grade implementation of the **Interaction Ledger & Reporting Pipeline** for Agentic Universe.

## Architecture & Design Overview

The pipeline implements an idempotent, incrementally updated data processing engine backed by SQLite (WAL mode) designed to handle messy lead CSV batches and streaming JSON Lines call events.

```
┌─────────────────────────┐          ┌──────────────────────────┐
│   Lead CSV Batches      │          │   Call Event Streams     │
│ (Messy headers, dates,  │          │ (JSON Lines, timestamps, │
│  phones, currency)      │          │  disposition overrides)  │
└───────────┬─────────────┘          └─────────────┬────────────┘
            │                                      │
            ▼                                      │
┌─────────────────────────┐                        │
│      LeadProcessor      │                        │
│ - Schema-order walk     │                        │
│ - Rejection & Warnings  │                        │
│ - Duplicate Merging     │                        │
│ - PII Masking & Hash    │                        │
└───────────┬─────────────┘                        │
            │                                      ▼
            │                         ┌──────────────────────────┐
            │                         │      EventProcessor      │
            │                         │ - JSON & schema validate │
            │                         │ - De-duplicate event_ids │
            │                         │ - Normalise IST dates    │
            │                         │ - PII Masking & Hash     │
            │                         └────────────┬─────────────┘
            ▼                                      │
┌──────────────────────────────────────────────────┴─────────────┐
│                    Append-Only Ledger & DB                     │
│  - leads: (workflow_id, phone_hash) unique identity            │
│  - interaction_ledger: immutable call event history            │
│  - lead_rejections: unaccepted rows (No PII)                   │
│  - pipeline_stats: cumulative data quality counters            │
└───────────────────────────┬────────────────────────────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │       MetricsEngine       │
              │ - Derived call facts      │
              │ - Effective disposition   │
              │ - Lead bucket resolution  │
              └─────────────┬─────────────┘
                            │
                            ▼
        ┌───────────────────────────────────────┐
        │ 5 Output Contract Reports (in --out): │
        │ 1. batch_summary.csv                  │
        │ 2. daily_overview.csv                 │
        │ 3. lead_report.csv                    │
        │ 4. rejections.csv                     │
        │ 5. data_quality.json                  │
        └───────────────────────────────────────┘
```

---

## Directory Structure

```
solution/
├── __init__.py
├── cli.py                  # Main entry point (ingest, report)
├── config.py               # Configurable paths, PII secrets
├── crypto.py               # Deterministic HMAC blind index, masking, Fernet encryption
├── database.py             # SQLite schema, WAL mode, transaction context
├── schema.py               # Schemas, field parsers, header normaliser, disposition catalogue
├── ingestion/
│   ├── __init__.py
│   ├── leads.py            # Lead parsing, validation, rejections, warnings
│   ├── events.py           # Event parsing, deduplication, disposition check
│   └── pipeline.py         # Orchestrator, reconciliation, idempotency
├── reporting/
│   ├── __init__.py
│   ├── metrics.py          # Derived facts, bucket resolution, report tables
│   └── exporter.py         # Output contract CSV & JSON writers
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_phone_normalization.py
    ├── test_date_parsing.py
    ├── test_decimal_parsing.py
    ├── test_header_matching.py
    ├── test_effective_disposition.py
    ├── test_bucket_logic.py
    ├── test_idempotency_and_incremental.py
    └── test_end_to_end.py
```

---

## Quickstart

### 1. Environment Setup
```bash
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Ingest Data Drops
```bash
python solution/cli.py ingest data/drop_1
python solution/cli.py ingest data/drop_2
```
*Note: Re-running `ingest` on any drop is completely idempotent and safe.*

### 3. Generate Reports
```bash
python solution/cli.py report --out reports/
```
Generates all 5 contract-compliant files in `reports/`:
- `batch_summary.csv`
- `daily_overview.csv`
- `lead_report.csv`
- `rejections.csv`
- `data_quality.json`

### 4. Run Test Suite
```bash
pytest -v
```

---

## Key Design Principles

1. **Deterministic PII Protection**:
   - `phone` and `customer_name` are never stored in plaintext in the database.
   - Lookups use a deterministic HMAC-SHA256 blind index (`phone_hash`) derived from canonical Indian mobile format (`+91XXXXXXXXXX`).
   - Phone numbers in `lead_report.csv` are masked (`+91******` + last 4 digits). Reversible fields are encrypted at rest with Fernet (AES-128-CBC + HMAC).

2. **Immutable Append-Only Ledger**:
   - Every valid distinct event is appended once to `interaction_ledger`.
   - Supervisor overrides (`disposition.override`) do not mutate or delete past events.
   - Events arriving before their lead are quarantined with `lead_id = NULL` and seamlessly linked when the lead arrives in a subsequent drop.

3. **Strict Derived Facts & Bucket Precedence**:
   - A call is only counted if `call.initiated` is present and linked.
   - Effective disposition prioritises valid `disposition.override` by latest `occurred_at`, then valid `disposition.set` by latest `occurred_at`.
   - Bucket logic strictly evaluates:
     1. `yet_to_reach_out`: 0 calls
     2. `completed`: any call has terminal disposition (`is_terminal = true`)
     3. `in_progress_telephony_failed`: every call has `call.telephony_failed`
     4. `in_progress_dialed`: everything else
