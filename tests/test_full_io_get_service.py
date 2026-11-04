from dataclasses import dataclass

from services.full_io_get_service import (
    fetch_organizations, fetch_full_io, compare_parameters,
    build_copy_patch, snapshots_to_dataframes, compare_copyable_parameters,
)


@dataclass
class Response:
    ok: bool
    status_code: int
    body: object


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        params = params or {}
        self.calls.append((url, params))
        if url.endswith('/inventoryOrganizations'):
            offset = params.get('offset', 0)
            all_rows = [{"OrganizationId": x, "OrganizationCode": str(x)} for x in range(1, 6)]
            n = params.get('limit', 50)
            rows = all_rows[offset:offset+n]
            return Response(True, 200, {"items": rows, "hasMore": offset+n < 5})
        if url.endswith('/child/invOrgParameters'):
            return Response(True, 200, {"items": [{"OrganizationId2": 987, "ScheduleId": 123, "AllowNegativeOnhandTransactionsFlag": False}]})
        if url.endswith('/child/plantParameters'):
            return Response(True, 200, {"items": []})
        return Response(True, 200, {"OrganizationId": 42, "OrganizationCode": "IO42", "OrganizationName": "Demo"})


def test_paginated_fetch():
    fake = FakeClient()
    rows = fetch_organizations(fake, max_rows=5, page_size=2)
    assert len(rows) == 5
    assert [x[1]['offset'] for x in fake.calls] == [0, 2, 4]


def test_full_get_and_compare():
    fake = FakeClient()
    snap = fetch_full_io(fake, 42)
    assert snap['inventory_parameters'][0]['ScheduleId'] == 123
    assert not snap['plant_parameters']
    assert not snap['warnings']
    assert not compare_parameters(snap, snap)['Same'].eq(False).any()
    assert len(snapshots_to_dataframes([snap])['IO_Parent']) == 1


def test_copy_template_only_selected_values():
    source = {
        'parent': {'OrganizationId': 1, 'OrganizationCode': 'SOURCE'},
        'inventory_parameters': [{'ScheduleId': 123, 'AllowNegativeOnhandTransactionsFlag': False}],
    }
    target = {
        'parent': {'OrganizationId': 2, 'OrganizationCode': 'TARGET'},
        'inventory_parameters': [{'OrganizationId2': 876, 'ScheduleId': 234}],
    }
    mapping = {'fields': [
        {'excel_column': 'OrganizationCode', 'send_to_payload': False},
        {'excel_column': 'OrganizationId', 'send_to_payload': False},
        {'excel_column': 'OrganizationId2', 'send_to_payload': False},
        {'excel_column': 'ScheduleId', 'send_to_payload': True},
        {'excel_column': 'AllowNegativeOnhandTransactionsFlag', 'send_to_payload': True},
        {'excel_column': 'OrganizationName', 'send_to_payload': False},
    ]}
    df, filtered = build_copy_patch(source, target, mapping, ['ScheduleId'])
    assert df.loc[0, 'OrganizationId'] == 2
    assert df.loc[0, 'OrganizationId2'] == 876
    assert df.loc[0, 'ScheduleId'] == 123
    assert 'AllowNegativeOnhandTransactionsFlag' not in df.columns
    assert len(filtered['fields']) == 4


def _demo_mapping():
    return {"fields": [
        {"excel_column": "OrganizationCode", "send_to_payload": False},
        {"excel_column": "OrganizationId", "send_to_payload": False},
        {"excel_column": "OrganizationId2", "send_to_payload": False},
        {"excel_column": "SerialNumberType", "label": "Serial Type", "send_to_payload": True, "type": "string", "payload_path": "SerialNumberType"},
        {"excel_column": "AllowNegativeOnhandTransactionsFlag", "send_to_payload": True, "type": "boolean", "payload_path": "AllowNegativeOnhandTransactionsFlag"},
        {"excel_column": "ScheduleId", "send_to_payload": True, "type": "integer", "payload_path": "ScheduleId"},
        {"excel_column": "AutoLotAlphaPrefix", "send_to_payload": True, "type": "string", "payload_path": "AutoLotAlphaPrefix"},
        {"excel_column": "ReadOnlyField", "send_to_payload": False},
    ]}


def test_compare_copyable_only_mapped_nonempty_fields():
    source = {
        "parent": {"OrganizationId": 1, "OrganizationCode": "SRC"},
        "inventory_parameters": [{
            "SerialNumberType": "1", "AllowNegativeOnhandTransactionsFlag": False,
            "ScheduleId": 0, "AutoLotAlphaPrefix": None,
            "ReadOnlyField": "internal", "UnmappedField": "not safe",
        }],
    }
    target = {
        "parent": {"OrganizationId": 2, "OrganizationCode": "DST"},
        "inventory_parameters": [{
            "OrganizationId2": 876, "SerialNumberType": "2",
            "AllowNegativeOnhandTransactionsFlag": True, "ScheduleId": 0,
        }],
    }
    table = compare_copyable_parameters(source, target, _demo_mapping())
    assert table["Field"].tolist() == [
        "SerialNumberType", "AllowNegativeOnhandTransactionsFlag", "ScheduleId",
    ]
    assert table["Status"].tolist() == ["Different", "Different", "Same"]
    assert table.loc[1, "Reference Value"] == "False"
    assert table.loc[2, "Reference Value"] == "0"


def test_copy_rejects_unmapped_and_missing_values_and_keeps_false():
    source = {
        "parent": {"OrganizationId": 1, "OrganizationCode": "SRC"},
        "inventory_parameters": [{
            "AllowNegativeOnhandTransactionsFlag": False,
            "ScheduleId": 0, "AutoLotAlphaPrefix": None,
            "ReadOnlyField": "internal",
        }],
    }
    target = {
        "parent": {"OrganizationId": 2, "OrganizationCode": "DST"},
        "inventory_parameters": [{"OrganizationId2": 876}],
    }
    mapping = _demo_mapping()
    df, filtered = build_copy_patch(
        source, target, mapping, ["AllowNegativeOnhandTransactionsFlag", "ScheduleId"]
    )
    assert df.loc[0, "OrganizationId"] == 2
    assert df.loc[0, "OrganizationId2"] == 876
    assert df.loc[0, "AllowNegativeOnhandTransactionsFlag"] == False
    assert df.loc[0, "ScheduleId"] == 0
    assert "ReadOnlyField" not in df.columns
    assert {field["excel_column"] for field in filtered["fields"]} == {
        "OrganizationCode", "OrganizationId", "OrganizationId2",
        "AllowNegativeOnhandTransactionsFlag", "ScheduleId",
    }

    import pytest
    with pytest.raises(ValueError, match="bukan parameter"):
        build_copy_patch(source, target, mapping, ["ReadOnlyField"])
    with pytest.raises(ValueError, match="kosong/tidak tersedia"):
        build_copy_patch(source, target, mapping, ["AutoLotAlphaPrefix"])
