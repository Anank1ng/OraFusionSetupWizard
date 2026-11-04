"""Read-only Full Inventory Organization reference helpers.

Designed to plug into the existing OraFusionSetupWizard OracleFusionClient.
No mutation methods are called by this module.
"""
from __future__ import annotations

import io
import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Dict, List, Sequence

import pandas as pd

if TYPE_CHECKING:
    from services.oracle_client import OracleFusionClient

ORG_ENDPOINT = "/fscmRestApi/resources/11.13.18.05/inventoryOrganizations"
CHILDREN = {
    "invOrgParameters": "inventory_parameters",
    "plantParameters": "plant_parameters",
}


def _items(body: Any) -> List[Dict[str, Any]]:
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return [entry for entry in body["items"] if isinstance(entry, dict)]
    if isinstance(body, list):
        return [entry for entry in body if isinstance(entry, dict)]
    return []


def fetch_organizations(
    client: OracleFusionClient,
    max_rows: int = 100,
    query: str = "",
    page_size: int = 50,
) -> List[Dict[str, Any]]:
    """GET multiple pages, honoring Oracle offset/hasMore (not slicing locally)."""
    max_rows = max(1, min(int(max_rows), 500))
    page_size = max(1, min(int(page_size), 100))
    result: List[Dict[str, Any]] = []
    offset = 0
    seen_ids = set()

    while len(result) < max_rows:
        limit = min(page_size, max_rows - len(result))
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "onlyData": "true",
        }
        if query.strip():
            params["q"] = query.strip()
        response = client.get(ORG_ENDPOINT, params=params)
        if not response.ok:
            raise RuntimeError(
                f"Fetch IO gagal (HTTP {response.status_code}): "
                f"{str(response.body)[:600]}"
            )
        body = response.body if isinstance(response.body, dict) else {}
        page = _items(response.body)
        if not page:
            break
        for row in page:
            org_id = row.get("OrganizationId")
            dedup_id = str(org_id) if org_id is not None else f"row_{offset}_{len(result)}"
            if dedup_id not in seen_ids:
                seen_ids.add(dedup_id)
                result.append(row)
                if len(result) >= max_rows:
                    break
        offset += len(page)
        if not body.get("hasMore", len(page) == limit) or len(page) < limit:
            break
    return result


def fetch_full_io(client: OracleFusionClient, organization_id: int) -> Dict[str, Any]:
    """Fetch parent and both child collections separately; preserve partial child errors."""
    org_id = int(organization_id)
    endpoint = f"{ORG_ENDPOINT}/{org_id}"
    response = client.get(endpoint, params={"onlyData": "true"})
    if not response.ok or not isinstance(response.body, dict):
        raise RuntimeError(
            f"GET parent IO {org_id} gagal (HTTP {response.status_code}): "
            f"{str(response.body)[:600]}"
        )
    snapshot: Dict[str, Any] = {
        "parent": response.body,
        "inventory_parameters": [],
        "plant_parameters": [],
        "warnings": [],
    }
    for child, key in CHILDREN.items():
        child_resp = client.get(
            f"{endpoint}/child/{child}",
            params={"onlyData": "true", "limit": 10},
        )
        if child_resp.ok:
            snapshot[key] = _items(child_resp.body)
        else:
            snapshot["warnings"].append(
                f"{child}: HTTP {child_resp.status_code} - {str(child_resp.body)[:180]}"
            )
    return snapshot


