import pandas as pd

from services.config_loader import filter_mapping_by_preset, load_schema
from services.payload_builder import build_payload_from_row


def test_minimal_io_payload():
    mapping = filter_mapping_by_preset(load_schema("minimal_inventory_organizations"), "minimal")
    row = pd.Series({
        "OrganizationCode": "IO_TEST01",
        "OrganizationName": "IO Test 01",
        "ManagementBusinessUnitId": 204,
        "LegalEntityId": 204,
        "ProfitCenterBusinessUnitId": 204,
        "Status": "Active",
        "LocationId": 1001,
        "InventoryFlag": True,
        "MasterOrganizationId": 204,
        "ItemGroupingCode": "ORA_RCS_IGB_DFTN",
        "ItemDefinitionOrganizationCode": "",
        "invOrgParameters.ScheduleId": 300000047225452,
    })
    payload = build_payload_from_row(row, mapping)
    assert payload["ItemDefinitionOrganizationCode"] == "IO_TEST01"
    assert payload["invOrgParameters"][0]["ScheduleId"] == 300000047225452


def test_subinventory_payload():
    mapping = filter_mapping_by_preset(load_schema("subinventories"), "minimal")
    row = pd.Series({
        "OrganizationCode": "IO_TEST01",
        "OrganizationId": 123,
        "SecondaryInventoryName": "RAW",
        "MaterialStatusCode": "Active",
        "Description": "Raw Material",
    })
    payload = build_payload_from_row(row, mapping)
    assert payload["OrganizationId"] == 123
    assert payload["SecondaryInventoryName"] == "RAW"
    assert "OrganizationCode" not in payload


def test_patch_params_payload():
    mapping = filter_mapping_by_preset(load_schema("io_parameters_update"), "standard")
    row = pd.Series({
        "OrganizationCode": "IO_TEST01",
        "OrganizationId": 123,
        "OrganizationId2": 123,
        "ScheduleId": 300000047225452,
        "DefaultSupplySubinventory": "RAW",
        "DefaultCompletionSubinventory": "FG",
        "UseCurrentItemCostFlag": True,
    })
    payload = build_payload_from_row(row, mapping)
    assert payload["ScheduleId"] == 300000047225452
    assert payload["DefaultSupplySubinventory"] == "RAW"
    assert "OrganizationCode" not in payload
