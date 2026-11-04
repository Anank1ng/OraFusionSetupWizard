"""Safeguards for Oracle-backed IO Parameter Update Builder templates.

Pure helpers; no Streamlit state and no Oracle mutations.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Sequence

import pandas as pd

from services.payload_builder import parse_bool


ROUTE_COLUMNS = ("OrganizationCode", "OrganizationId", "OrganizationId2")
CHILD_KEY_COLUMNS = ("OrganizationId2", "OrgParameterId", "InvOrgParameterId", "ParameterId")


def is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def child_parameter_key(parameters: Dict[str, Any]) -> Any:
    """Use only keys returned by GET; never guess from the parent IO ID."""
    for key in CHILD_KEY_COLUMNS:
        value = parameters.get(key)
        if not is_missing(value):
            return value
    return ""


def selection_signature(
    base_url: str, username: str, selected_orgs: Sequence[Dict[str, Any]], list_revision: int
) -> str:
    """Bind GET results to a specific instance, user, IO selection and list fetch."""
    fingerprint = {
        "instance": base_url.strip().rstrip("/").lower(),
        "username": username.strip(),
        "list_revision": int(list_revision),
        "organizations": [
            [str(org.get("OrganizationId", "")), str(org.get("OrganizationCode", ""))]
            for org in selected_orgs
        ],
    }
    data = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]


def validate_reference_rows(rows: Sequence[Dict[str, Any]]) -> List[str]:
    """Hard blockers: failed GET, missing parent route keys, or missing child key."""
    errors = []
    for row in rows:
        org = row.get("org") or {}
        params = row.get("params") or {}
        code = org.get("OrganizationCode") or str(org.get("OrganizationId") or "(unknown IO)")
        err = row.get("error")
        if err:
            errors.append(f"{code}: {err}")
        elif is_missing(org.get("OrganizationCode")) or is_missing(org.get("OrganizationId")):
            errors.append(f"{code}: OrganizationCode / OrganizationId tidak ditemukan dalam GET.")
        elif not params:
            errors.append(f"{code}: GET invOrgParameters tidak menghasilkan data.")
        elif is_missing(child_parameter_key(params)):
            errors.append(f"{code}: child key OrganizationId2 tidak ditemukan dalam GET.")
    return errors


def build_reference_template(
    rows: Sequence[Dict[str, Any]], mapping: Dict[str, Any]
) -> pd.DataFrame:
    """Build rows using *only* GET data; blank optional parameters stay blank."""
    if not rows:
        raise ValueError("Belum ada IO yang berhasil di-GET.")
    errors = validate_reference_rows(rows)
    if errors:
        raise ValueError("Template hasil GET diblokir: " + " | ".join(errors[:5]))

    records: List[Dict[str, Any]] = []
    for entry in rows:
        org = entry["org"]
        params = entry["params"]
        record: Dict[str, Any] = {}
        for field in mapping.get("fields") or []:
            col = field.get("excel_column")
            if not col:
                continue
            if col == "OrganizationCode":
                value = org.get(col)
            elif col == "OrganizationName":
                value = org.get(col)
            elif col == "OrganizationId":
                value = org.get(col)
            elif col == "OrganizationId2":
                value = child_parameter_key(params)
            else:
                value = params.get(col)
                if is_missing(value):
                    payload_path = field.get("payload_path")
                    if payload_path and payload_path != col:
                        value = params.get(payload_path)
            record[col] = "" if is_missing(value) else value
        records.append(record)
    return pd.DataFrame(records).astype(object)


def reference_status_table(
    rows: Sequence[Dict[str, Any]], mapping: Dict[str, Any]
) -> pd.DataFrame:
    """Explicitly report GET health and how many selected fields are unavailable."""
    payload_fields = [
        field["excel_column"]
        for field in mapping.get("fields") or []
        if field.get("excel_column") and field.get("send_to_payload", True)
    ]
    result = []
    for entry in rows:
        org = entry.get("org") or {}
        params = entry.get("params") or {}
        entry_errors = validate_reference_rows([entry])
        missing = [col for col in payload_fields if is_missing(params.get(col))]
        result.append({
            "OrganizationCode": org.get("OrganizationCode") or "",
            "OrganizationName": org.get("OrganizationName") or "",
            "OrganizationId": org.get("OrganizationId") or "",
            "OrganizationId2": child_parameter_key(params),
            "GET Status": "ERROR" if entry_errors else "OK",
            "Field berisi nilai": len(payload_fields) - len(missing),
            "Field kosong/tidak tersedia": len(missing),
            "Detail": entry_errors[0] if entry_errors else (
                ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else "")
                if missing else "Semua field terpilih punya nilai"
            ),
        })
    return pd.DataFrame(result)


def nullable_checkbox_value(value: Any) -> bool | None:
    """Preserve a missing Oracle value as NULL, never synthesize False."""
    if is_missing(value):
        return None
    return parse_bool(value)


def clean_editor_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Use Python None for nullable Excel exports and PATCH payload validation."""
    return df.astype(object).where(pd.notna(df), None)


def preserve_untouched_blank_booleans(
    original: pd.DataFrame,
    edited: pd.DataFrame,
    mapping: Dict[str, Any],
    editor_state: Dict[str, Any] | None,
) -> pd.DataFrame:
    """Protect absent Oracle booleans even if a frontend renders null as unchecked.

    A user can still explicitly set False: the change must appear in the
    data_editor edited_rows delta.
    """
    result = edited.copy().astype(object)
    delta = (editor_state or {}).get("edited_rows") or {}
    boolean_cols = [
        f.get("excel_column") for f in mapping.get("fields") or []
        if f.get("type") == "boolean" and f.get("excel_column") in original.columns
    ]
    for position, index in enumerate(original.index):
        changes = delta.get(position, delta.get(str(position), {}))
        for col in boolean_cols:
            if is_missing(original.at[index, col]) and col not in changes:
                result.at[index, col] = None
    return clean_editor_dataframe(result)
