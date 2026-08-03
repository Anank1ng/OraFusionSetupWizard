from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from services.oracle_client import OracleFusionClient
from services.payload_builder import PayloadBuildError, build_payload_from_row, is_blank, parse_bool
from services.reference_service import fetch_inv_org_parameters


@dataclass
class RunnerConfig:
    dry_run: bool = True
    upsert_io: bool = False
    stop_on_error: bool = False


def _body_to_text(body: Any) -> str:
    try:
        import json
        return json.dumps(body, ensure_ascii=False, default=str)[:4000]
    except Exception:
        return str(body)[:4000]


def _mock_org_id(index: int) -> int:
    return 900000000000000 + index


def extract_response_id(body: Any, candidates: List[str]) -> Optional[int]:
    if not isinstance(body, dict):
        return None
    for key in candidates:
        if key in body and not is_blank(body[key]):
            try:
                return int(body[key])
            except Exception:
                return None
    return None


def resolve_organization_id(row: pd.Series, org_map: Dict[str, int]) -> Optional[int]:
    if "OrganizationId" in row and not is_blank(row.get("OrganizationId")):
        try:
            return int(row.get("OrganizationId"))
        except Exception:
            return None
    org_code = str(row.get("OrganizationCode", "")).strip()
    if org_code and org_code in org_map:
        return int(org_map[org_code])
    return None


def run_create_io(
    df: pd.DataFrame,
    mapping: Dict[str, Any],
    client: Optional[OracleFusionClient],
    config: RunnerConfig,
) -> tuple[pd.DataFrame, Dict[str, int]]:
    logs: List[Dict[str, Any]] = []
    org_map: Dict[str, int] = {}
    endpoint = mapping["endpoint"]

    for i, row in df.iterrows():
        org_code = str(row.get("OrganizationCode", "")).strip()
        try:
            payload = build_payload_from_row(row, mapping)
            if config.dry_run:
                org_id = _mock_org_id(i + 1)
                ok = True
                status_code = 0
                response_body = {"dry_run": True, "OrganizationId": org_id, "message": "Dry run only"}
            else:
                if client is None:
                    raise RuntimeError("Client Oracle belum tersedia")
                resp = client.post(endpoint, payload, upsert_mode=config.upsert_io)
                ok = resp.ok
                status_code = resp.status_code
                response_body = resp.body
                org_id = extract_response_id(resp.body, ["OrganizationId"])
            if ok and org_code and org_id:
                org_map[org_code] = int(org_id)
            logs.append({
                "step": "Create IO",
                "excel_row": int(i) + 2,
                "business_key": org_code,
                "success": ok,
                "status_code": status_code,
                "OrganizationId": org_map.get(org_code),
                "request_payload": payload,
                "response_body": response_body,
                "message": "OK" if ok else _body_to_text(response_body),
            })
        except Exception as exc:
            logs.append({
                "step": "Create IO",
                "excel_row": int(i) + 2,
                "business_key": org_code,
                "success": False,
                "status_code": None,
                "OrganizationId": None,
                "request_payload": None,
                "response_body": None,
                "message": str(exc),
            })
            if config.stop_on_error:
                break
    return pd.DataFrame(logs), org_map


def run_create_subinventories(
    df: pd.DataFrame,
    mapping: Dict[str, Any],
    client: Optional[OracleFusionClient],
    config: RunnerConfig,
    org_map: Dict[str, int],
) -> pd.DataFrame:
    logs: List[Dict[str, Any]] = []
    endpoint = mapping["endpoint"]
    for i, row in df.iterrows():
        subinv = str(row.get("SecondaryInventoryName", "")).strip()
        org_code = str(row.get("OrganizationCode", "")).strip()
        try:
            row = row.copy()
            org_id = resolve_organization_id(row, org_map)
            if org_id is None:
                raise PayloadBuildError("OrganizationId tidak ditemukan. Isi OrganizationId atau pastikan OrganizationCode berhasil dibuat/ditemukan.")
            row["OrganizationId"] = org_id
            payload = build_payload_from_row(row, mapping)
            if config.dry_run:
                ok = True
                status_code = 0
                response_body = {"dry_run": True, "message": "Dry run only"}
            else:
                if client is None:
                    raise RuntimeError("Client Oracle belum tersedia")
                resp = client.post(endpoint, payload)
                ok = resp.ok
                status_code = resp.status_code
                response_body = resp.body
            logs.append({
                "step": "Create Subinventory",
                "excel_row": int(i) + 2,
                "business_key": f"{org_code}/{subinv}",
                "success": ok,
                "status_code": status_code,
                "OrganizationId": row.get("OrganizationId"),
                "request_payload": payload,
                "response_body": response_body,
                "message": "OK" if ok else _body_to_text(response_body),
            })
        except Exception as exc:
            logs.append({
                "step": "Create Subinventory",
                "excel_row": int(i) + 2,
                "business_key": f"{org_code}/{subinv}",
                "success": False,
                "status_code": None,
                "OrganizationId": row.get("OrganizationId") if "OrganizationId" in row else None,
                "request_payload": None,
                "response_body": None,
                "message": str(exc),
            })
            if config.stop_on_error:
                break
    return pd.DataFrame(logs)


