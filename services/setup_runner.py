from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd

from services.oracle_client import OracleFusionClient
from services.payload_builder import PayloadBuildError, build_payload_from_row, is_blank
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
                "step": "Patch Organization Usage",
                "excel_row": int(i) + 2,
                "business_key": org_code,
                "success": ok,
                "status_code": status_code,
                "OrganizationId": org_id,
                "endpoint": endpoint,
                "request_payload": payload,
                "response_body": response_body,
                "message": "OK" if ok else _body_to_text(response_body),
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

