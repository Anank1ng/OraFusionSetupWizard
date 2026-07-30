from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from services.oracle_client import OracleFusionClient, OracleResponse

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
    endpoint = f"/fscmRestApi/resources/11.13.18.05/inventoryOrganizations/{organization_id}/child/invOrgParameters"
    response = client.get(endpoint, params={"onlyData": "true", "limit": 10})
    return pd.DataFrame(extract_items(response.body)), response


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