def resolve_inv_org_parameter_id(row: pd.Series, org_id: int, client: Optional[OracleFusionClient], dry_run: bool) -> Optional[int]:
    if "OrganizationId2" in row and not is_blank(row.get("OrganizationId2")):
        try:
            return int(row.get("OrganizationId2"))
        except Exception:
            return None
    if dry_run:
        return int(org_id)
    if client is None:
        return None
    df, _ = fetch_inv_org_parameters(client, int(org_id))
    if df.empty:
        return None
    first = df.iloc[0]
    for candidate in ["OrganizationId2", "OrganizationId", "OrgParameterId"]:
        if candidate in df.columns and not is_blank(first.get(candidate)):
            try:
                return int(first.get(candidate))
            except Exception:
                pass
    return int(org_id)


def run_patch_io_parameters(
    df: pd.DataFrame,
    mapping: Dict[str, Any],
    client: Optional[OracleFusionClient],
    config: RunnerConfig,
    org_map: Dict[str, int],
) -> pd.DataFrame:
    logs: List[Dict[str, Any]] = []
    for i, row in df.iterrows():
        org_code = str(row.get("OrganizationCode", "")).strip()
        try:
            row = row.copy()
            org_id = resolve_organization_id(row, org_map)
            if org_id is None:
                raise PayloadBuildError("OrganizationId tidak ditemukan. Isi OrganizationId atau pastikan OrganizationCode berhasil dibuat/ditemukan.")
            row["OrganizationId"] = org_id
            org_id2 = resolve_inv_org_parameter_id(row, org_id, client, config.dry_run)
            if org_id2 is None:
                raise PayloadBuildError("OrganizationId2 / invOrgParameters key tidak ditemukan. GET child invOrgParameters gagal/kosong.")
            row["OrganizationId2"] = org_id2
            payload = build_payload_from_row(row, mapping)
            endpoint = mapping["endpoint_template"].format(OrganizationId=org_id, OrganizationId2=org_id2)
            if config.dry_run:
                ok = True
                status_code = 0
                response_body = {"dry_run": True, "message": "Dry run only"}
            else:
                if client is None:
                    raise RuntimeError("Client Oracle belum tersedia")
                resp = client.patch(endpoint, payload)
                ok = resp.ok
                status_code = resp.status_code
                response_body = resp.body
            logs.append({
                "step": "Patch IO Parameters",
                "excel_row": int(i) + 2,
                "business_key": org_code,
                "success": ok,
                "status_code": status_code,
                "OrganizationId": org_id,
                "OrganizationId2": org_id2,
                "endpoint": endpoint,
                "request_payload": payload,
                "response_body": response_body,
                "message": "OK" if ok else _body_to_text(response_body),
            })
        except Exception as exc:
            logs.append({
                "step": "Patch IO Parameters",
                "excel_row": int(i) + 2,
                "business_key": org_code,
                "success": False,
                "status_code": None,
                "OrganizationId": row.get("OrganizationId") if "OrganizationId" in row else None,
                "OrganizationId2": row.get("OrganizationId2") if "OrganizationId2" in row else None,
                "endpoint": None,
                "request_payload": None,
                "response_body": None,
                "message": str(exc),
            })
            if config.stop_on_error:
                break
    return pd.DataFrame(logs)




def _extract_items_from_body(body: Any) -> List[Dict[str, Any]]:
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return body["items"]
    if isinstance(body, list):
        return body
    return []


def _row_value(row: pd.Series, candidates: List[str]) -> Any:
    for col in candidates:
        if col in row and not is_blank(row.get(col)):
            return row.get(col)
    return None


def _row_bool(row: pd.Series, col: str) -> bool:
    if col not in row or is_blank(row.get(col)):
        return False
    try:
        return bool(parse_bool(row.get(col)))
    except Exception:
        return False


