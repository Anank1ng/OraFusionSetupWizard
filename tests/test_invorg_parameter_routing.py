"""Oracle invOrgParameters routing must use verified keys, not guesses."""
from dataclasses import dataclass

import pandas as pd

from services.reference_service import fetch_inv_org_parameters
from services.setup_runner import resolve_inv_org_parameter_id


@dataclass
class Response:
    ok: bool
    status_code: int
    body: object


class FakeClient:
    def __init__(self, items, probe_ok=False, probe_org=None):
        self.items = items
        self.probe_ok = probe_ok
        self.probe_org = probe_org
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if url.endswith("/child/invOrgParameters"):
            return Response(True, 200, {"items": self.items})
        if self.probe_ok:
            return Response(True, 200, {"OrganizationId": self.probe_org})
        return Response(False, 404, {"detail": "Not found"})


def test_get_preserves_oracle_item_self_link_to_resolve_key():
    client = FakeClient([{
        "OrganizationId": 199,
        "ScheduleId": 123,
        "links": [{
            "rel": "self",
            "href": "https://example.test/fscmRestApi/resources/11.13.18.05/"
                    "inventoryOrganizations/199/child/invOrgParameters/88888888",
        }],
    }])
    df, response = fetch_inv_org_parameters(client, 199)
    assert response.ok
    assert df.loc[0, "OrganizationId2"] == "88888888"
    assert df.loc[0, "_ChildKeySource"] == "Oracle item self/canonical link"
    assert len(client.calls) == 1
    assert client.calls[0][1]["onlyData"] == "false"
    assert resolve_inv_org_parameter_id(
        pd.Series({"OrganizationId2": ""}), 199, client, False
    ) == 88888888


def test_get_probe_candidate_only_after_successful_readonly_item_get():
    candidate = FakeClient([{"OrganizationId": 199, "ScheduleId": 123}],
                           probe_ok=True, probe_org=199)
    df, _ = fetch_inv_org_parameters(candidate, 199)
    assert len(candidate.calls) == 2
    assert df.loc[0, "OrganizationId2"] == "199"
    assert df.loc[0, "_ChildKeySource"] == "Verified by child-item GET"
    assert resolve_inv_org_parameter_id(
        pd.Series({"OrganizationId2": ""}), 199, candidate, False
    ) == 199


def test_mismatch_or_failed_probe_does_not_invent_child_key():
    for probe_ok, probe_org in [(False, None), (True, 111)]:
        client = FakeClient([{"OrganizationId": 199, "ScheduleId": 123}],
                            probe_ok=probe_ok, probe_org=probe_org)
        df, _ = fetch_inv_org_parameters(client, 199)
        assert "OrganizationId2" not in df.columns
        assert resolve_inv_org_parameter_id(
            pd.Series({"OrganizationId2": ""}), 199, client, False
        ) is None


def test_dry_run_uses_unambiguous_placeholder_not_parent_id():
    row = pd.Series({"OrganizationId2": ""})
    assert resolve_inv_org_parameter_id(row, 199, None, True) == "RESOLVE_ON_LIVE_GET"
