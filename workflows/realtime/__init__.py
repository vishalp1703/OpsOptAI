"""
Phase 6 — Real-Time Monitoring & API Integrations.

Self-contained so it can be dropped into your existing OPSOPTAI/workflows/
folder without touching Phase 1-5 code.

Modules:
    config           - source routing, table-name mapping, thresholds (edit this first)
    db               - runtime table reflection (no hard import of data/models.py)
    connectors       - mock polling connectors (POS, CRM, employee feedback, surveys)
    webhook_server    - FastAPI receiver for push sources (reviews, tickets, social, incidents)
    landing_writer    - single write path into per-source landing tables
    promotion         - landing rows -> normalized (unclassified) complaints rows
    batch_classify    - hooks into your real Phase 2 ClassificationAgent on a schedule
    alerting          - pluggable Notifier interface + keyword/severity/volume-spike rules
    scheduler         - ties polling + promotion + batch classification together
    mock_simulator    - CLI to fire test webhook traffic
    schema_check      - read-only diagnostic: your real DB vs config.py assumptions
"""
