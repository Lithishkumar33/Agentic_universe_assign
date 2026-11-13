# Submission

## Candidate
- **Name:** Lithish Kumar
- **Assignment chosen:** A (Data: Interaction Ledger & Reporting Pipeline)
- **Time spent (honest estimate):** 6h

## How to run
```bash
# 1. Setup virtual environment & dependencies from a clean clone
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt

# 2. Run data ingestion (idempotent; supports sequential or combined drops)
python solution/cli.py ingest data/drop_1
python solution/cli.py ingest data/drop_2

# 3. Generate the 5 output contract reports
python solution/cli.py report --out reports/

# 4. Run full automated test suite (28 unit and integration tests)
pytest -v
```

## Design

The solution is architected as an idempotent, incremental data engine backed by SQLite running in Write-Ahead Logging (`WAL`) mode with explicit transaction boundaries, ensuring zero external service dependencies while delivering sub-2-second performance.

### Architecture Overview

```
                      Raw Drops (data/drop_1, data/drop_2)
                                      │
            ┌─────────────────────────┴─────────────────────────┐
            ▼                                                   ▼
      Lead Batches (CSV)                               Call Events (JSONL)
            │                                                   │
     [LeadProcessor]                                    [EventProcessor]
   - Header alias map                                  - Malformed check
   - Schema-order walk                                 - Deduplicate event_ids
   - Rejections & Warnings                             - IST timestamp normalisation
   - Duplicate merge                                   - Unknown disposition check
   - Blind index + Fernet PII                          - PII phone masking
            │                                                   │
            └─────────────────────────┬─────────────────────────┘
                                      ▼
                        [DatabaseManager: SQLite WAL]
           ┌────────────────────────────────────────────────────────┐
           │ - leads (workflow_id, phone_hash) UNIQUE               │
           │ - interaction_ledger (event_id UNIQUE, append-only)    │
           │ - lead_rejections (workflow_id, batch_id, row_number)  │
           │ - pipeline_stats (cumulative quality counters)         │
           │ - ingested_drops (drop idempotency tracking)           │
           └──────────────────────────┬─────────────────────────────┘
                                      │
                                      ▼
                             [MetricsEngine]
                   - Reconcile orphan events on-demand
                   - Derive call facts (call.initiated presence)
                   - Compute effective disposition (overrides > sets)
                   - Determine lead bucket (1 to 4 strict order)
                                      │
                                      ▼
                           [ReportExporter]
             Writes batch_summary.csv, daily_overview.csv,
             lead_report.csv, rejections.csv, data_quality.json
```

### Database Schema & Ledger Rationale
1. **`interaction_ledger`**: Implements an immutable, append-only ledger for all unique events. Overrides (`disposition.override`) append a new record with supervisor details rather than updating past rows. Events arriving before their leads are stored safely with `lead_id = NULL` (quarantine). When a subsequent drop introduces the lead, unlinked events are linked automatically.
2. **`leads`**: Enforces lead identity on `(workflow_id, phone_hash)`. The first valid CSV row permanently sets the lead's `batch_id`. Subsequent valid rows merge into the existing record (`last write wins` for non-null values), incrementing `duplicate_lead_rows_merged`. Schema defaults are applied after merging to any fields that remain null.
3. **PII Protection at Rest**:
   - Neither `phone` nor `customer_name` is ever stored in plaintext anywhere in the database or ledger.
   - For phone matching and indexing, we use a deterministic HMAC-SHA256 blind index (`phone_hash`) computed against canonical Indian mobile numbers (`+91XXXXXXXXXX`).
   - For reversible storage, sensitive PII is encrypted using authenticated symmetric encryption (Fernet / AES-128-CBC + HMAC-SHA256).
   - In reports, phone numbers are masked per spec (`+91******` + last 4 digits).

---

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| **SQLite with WAL mode as primary storage** | Zero infrastructure footprint, fully portable, reproducible on any OS without Docker daemon, and processes small dataset in 1.2s. | PostgreSQL via Docker Compose (supported via connection string, but SQLite eliminates friction for reviewers). |
| **HMAC-SHA256 Blind Index for PII** | Phone numbers must match events to leads without decrypting table scans or storing plaintext numbers in the DB. HMAC with salt prevents rainbow table attacks. | Plain SHA-256 (vulnerable to brute-force on 10-digit mobile space) or decrypting all leads on ingest (O(N) CPU overhead). |
| **Two-Phase Orphan Event Reconciliation** | Events can arrive before leads (920 events in drop 1 match drop 2 leads). Reconciling by `(workflow_id, phone_hash)` on lead insertion links events; a second pass on `call_id` links overrides without phone fields. | Discarding early events (violates data integrity) or reprocessing all files on every drop (violates incremental principle). |
| **Tie-breaking identical timestamps** | In synthetic data, if multiple overrides or sets share identical timestamps, we break ties deterministically by `event_id` ascending. | Arbitrary database rowid order (non-deterministic). |
| **Strict Precedence in Rejection Walk** | Walking schema fields in order: if `customer_name` is empty, it fails `missing_required:customer_name` immediately, even if `phone` is also missing or invalid. | Collecting multi-field errors into an array (spec specifies single reason). |
| **Blank Rows Ignored Completely** | Blank rows are skipped and do not increment 1-based `row_number` or `lead_rows_total`. | Incrementing row counter for blank lines (contradicts spec line 90: "skipped and not counted"). |