def first_parameter(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    items = snapshot.get("inventory_parameters") or []
    return items[0] if items and isinstance(items[0], dict) else {}


def get_parameter_key(snapshot: Dict[str, Any]) -> Any:
    row = first_parameter(snapshot)
    for name in ("OrganizationId2", "OrgParameterId", "InvOrgParameterId", "ParameterId"):
        if row.get(name) not in (None, ""):
            return row[name]
    # Never guess child OrganizationId2 from the parent OrganizationId.
    return ""


def _flat_scalar_row(obj: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in obj.items()
        if value is None or isinstance(value, (str, int, float, bool))
    }


def snapshots_to_dataframes(snapshots: Sequence[Dict[str, Any]]) -> Dict[str, pd.DataFrame]:
    parent_rows = []
    inventory_rows = []
    plant_rows = []
    errors = []
    for snapshot in snapshots:
        org = snapshot.get("parent") or {}
        identity = {
            "OrganizationCode": org.get("OrganizationCode"),
            "OrganizationName": org.get("OrganizationName"),
            "OrganizationId": org.get("OrganizationId"),
        }
        parent_rows.append(_flat_scalar_row(org))
        for param in snapshot.get("inventory_parameters") or []:
            inventory_rows.append({**identity, **_flat_scalar_row(param)})
        for param in snapshot.get("plant_parameters") or []:
            plant_rows.append({**identity, **_flat_scalar_row(param)})
        for warning in snapshot.get("warnings") or []:
            errors.append({**identity, "Warning": warning})
    return {
        "IO_Parent": pd.DataFrame(parent_rows),
        "InvOrg_Parameters": pd.DataFrame(inventory_rows),
        "Plant_Parameters": pd.DataFrame(plant_rows),
        "Fetch_Warnings": pd.DataFrame(errors),
    }


def compare_parameters(source: Dict[str, Any], target: Dict[str, Any]) -> pd.DataFrame:
    source_params = first_parameter(source)
    target_params = first_parameter(target)
    names = sorted((set(source_params) | set(target_params)) - {"links"})
    rows = []
    for field in names:
        src = source_params.get(field)
        dst = target_params.get(field)
        if isinstance(src, (dict, list)) or isinstance(dst, (dict, list)):
            continue
        rows.append({
            "Field": field,
            "Reference IO": src,
            "Target IO": dst,
            "Same": src == dst,
        })
    return pd.DataFrame(rows)



def _copy_value_available(value: Any) -> bool:
    """False and zero are valid PATCH values; absent/blank values are not."""
    if value is None or isinstance(value, (dict, list)):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _display_parameter_value(value: Any) -> str:
    if not _copy_value_available(value):
        return "—"
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def compare_copyable_parameters(
    source: Dict[str, Any],
    target: Dict[str, Any],
    base_mapping: Dict[str, Any],
) -> pd.DataFrame:
    """Only show PATCH-mapped child fields with a usable source value.

    Preserve typed values in the source snapshot for build_copy_patch().
    These strings are UI display values only, never sent to Oracle.
    """
    columns = ["Field", "Parameter", "Section", "Reference Value", "Target Value", "Status"]
    source_params = first_parameter(source)
    target_params = first_parameter(target)
    rows = []
    for field in base_mapping.get("fields") or []:
        column = field.get("excel_column")
        if not column or not field.get("send_to_payload", True):
            continue
        if column not in source_params or not _copy_value_available(source_params[column]):
            continue
        src_value = source_params[column]
        dst_value = target_params.get(column)
        rows.append({
            "Field": column,
            "Parameter": field.get("label") or column,
            "Section": field.get("section") or "General",
            "Reference Value": _display_parameter_value(src_value),
            "Target Value": _display_parameter_value(dst_value),
            "Status": "Same" if src_value == dst_value else "Different",
        })
    return pd.DataFrame(rows, columns=columns)


def build_copy_patch(
    source: Dict[str, Any],
    target: Dict[str, Any],
    base_mapping: Dict[str, Any],
    selected_columns: Sequence[str],
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Route to TARGET, copy only explicitly chosen POST-safe child params from SOURCE."""
    source_params = first_parameter(source)
    if not source_params:
        raise ValueError("IO referensi tidak mempunyai invOrgParameters.")
    target_parent = target.get("parent") or {}
    if not target_parent.get("OrganizationId"):
        raise ValueError("OrganizationId target tidak ada.")
    fields = base_mapping.get("fields") or []
    approved = {
        f["excel_column"]: f
        for f in fields
        if f.get("excel_column") and f.get("send_to_payload", True)
    }
    requested = list(dict.fromkeys(selected_columns))
    if not requested:
        raise ValueError("Pilih minimal satu field parameter yang akan dicopy.")
    invalid = [col for col in requested if col not in approved]
    if invalid:
        raise ValueError("Field ini bukan parameter yang boleh di-PATCH: " + ", ".join(invalid))
    missing = [col for col in requested if not _copy_value_available(source_params.get(col))]
    if missing:
        raise ValueError("Field ini kosong/tidak tersedia dari GET IO referensi: " + ", ".join(missing))

    route_keys = ("OrganizationCode", "OrganizationId", "OrganizationId2")
    chosen_set = set(requested) | set(route_keys)
    chosen = [deepcopy(f) for f in fields if f.get("excel_column") in chosen_set]
    mapping = deepcopy(base_mapping)
    mapping["fields"] = chosen

    row = {
        "OrganizationCode": target_parent.get("OrganizationCode", ""),
        "OrganizationId": target_parent.get("OrganizationId", ""),
        "OrganizationId2": get_parameter_key(target),
    }
    for col in requested:
        row[col] = source_params[col]
    # Explicitly preserve mapping column order and NO display-only columns.
    ordered_cols = [f["excel_column"] for f in chosen]
    return pd.DataFrame([{col: row.get(col, "") for col in ordered_cols}]), mapping


def dataframe_book_bytes(tables: Dict[str, pd.DataFrame]) -> bytes:
    """Read-only export; keep identifiers text-like to avoid Excel rounding."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, frame in tables.items():
            df = frame.copy()
            if df.empty and not len(df.columns):
                df = pd.DataFrame({"Info": ["Tidak ada data tersedia"]})
            df.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.book[name[:31]]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.fill = PatternFill("solid", fgColor="1D3557")
                cell.font = Font(name="Aptos", bold=True, color="FFFFFF")
                cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.row_dimensions[1].height = 30
            for j, column in enumerate(ws.columns, 1):
                col_name = str(ws.cell(1, j).value or "")
                width = min(45, max(16, min(38, len(col_name) + 3)))
                ws.column_dimensions[get_column_letter(j)].width = width
                if col_name.endswith("Id") or col_name in {"OrganizationId2"}:
                    for cell in list(column)[1:]:
                        if cell.value is not None:
                            cell.value = str(cell.value)
                            cell.number_format = "@"
    return output.getvalue()


def json_bytes(snapshots: Sequence[Dict[str, Any]]) -> bytes:
    return json.dumps({"inventoryOrganizations": list(snapshots)},
                      ensure_ascii=False, indent=2, default=str).encode("utf-8")
