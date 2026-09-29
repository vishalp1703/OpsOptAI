# OpsPilot AI — Project Guide

OpsPilot AI converts operational feedback and incident data into structured insights. It supports batch files, LLM-assisted analysis, SQLite/PostgreSQL storage, a Streamlit dashboard, and a near-real-time webhook/polling workflow.

## Architecture and phases

| Phase | Purpose | Run command | Output |
| --- | --- | --- | --- |
| 1 | Ingestion Mapping Agent: map, validate, and normalize eight source formats | `python app.py ingest` | `data/cleaned/*.csv`, `master_cleaned.csv` |
| 2 | Classify complaints with an OpenRouter LLM | `python -m agents.classification_agent` | `outputs/classified_complaints.csv` |
| 2b | Benchmark and select a model | `python -m agents.model_evaluator` | `outputs/best_model.json` |
| 3 | Generate root causes, patterns, recommendations, Jira stories, and summary | `python -m workflows.pipeline` | `outputs/opspilot_report.json` |
| 4 | Create and load the database | `python -m data.init_db`, `python -m data.load_outputs` | SQLite DB by default |
| 5 | View insights in Streamlit | `streamlit run dashboard/app.py` | Local dashboard |
| 6 | Poll, accept webhooks, promote, classify, and alert | Uvicorn + scheduler | Landing rows, complaints, classifications, alerts |

Supported sources: customer reviews, support tickets, POS logs, employee feedback, surveys, social media, CRM notes, and incident reports.

## Prerequisites

- Python 3.11 or later (3.12 recommended)
- `pip`
- An OpenRouter API key for Phases 2, 2b, 3, and live classification
- Optional: PostgreSQL for production; SQLite is the local default

Run all commands from the project root.

> Security: Store credentials only in `.env` and never commit API keys. If a key was placed in a shared or tracked file, revoke it in the provider console and create a replacement.

## Installation

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, invoke the venv interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configuration

Create `.env` in the project root. Start from `.env.example`, but use a newly-issued OpenRouter key and a long random webhook secret.

```dotenv
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_SITE_URL=https://opspilot.local
OPENROUTER_APP_NAME=OpsPilot-AI

# Optional; defaults to sqlite:///data/opspilot.db
# DATABASE_URL=postgresql+psycopg://user:password@host:5432/opspilot

WEBHOOK_SHARED_SECRET=use-a-long-random-value

# Optional alert email settings
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_USER=...
# SMTP_PASSWORD=...
# ALERT_EMAIL_FROM=alerts@example.com
# ALERT_EMAIL_TO=ops@example.com,leader@example.com
```

## Batch workflow

### One-command integration runner

Use the root runner to exercise the completed local phases in order:

```bash
python run_all.py
```

This runs Phase 1, database setup/loading, a dashboard data-layer smoke test,
and Phase 6's isolated tests. It deliberately skips paid OpenRouter work. To
run every phase, including live Phase 2 classification and Phase 3 agents:

```bash
python run_all.py --with-llm --limit 10
```

`--with-llm` overwrites the standard Phase 2/3 checkpoint outputs and makes
external API calls. Add `--launch-dashboard` to start Streamlit after the run.

### 1. Generate sample data (optional)

Sample data is included. Regenerate it when needed:

```bash
python data/generate_sample_data.py --rows 40 --seed 42
```

### 2. Run Phase 1 ingestion

```bash
python app.py ingest
```

Process selected sources only:

```bash
python app.py ingest --sources customer_reviews support_tickets
```

The canonical combined output is `data/cleaned/master_cleaned.csv`.
Every record passes through `agents/ingestion_mapping_agent.py` before it is
written. This first-stage agent enforces the stable `StandardComplaint`
schema and rejects blank complaint text, so downstream classification and
analysis agents receive only valid normalized records.

### 3. Optional: benchmark models

Verify configured model IDs, then benchmark candidates against representative data:

```bash
python -m agents.model_evaluator --check-slugs
python -m agents.model_evaluator --input data/cleaned/master_cleaned.csv --sample-size 15
```

The recommended model is written to `outputs/best_model.json`.

### 4. Run Phase 2 classification

```bash
python -m agents.classification_agent \
  --input data/cleaned/master_cleaned.csv \
  --output outputs/classified_complaints.csv
```

For a low-cost smoke test, add `--limit 10`. To explicitly select a configured model, add `--model gpt`, `claude`, `gemini`, `deepseek`, or `grok`.