---

## Failure modes considered

1. **Duplicate Event Delivery**:
   - Handled: Deduplicated on `event_id`. Duplicate occurrences in the same or subsequent drops increment `duplicate_events_dropped` and are skipped.
2. **Re-running Ingestion (Idempotency)**:
   - Handled: `ingested_drops` tracks successfully processed drops. Re-running `ingest data/drop_1` logs an idempotent skip without modifying database state or double-counting statistics.
3. **Events Arriving Out of Order**:
   - Handled: An event arriving before its lead is quarantined (`lead_id = NULL`) and linked automatically once the lead is uploaded. Overrides arriving before `call.initiated` link through `call_id` reconciliation.
4. **Telephony Layer Failures vs Connectivity**:
   - Handled: `call.telephony_failed` does not mark a call as connected. A call is connected only if `call.connected` exists.
5. **Supervisor Override with Unknown Disposition Code**:
   - Handled: Per rule 4, any `disposition.*` event with a code not present in `dispositions.csv` is counted in `unknown_disposition_events` and ignored. The call's effective disposition falls back to the latest valid `disposition.set` (or None).
6. **Process Crashes Mid-Ingestion**:
   - Handled: Ingestion occurs inside atomic SQLite transactions (`BEGIN IMMEDIATE ... COMMIT`). A crash or `kill -9` rolls back uncommitted changes, ensuring database consistency.
7. **Known Limitation (Out of Scope)**:
   - Dynamic schema alterations mid-flight (e.g. altering existing field types on the fly without DB schema migration) is not supported without a migration tool (e.g. Alembic).

---

## Testing

### What was tested:
1. **Phone Normalisation**: All 6 formats (`+91`, `91-`, `0`, spaces, parentheses, raw 10-digit), plus landlines, bad prefixes (1–5), international numbers, and 9/11/13-digit lengths.
2. **Date Parsing**: `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-MM-YYYY`, `DD-Mon-YYYY` (case-insensitive month), impossible dates (Feb 31, leap year edge cases, month 13, day 32), and non-date strings (`soon`, `TBD`).
3. **Decimal Parsing**: Rupee symbols (`₹`), `Rs`, `Rs.`, `INR`, thousands separators (`1,27,554`), negative value rejection, and rounding to 2 decimals.
4. **Header Mapping**: Normalisation of whitespace, underscores, case, and alias resolution.
5. **Disposition Resolution & Overrides**: Overrides taking precedence over sets, latest-by-timestamp resolution, unknown disposition code fallback.
6. **Lead Bucket State Machine**: All 4 buckets (`yet_to_reach_out`, `completed`, `in_progress_telephony_failed`, `in_progress_dialed`) and edge cases where telephony failure co-exists with dialled calls.
7. **Idempotency & Incremental Verification**: Re-running drops, linking orphan events when leads arrive later, and byte-for-byte diffing sequential ingestion (`drop_1` then `drop_2`) against combined ingestion.
8. **End-to-End Contract Verification**: Checking all 5 report files against headers, row sorting, 4-decimal connectivity rate, and data quality totals.

### What was not tested:
- High-availability distributed locking across multi-node clusters (beyond scope for single-machine SQLite take-home).

---

## Results

### Timings (Small Dataset: 2,057 lead rows, 8,745 event lines)
- `drop_1` ingestion: **0.564s**
- `drop_2` ingestion: **0.609s**
- `report` export (all 5 files): **0.117s**
- Total pipeline run: **1.289s**

### Observations in Data
- **Out-of-order arrival**: Exactly **920 events** in `drop_1` belonged to leads that were only delivered in `drop_2`. Our reconciliation linked all 920 events upon `drop_2` ingestion.
- **Overrides without phone**: Overrides carry no phone number and link solely through `call_id`.
- **Pure Orphans**: Calls placed to phone numbers never uploaded as leads remained orphans (**462 events**), matching data quality expectations.
- **Unknown Dispositions**: 18 linked events contained invalid codes (`RENEWD`, `call_back`, `unknown`), which were cleanly ignored while maintaining audit integrity.

---

## If I had another day

1. **Streaming Ingestion with Apache Kafka / Redpanda**: Ingest JSON Lines call events via a streaming consumer rather than file drops, updating the interaction ledger in micro-batches with consumer offset checkpointing.
2. **PostgreSQL + Partitioning**: For 100k+ leads/day, migrate to PostgreSQL with date-partitioned tables (`interaction_ledger_2026_10`) and hypertable indexing.
3. **Dead Letter Queue (DLQ)**: Export rejected lead rows and malformed events directly to an S3/GCS DLQ bucket with an automated alerting webhook for operations teams.
4. **Automated Schema Evolution**: Implement an automated schema migration runner to handle new workflows or column additions without service downtime.
