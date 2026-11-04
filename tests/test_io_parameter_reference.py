"""Regression tests for Oracle-backed IO parameter template selection."""

import pandas as pd
import pytest

from services.io_parameter_reference import (
    build_reference_template,
    child_parameter_key,
    missing_child_key_warnings,
    clean_editor_dataframe,
    is_missing,
    nullable_checkbox_value,
    preserve_untouched_blank_booleans,
    reference_status_table,
    selection_signature,
    validate_reference_rows,
)


MAPPING = {
    "fields": [
        {"excel_column": "OrganizationCode", "send_to_payload": False, "sample": "IO_TEST01"},
        {"excel_column": "OrganizationName", "send_to_payload": False},
        {"excel_column": "OrganizationId", "send_to_payload": False},
        {"excel_column": "OrganizationId2", "send_to_payload": False},
        {"excel_column": "ScheduleId", "send_to_payload": True, "sample": 999},
        {"excel_column": "UseCurrentItemCostFlag", "send_to_payload": True, "type": "boolean", "sample": True},
        {"excel_column": "StockLocatorControl", "send_to_payload": True, "sample": "SAMPLE"},
    ]
}


def _entry(code="IO105", org_id=105, params=None, error=""):
    if params is None:
        params = {
            "OrganizationId2": 876,
            "ScheduleId": 123,
            "UseCurrentItemCostFlag": False,
        }
    return {
        "org": {"OrganizationCode": code, "OrganizationName": f"Warehouse {code}", "OrganizationId": org_id},
        "params": params, "error": error,
    }


def test_signature_changes_with_selected_io_instance_user_or_list_generation():
    a = [_entry()]
    first = selection_signature("https://test.example.com/", "integration", [a[0]["org"]], 1)
    assert first == selection_signature("https://test.example.com", "integration", [a[0]["org"]], 1)
    assert first != selection_signature("https://prod.example.com", "integration", [a[0]["org"]], 1)
    assert first != selection_signature("https://test.example.com", "other", [a[0]["org"]], 1)
    assert first != selection_signature("https://test.example.com", "integration", [_entry(org_id=106)["org"]], 1)
    assert first != selection_signature("https://test.example.com", "integration", [a[0]["org"]], 2)


def test_reference_template_uses_actual_io_and_child_values_not_samples():
    rows = [_entry()]
    template = build_reference_template(rows, MAPPING)
    assert len(template) == 1
    assert template.iloc[0]["OrganizationCode"] == "IO105"
    assert template.iloc[0]["OrganizationId"] == 105
    assert template.iloc[0]["OrganizationId2"] == 876
    assert template.iloc[0]["ScheduleId"] == 123
    assert template.iloc[0]["UseCurrentItemCostFlag"] == False
    # Unavailable optional field stays empty. Do not copy SAMPLE from schema.
    assert template.iloc[0]["StockLocatorControl"] == ""


def test_reference_template_supports_multiple_selected_io_rows_without_mixup():
    rows = [_entry(code="IO105", org_id=105),
            _entry(code="IO107", org_id=107, params={
                "OrganizationId2": 987, "ScheduleId": 777, "UseCurrentItemCostFlag": True,
            })]
    df = build_reference_template(rows, MAPPING)
    assert df["OrganizationCode"].tolist() == ["IO105", "IO107"]
    assert df["OrganizationId2"].tolist() == [876, 987]
    assert df["ScheduleId"].tolist() == [123, 777]


def test_missing_child_key_yields_excel_warning_but_failed_get_still_blocks():
    missing = _entry(params={"ScheduleId": 234})
    assert child_parameter_key(missing["params"]) == ""
    assert validate_reference_rows([missing]) == []
    assert missing_child_key_warnings([missing])
    template = build_reference_template([missing], MAPPING)
    assert template.loc[0, "OrganizationId2"] == ""
    assert template.loc[0, "ScheduleId"] == 234

    failed = _entry(code="IO106", org_id=106, params={}, error="HTTP 403 Forbidden")
    assert any("403" in issue for issue in validate_reference_rows([failed]))
    with pytest.raises(ValueError):
        build_reference_template([_entry(), failed], MAPPING)

    table = reference_status_table([missing, failed], MAPPING)
    assert table["GET Status"].tolist() == ["KEY PENDING", "ERROR"]


def test_child_key_uses_parent_matching_oracle_self_link_only():
    source = {"links": [{
        "rel": "self",
        "href": "https://example.oraclecloud.com/fscmRestApi/resources/11.13.18.05/inventoryOrganizations/105/child/invOrgParameters/999001?foo=bar"
    }]}
    assert child_parameter_key(source, 105) == "999001"
    assert child_parameter_key(source, 106) == ""
    source["links"][0]["href"] = "https://example.com/inventoryOrganizations/105/child/invOrgParameters"
    assert child_parameter_key(source, 105) == ""
    assert child_parameter_key({"OrganizationId": 105}, 105) == ""


def test_blank_values_remain_blank_and_false_zero_are_not_missing():
    data = _entry(params={
        "OrganizationId2": 888,
        "ScheduleId": 0,
        "UseCurrentItemCostFlag": False,
        "StockLocatorControl": None,
    })
    df = build_reference_template([data], MAPPING)
    assert df.loc[0, "ScheduleId"] == 0
    assert df.loc[0, "UseCurrentItemCostFlag"] == False
    assert df.loc[0, "StockLocatorControl"] == ""
    assert is_missing(pd.NA)
    assert is_missing(float("nan"))
    assert not is_missing(False)
    assert not is_missing(0)
    assert nullable_checkbox_value(None) is None
    assert nullable_checkbox_value("") is None
    assert nullable_checkbox_value(False) is False
    assert nullable_checkbox_value("0") is False
    assert nullable_checkbox_value("1") is True


def test_untouched_null_boolean_does_not_become_false_but_manual_false_is_kept():
    mapping = {"fields": [{"excel_column": "Flag", "type": "boolean"}]}
    original = pd.DataFrame({"Flag": [None, None, True]})
    rendered = pd.DataFrame({"Flag": [False, False, False]})
    # Simulated Streamlit data_editor delta: only the second row was explicitly edited.
    state = {"edited_rows": {1: {"Flag": False}, 2: {"Flag": False}}}
    result = preserve_untouched_blank_booleans(original, rendered, mapping, state)
    assert result.loc[0, "Flag"] is None
    assert result.loc[1, "Flag"] is False
    assert result.loc[2, "Flag"] is False
    assert clean_editor_dataframe(pd.DataFrame({"Flag": [pd.NA]})).loc[0, "Flag"] is None