### 5. Run Phase 3 multi-agent analysis

```bash
python -m workflows.pipeline --input outputs/classified_complaints.csv
```

Checkpoint files allow safe resumption. Common options:

```bash
# Re-run all Phase 3 stages
python -m workflows.pipeline --input outputs/classified_complaints.csv --force

# Re-run pattern detection and every downstream stage
python -m workflows.pipeline --input outputs/classified_complaints.csv --force-from patterns

# Set period and cluster-size metadata
python -m workflows.pipeline --input outputs/classified_complaints.csv --period "Q3 2026" --min-cluster-size 3
```

### 6. Initialize and load the database (Phase 4)

Create database tables while preserving current data:

```bash
python -m data.init_db
python -m data.load_outputs
```

For a disposable local demo database, the following recreates every table and loads raw CSV files. It is destructive:

```bash
python -m data.init_db --drop --load-raw
```

### 7. Start the dashboard (Phase 5)

```bash
streamlit run dashboard/app.py
```

The dashboard reads `data/opspilot.db` unless `DATABASE_URL` is set. It includes volumes, categories, sentiment, root causes, store performance, patterns, recommendations, and the latest executive summary.

## Near-real-time workflow (Phase 6)

Use two terminals, each with the virtual environment active.

### Terminal 1: Webhook receiver

```bash
uvicorn workflows.realtime.webhook_server:app --reload --port 8001
```

Available endpoints:

- `GET /healthz`
- `POST /webhooks/customer_reviews`
- `POST /webhooks/support_tickets`
- `POST /webhooks/social_media`
- `POST /webhooks/incidents`

Every webhook request needs the `X-OpsPilot-Webhook-Secret` header with the configured `WEBHOOK_SHARED_SECRET`.

### Terminal 2: Scheduler

```bash
python -m workflows.realtime.scheduler
```

The scheduler polls POS/CRM/employee-feedback/survey sources; promotes landing rows every minute; then classifies new complaints and checks volume spikes according to the configured interval.

### Terminal 3: Optional webhook simulator

```bash
python -m workflows.realtime.mock_simulator --count 10 --delay 2
```

### Verify Phase 6

```bash
python -m workflows.realtime.schema_check
python -m workflows.realtime.verify_pipeline
python -m pytest tests/test_phase6.py -q
```

`verify_pipeline` is read-only and reports landing-table, complaint, classification, and alert counts.

## Optional external integrations and retrieval

The `integrations/` package is opt-in and leaves the existing Phase 1–6 commands unchanged. It adds external CSV/Excel and source-database adapters plus a read-only chat-retrieval API. See `integrations/README.md` for secure configuration and run commands.

## Key files and outputs

- `data/raw/` — raw source files
- `data/cleaned/` — cleaned source files and `master_cleaned.csv`
- `outputs/classified_complaints.csv` — Phase 2 classification
- `outputs/root_caused_complaints.csv` — root-cause results
- `outputs/patterns.json`, `recommendations.json`, `jira_stories.json` — Phase 3 artifacts
- `outputs/executive_summary.json`, `opspilot_report.json` — summary reports
- `data/opspilot.db` — default local database

## Current limitations

- Resolution time and employee-performance dashboard metrics are intentionally unavailable because the current schema lacks the required structured data.
- Phase 6 polling connectors are mock development connectors. Production use requires vendor integrations and persistent polling cursors.
- Adaptive schema mapping (Agent 0) is planned in `agents/AGENT_0_ROADMAP.md`, not implemented.
- If a local virtual environment cannot find its base Python interpreter, recreate only `.venv` using the installation steps above.

## Troubleshooting

| Problem | Check / fix |
| --- | --- |
| `python` is not recognized | Activate `.venv` or run `.\.venv\Scripts\python.exe` directly on Windows. |
| Virtual environment cannot find base Python | Recreate `.venv` with an installed Python version. |
| LLM stages fail | Check `OPENROUTER_API_KEY`, network access, and model IDs with `--check-slugs`. |
| Dashboard has no data | Run `python -m data.init_db` and `python -m data.load_outputs`, then verify `DATABASE_URL`. |
| Webhook returns 401 | Match `X-OpsPilot-Webhook-Secret` exactly to `WEBHOOK_SHARED_SECRET`. |
| Alerts table missing | Run `python -m data.init_alerts_table`. |
