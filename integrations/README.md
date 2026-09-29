# Optional integrations layer

This package is disabled by default and does not alter the existing Phase 1–6
entry points. It adapts external files and source databases into the existing
OpsPilot landing tables, then uses the existing promotion, classification,
alerting, database, and dashboard flows.

Before a record is written to a landing table, the Ingestion Mapping Agent
validates it against the same StandardComplaint contract used by Phase 1.
Records without usable complaint text are skipped and reported; no malformed
record reaches classification or later agents.

## Security model

- Do not put database passwords in JSON configuration or prompts.
- Put each external database URL in an environment variable, such as
  EXTERNAL_CRM_DATABASE_URL.
- Use read-only source-database accounts with access to only the selected
  table or view.
- The optional LLM mapper receives column names only. It never receives a
  database URL, credentials, or record values.
- The chat API is read-only and requires OPSPILOT_CHAT_API_TOKEN.

## Configure a source

Copy sources.example.json to sources.json. Keep the copied file local because
it may contain file paths and source metadata. Set enabled to true only after
reviewing the mapping.

For a database source:

    EXTERNAL_CRM_DATABASE_URL=postgresql+psycopg://readonly_user:password@host:5432/crm

For a file source, set file_path to a CSV, XLS, or XLSX export.

The mapping maps external columns to id, store, date, and text. Known headers
are inferred and saved to data/schema_mappings. Leave mapping empty when the
headers are recognized; for unknown headers, add an explicit mapping using
actual source column names. Set allow_llm_mapping to true only to permit the optional,
column-name-only OpenRouter fallback.

## Run an enabled source

    python -m integrations.unified_pipeline --config integrations/sources.json
    python -m integrations.unified_pipeline --config integrations/sources.json --source external-crm

Each successful source run writes to the existing raw landing table selected by
target_source and promotes records into complaints. Current database reads are
bounded snapshots controlled by --limit; scheduled incremental cursors are the
next production-hardening step.

## Run read-only retrieval chat

    set OPSPILOT_CHAT_API_TOKEN=choose-a-long-random-token
    uvicorn integrations.chat_api:app --port 8002

Send POST /chat with the X-OpsPilot-Chat-Token header and JSON such as:

    {"question": "What are the latest complaint trends?", "limit": 10}

The response contains summary counts and the evidence records used to answer.
