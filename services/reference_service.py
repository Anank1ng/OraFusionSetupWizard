from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from services.oracle_client import OracleFusionClient, OracleResponse
from services.io_parameter_reference import child_parameter_key, is_missing

REFERENCE_COLLECTIONS: Dict[str, Dict[str, Any]] = {
    "Business Units": {
        "endpoint": "/fscmRestApi/resources/11.13.18.05/finBusinessUnitsLOV",
        "id_candidates": ["BusinessUnitId", "BUId"],
        "name_candidates": ["Name", "BusinessUnitName"],
        "description": "Untuk ManagementBusinessUnitId.",
    },
    "Profit Center Business Units": {
        "endpoint": "/fscmRestApi/resources/11.13.18.05/finBusinessUnitsLOV",
        "id_candidates": ["BusinessUnitId", "BUId"],
        "name_candidates": ["Name", "BusinessUnitName"],
        "fixed_q_filter": "ProfitCenterFlag=true",
        "description": "Untuk ProfitCenterBusinessUnitId. Sumber sama dengan BU, difilter ProfitCenterFlag=true.",
    },
    "Legal Entities": {
        "endpoint": "/fscmRestApi/resources/11.13.18.05/legalEntitiesLOV",
        "id_candidates": ["LegalEntityId", "LegalEntityIdentifier"],
        "name_candidates": ["Name", "LegalEntityName"],
        "description": "Untuk LegalEntityId.",
    },
    "Inventory Organizations LOV": {
        "endpoint": "/fscmRestApi/resources/11.13.18.05/inventoryOrganizationsLOV",
        "id_candidates": ["OrganizationId"],
        "name_candidates": ["OrganizationName", "OrganizationCode"],
        "description": "Untuk MasterOrganizationId, ItemDefinitionOrganizationId, dan lookup OrganizationCode → OrganizationId.",
    },
    "Schedules": {
        "endpoint": "/fscmRestApi/resources/11.13.18.05/schedules",
        "id_candidates": ["ScheduleId"],
        "name_candidates": ["Name", "ScheduleName"],
        "description": "Untuk invOrgParameters.ScheduleId.",
    },
    "Locations LOV": {
        "endpoint": "/hcmRestApi/resources/11.13.18.05/locationsLov",
        "id_candidates": ["LocationId", "LocationID"],
        "name_candidates": ["LocationName", "Name", "LocationCode", "AddressLine1"],
        "description": "Untuk LocationId.",
    },
    "Material Statuses": {
        "endpoint": "/fscmRestApi/resources/11.13.18.05/materialStatuses",
        "id_candidates": ["MaterialStatusId", "StatusId"],
        "name_candidates": ["MaterialStatusCode", "StatusCode", "MaterialStatus", "Description"],
        "description": "Untuk MaterialStatusCode/MaterialStatusId pada Subinventory.",
    },
}


def extract_items(response_body: Any) -> List[Dict[str, Any]]:
    if isinstance(response_body, dict) and isinstance(response_body.get("items"), list):
        return response_body["items"]
    if isinstance(response_body, list):
        return response_body
    return []


def fetch_reference_collection(
    client: OracleFusionClient,
    config: Dict[str, Any],
    limit: int = 100,
    q_filter: str = "",
) -> tuple[pd.DataFrame, OracleResponse]:
    params: Dict[str, Any] = {"limit": limit, "onlyData": "true"}
    q_parts = []
    if config.get("fixed_q_filter"):
        q_parts.append(config["fixed_q_filter"])
    if q_filter:
        q_parts.append(q_filter)
    if q_parts:
        params["q"] = " AND ".join(q_parts)
    response = client.get(config["endpoint"], params=params)
    items = extract_items(response.body)
    df = pd.DataFrame(items)
    return df, response


def fetch_all_selected_references(client: OracleFusionClient, selected_names: List[str], limit: int = 100, q_filter: str = "") -> Dict[str, pd.DataFrame]:
    results: Dict[str, pd.DataFrame] = {}
    for name in selected_names:
        df, _ = fetch_reference_collection(client, REFERENCE_COLLECTIONS[name], limit=limit, q_filter=q_filter)
        results[name] = df
    return results


def fetch_inventory_orgs(client: OracleFusionClient, limit: int = 60, q_filter: str = "", expand_children: bool = True) -> tuple[pd.DataFrame, OracleResponse]:
    params: Dict[str, Any] = {"limit": limit, "onlyData": "true"}
    if q_filter:
        params["q"] = q_filter
    if expand_children:
        params["expand"] = "invOrgParameters,plantParameters"
    response = client.get("/fscmRestApi/resources/11.13.18.05/inventoryOrganizations", params=params)
    return pd.DataFrame(extract_items(response.body)), response


def fetch_inv_org_parameters(client: OracleFusionClient, organization_id: int) -> tuple[pd.DataFrame, OracleResponse]:
    """GET data plus Oracle-generated item links needed for PATCH routing.

    Never infer a child key from a parent attribute without confirming a
    successful child item GET. A blank unresolved key is allowed in the Excel
    template; the live runner must resolve it or fail before PATCH.
    """
    endpoint = f"/fscmRestApi/resources/11.13.18.05/inventoryOrganizations/{organization_id}/child/invOrgParameters"
    response = client.get(endpoint, params={"onlyData": "false", "limit": 10})
    if not response.ok:
        return pd.DataFrame(), response
    items = extract_items(response.body)
    for item in items:
        key = child_parameter_key(item, organization_id)
        if not is_missing(key):
            if is_missing(item.get("OrganizationId2")):
                item["OrganizationId2"] = key
                item["_ChildKeySource"] = "Oracle item self/canonical link"
            else:
                item["_ChildKeySource"] = "Oracle field"
    # Some Oracle configurations suppress item links. Before giving up,
    # probe a *read-only* single-item URL using the parent ID as a candidate.
    # Only an actual HTTP 200 child response confirms this candidate is usable.
    if len(items) == 1 and is_missing(child_parameter_key(items[0], organization_id)):
        candidate = str(organization_id)
        try:
            item_response = client.get(
                f"{endpoint}/{candidate}", params={"onlyData": "false"}
            )
            body = item_response.body
            if item_response.ok and isinstance(body, dict):
                returned_org = body.get("OrganizationId")
                if is_missing(returned_org) or str(returned_org) == candidate:
                    items[0]["OrganizationId2"] = candidate
                    items[0]["_ChildKeySource"] = "Verified by child-item GET"
        except Exception:
            pass  # A failed read-only probe must never be promoted to a key.
    return pd.DataFrame(items), response


def organization_lookup_from_reference(df: pd.DataFrame) -> Dict[str, int]:
    lookup: Dict[str, int] = {}
    if df.empty:
        return lookup
    for _, row in df.iterrows():
        org_id = row.get("OrganizationId")
        for key_col in ["OrganizationCode", "OrganizationName"]:
            val = row.get(key_col)
            if pd.notna(val) and val != "" and pd.notna(org_id):
                try:
                    lookup[str(val)] = int(org_id)
                except Exception:
                    pass
    return lookup
