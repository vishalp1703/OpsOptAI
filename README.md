# OpsPilot AI

OpsPilot AI turns operational feedback and incident data into structured insights. It includes multi-source ingestion, optional OpenRouter-powered analysis, a SQLite-backed dashboard, and an opt-in webhook workflow.

## Quick start

Requires Python 3.11 or later. On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python data/generate_sample_data.py
python app.py ingest
python -m data.init_db
python -m data.load_outputs
streamlit run dashboard/app.py
```

The dashboard is available at `http://localhost:8501`. For the full setup, batch pipeline, database, and webhook instructions, see [PROJECT.md](PROJECT.md).

## Tests

```powershell
python -m pytest -q
```

Tests and the default integration runner do not require paid LLM calls. To run LLM-backed phases, copy `.env.example` to `.env`, add a newly issued `OPENROUTER_API_KEY`, and review the API usage before running `python run_all.py --with-llm`.

## Secrets and local files

Never commit `.env`, API keys, webhook tokens, private integration configuration, local databases, or operational datasets. `.gitignore` excludes these by default. `.env.example` is a placeholder-only template; copy it locally and never put real credentials in it. If a credential has already been pushed, revoke it with its provider and replace it.

## Project status

This repository is an operational intelligence prototype. Review data handling, access controls, and integration settings before connecting production systems. See [PROJECT.md](PROJECT.md) for architecture, limitations, and supported commands.