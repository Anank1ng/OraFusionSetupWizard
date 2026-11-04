"""Regression: Oracle UI 'Allow negative balances' is not cycle-count negative flag."""

from services.config_loader import load_schema
from services.io_parameter_reference import build_reference_template
from services.payload_builder import build_payload_from_row


def test_negative_balances_ui_and_onhand_cycle_count_have_distinct_rest_fields():
    mapping = load_schema("io_parameters_update")
    by_field = {item["excel_column"]: item for item in mapping["fields"]}
    ui = by_field["NegativeInvReceiptFlag"]
    cycle = by_field["AllowNegativeOnhandTransactionsFlag"]
    assert ui["label"] == "Allow negative balances"
    assert ui["payload_path"] == "NegativeInvReceiptFlag"
    assert cycle["payload_path"] == "AllowNegativeOnhandTransactionsFlag"
    assert ui["type"] == cycle["type"] == "boolean"
    assert ui["include_in_standard"] is True

    rows = [{
        "org": {
            "OrganizationCode": "001",
            "OrganizationId": 300000047274444,
        },
        "params": {
            "OrganizationId2": 300000047274444,
            "NegativeInvReceiptFlag": True,
            "AllowNegativeOnhandTransactionsFlag": False,
        },
        "error": "",
    }]
    frame = build_reference_template(rows, mapping)
    assert frame.at[0, "NegativeInvReceiptFlag"] is True
    assert frame.at[0, "AllowNegativeOnhandTransactionsFlag"] is False
    payload = build_payload_from_row(frame.iloc[0], mapping)
    assert payload["NegativeInvReceiptFlag"] is True
    assert payload["AllowNegativeOnhandTransactionsFlag"] is False


def test_missing_negative_balances_field_is_never_filled_from_other_flag():
    mapping = load_schema("io_parameters_update")
    rows = [{
        "org": {"OrganizationCode": "001", "OrganizationId": 300000047274444},
        "params": {
            "OrganizationId2": 300000047274444,
            "AllowNegativeOnhandTransactionsFlag": False,
        },
        "error": "",
    }]
    frame = build_reference_template(rows, mapping)
    assert frame.at[0, "NegativeInvReceiptFlag"] == ""
    payload = build_payload_from_row(frame.iloc[0], mapping)
    assert "NegativeInvReceiptFlag" not in payload
    assert payload["AllowNegativeOnhandTransactionsFlag"] is False
