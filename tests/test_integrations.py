from integrations.config import ExternalSourceConfig, SourceKind
from integrations.mapping import MappingStore, infer_mapping


def test_rule_based_mapping_finds_common_columns(tmp_path):
    mapping, confidence = infer_mapping(["Ticket ID", "Branch", "Created Date", "Description"])
    assert mapping == {
        "id": "Ticket ID",
        "store": "Branch",
        "date": "Created Date",
        "text": "Description",
    }
    assert confidence["id"] == 0.9

    store = MappingStore(tmp_path)
    store.save("tickets", ["Ticket ID", "Branch", "Created Date", "Description"], mapping, confidence)
    assert store.load("tickets", ["Branch", "Description", "Ticket ID", "Created Date"]) == mapping


def test_external_database_config_keeps_credentials_out_of_config():
    source = ExternalSourceConfig(
        name="partner-crm",
        kind=SourceKind.DATABASE,
        target_source="crm_notes",
        database_url_env="PARTNER_CRM_DATABASE_URL",
        table="notes",
    )
    assert source.database_url_env == "PARTNER_CRM_DATABASE_URL"