def fetch_plant_parameters(client: OracleFusionClient, organization_id: int) -> tuple[pd.DataFrame, Any]:
    endpoint = f"/fscmRestApi/resources/11.13.18.05/inventoryOrganizations/{organization_id}/child/plantParameters"
    response = client.get(endpoint, params={"onlyData": "true", "limit": 10})
    return pd.DataFrame(_extract_items_from_body(response.body)), response


def _resolve_manufacturing_calendar_id(row: pd.Series, org_id: int, client: Optional[OracleFusionClient], dry_run: bool) -> Optional[int]:
    raw = _row_value(row, [
        "ManufacturingCalendarId",
        "PlantManufacturingCalendarId",
        "plantParameters.ManufacturingCalendarId",
        "ScheduleId",
        "invOrgParameters.ScheduleId",
    ])
    if not is_blank(raw):
        try:
            return int(float(raw))
        except Exception:
            raise PayloadBuildError(f"ManufacturingCalendarId tidak valid: {raw!r}")

    if dry_run:
        return 0

    if client is not None:
        # Most IOs already have invOrgParameters.ScheduleId from minimal create.
        # Reuse it as manufacturing calendar when explicit ManufacturingCalendarId isn't supplied.
        params_df, _ = fetch_inv_org_parameters(client, int(org_id))
        if not params_df.empty:
            first = params_df.iloc[0]
            for candidate in ["ScheduleId", "ManufacturingCalendarId"]:
                if candidate in params_df.columns and not is_blank(first.get(candidate)):
                    try:
                        return int(float(first.get(candidate)))
                    except Exception:
                        pass
    return None


def _resolve_subinventory_code(row: pd.Series, candidates: List[str], label: str) -> str:
    raw = _row_value(row, candidates)
    if is_blank(raw):
        raise PayloadBuildError(
            f"{label} wajib untuk membuat plantParameters. "
            "Isi dengan kode/nama subinventory yang sudah dibuat di IO tersebut."
        )
    value = str(raw).strip()
    if not value:
        raise PayloadBuildError(f"{label} tidak boleh kosong.")
    return value


def _plant_payload_from_row(row: pd.Series, org_id: int, client: Optional[OracleFusionClient], dry_run: bool) -> Dict[str, Any]:
    calendar_id = _resolve_manufacturing_calendar_id(row, org_id, client, dry_run)
    if calendar_id is None:
        raise PayloadBuildError(
            "ManufacturingCalendarId wajib untuk membuat plantParameters. "
            "Isi kolom ManufacturingCalendarId, atau pastikan IO punya invOrgParameters.ScheduleId yang bisa diambil app."
        )

    supply_subinv = _resolve_subinventory_code(
        row,
        [
            "DefSupplySubinv",
            "DefaultSupplySubinventory",
            "plantParameters.DefaultSupplySubinventory",
            "plantParameters.DefSupplySubinv",
        ],
        "DefaultSupplySubinventory / Default Supply Subinventory",
    )
    completion_subinv = _resolve_subinventory_code(
        row,
        [
            "DefCompltnSubinv",
            "DefaultCompletionSubinventory",
            "plantParameters.DefaultCompletionSubinventory",
            "plantParameters.DefCompltnSubinv",
        ],
        "DefaultCompletionSubinventory / Default Completion Subinventory",
    )

    payload: Dict[str, Any] = {
        "ManufacturingCalendarId": calendar_id,
        "DefaultSupplySubinventory": supply_subinv,
        "DefaultCompletionSubinventory": completion_subinv,
    }

    optional_fields = {
        "DefaultWorkMethod": "DefaultWorkMethod",
        "EnableProcessManufacturingFlag": "EnableProcessManufacturingFlag",
    }
    for col, payload_key in optional_fields.items():
        if col in row and not is_blank(row.get(col)):
            val = row.get(col)
            if col == "EnableProcessManufacturingFlag":
                val = parse_bool(val)
            payload[payload_key] = val
    return payload


def ensure_plant_parameters_for_usage(
    row: pd.Series,
    org_id: int,
    client: Optional[OracleFusionClient],
    dry_run: bool,
) -> Dict[str, Any]:
    """Create plantParameters when user marks an IO as Manufacturing/Maintenance.

    Oracle may accept PATCH on the parent inventory organization with HTTP 200, but the UI checkbox
    won't actually turn on unless the related child plantParameters row exists.
    """
    plant_payload = _plant_payload_from_row(row, org_id, client, dry_run)
    collection_endpoint = f"/fscmRestApi/resources/11.13.18.05/inventoryOrganizations/{org_id}/child/plantParameters"

    if dry_run:
        return {
            "action": "dry_run_create_plant_parameters_if_missing",
            "success": True,
            "status_code": 0,
            "endpoint": collection_endpoint,
            "request_payload": plant_payload,
            "response_body": {"dry_run": True, "message": "Dry run only"},
        }

    if client is None:
        raise RuntimeError("Client Oracle belum tersedia")

    plant_df, get_resp = fetch_plant_parameters(client, org_id)
    if not plant_df.empty:
        first = plant_df.iloc[0]
        plant_id = None
        for candidate in ["OrganizationId4", "OrganizationId", "PlantParameterId"]:
            if candidate in plant_df.columns and not is_blank(first.get(candidate)):
                try:
                    plant_id = int(float(first.get(candidate)))
                    break
                except Exception:
                    pass
        return {
            "action": "plant_parameters_already_exist",
            "success": True,
            "status_code": get_resp.status_code,
            "endpoint": collection_endpoint if plant_id is None else f"{collection_endpoint}/{plant_id}",
            "request_payload": None,
            "response_body": get_resp.body,
        }

    post_resp = client.post(collection_endpoint, plant_payload)
    return {
        "action": "create_plant_parameters",
        "success": post_resp.ok,
        "status_code": post_resp.status_code,
        "endpoint": collection_endpoint,
        "request_payload": plant_payload,
        "response_body": post_resp.body,
    }

def run_patch_organization_usage(
    df: pd.DataFrame,
    mapping: Dict[str, Any],
    client: Optional[OracleFusionClient],
    config: RunnerConfig,
    org_map: Dict[str, int],
) -> pd.DataFrame:
    """PATCH parent inventoryOrganizations/{OrganizationId} for Additional Usages style fields."""
    logs: List[Dict[str, Any]] = []
    for i, row in df.iterrows():
        org_code = str(row.get("OrganizationCode", "")).strip()
        try:
            row = row.copy()
            org_id = resolve_organization_id(row, org_map)
            if org_id is None:
                raise PayloadBuildError("OrganizationId tidak ditemukan. Isi OrganizationId atau pastikan OrganizationCode berhasil dibuat/ditemukan.")
            row["OrganizationId"] = org_id
            payload = build_payload_from_row(row, mapping)
            endpoint = mapping["endpoint_template"].format(OrganizationId=org_id)
            plant_result = None
            needs_plant_parameters = _row_bool(row, "ManufacturingPlantFlag") or _row_bool(row, "MaintenanceEnabledFlag")

            if config.dry_run:
                ok = True
                status_code = 0
                response_body = {"dry_run": True, "message": "Dry run only"}
                if needs_plant_parameters:
                    plant_result = ensure_plant_parameters_for_usage(row, org_id, client, config.dry_run)
            else:
                if client is None:
                    raise RuntimeError("Client Oracle belum tersedia")
                resp = client.patch(endpoint, payload)
                ok = resp.ok
                status_code = resp.status_code
                response_body = resp.body
                if ok and needs_plant_parameters:
                    plant_result = ensure_plant_parameters_for_usage(row, org_id, client, config.dry_run)
                    ok = bool(ok and plant_result.get("success"))

            message = "OK" if ok else _body_to_text(response_body)
            if plant_result:
                if plant_result.get("success"):
                    message = f"{message}; plantParameters: {plant_result.get('action')} OK"
                else:
                    message = f"{message}; plantParameters FAILED: {_body_to_text(plant_result.get('response_body'))}"

            logs.append({
                "step": "Patch Organization Usage",
                "excel_row": int(i) + 2,
                "business_key": org_code,
                "success": ok,
                "status_code": status_code,
                "OrganizationId": org_id,
                "endpoint": endpoint,
                "request_payload": payload,
                "response_body": response_body,
                "plant_parameters_action": plant_result.get("action") if plant_result else None,
                "plant_parameters_status_code": plant_result.get("status_code") if plant_result else None,
                "plant_parameters_endpoint": plant_result.get("endpoint") if plant_result else None,
                "plant_parameters_payload": plant_result.get("request_payload") if plant_result else None,
                "plant_parameters_response": plant_result.get("response_body") if plant_result else None,
                "message": message,
            })
        except Exception as exc:
            logs.append({
                "step": "Patch Organization Usage",
                "excel_row": int(i) + 2,
                "business_key": org_code,
                "success": False,
                "status_code": None,
                "OrganizationId": row.get("OrganizationId") if "OrganizationId" in row else None,
                "endpoint": None,
                "request_payload": None,
                "response_body": None,
                "message": str(exc),
            })
            if config.stop_on_error:
                break
    return pd.DataFrame(logs)

