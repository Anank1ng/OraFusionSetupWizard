from __future__ import annotations

import math
import numbers
import re
from typing import Any, Dict, List, Tuple

import pandas as pd

PATH_TOKEN_PATTERN = re.compile(r"(?P<name>[^.\[\]]+)(?:\[(?P<index>\d+)\])?")


class PayloadBuildError(ValueError):
    pass


READ_ONLY_POST_COLUMNS = {
    "ManagementBusinessUnitName": "ManagementBusinessUnitId",
    "LegalEntityName": "LegalEntityId",
    "ProfitCenterBusinessUnitName": "ProfitCenterBusinessUnitId",
    "ItemDefinitionOrganizationName": "ItemDefinitionOrganizationId atau ItemDefinitionOrganizationCode",
    "ScheduleName": "ScheduleId",
}


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    try:
        result = pd.isna(value)
        if isinstance(result, bool):
            return result
    except Exception:
        pass
    return False


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if type(value).__module__.startswith("numpy") and type(value).__name__.startswith("bool"):
        return bool(value)
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        if float(value) == 1:
            return True
        if float(value) == 0:
            return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "yes", "y", "1", "ya", "iya"}:
            return True
        if normalized in {"false", "f", "no", "n", "0", "tidak", "nggak", "ga", "gak"}:
            return False
    raise PayloadBuildError(f"Nilai boolean tidak valid: {value!r}")


def cast_value(value: Any, field_type: str) -> Any:
    if is_blank(value):
        return None
    if field_type == "string":
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()
    if field_type == "integer":
        try:
            return int(value)
        except Exception as exc:
            raise PayloadBuildError(f"Nilai integer tidak valid: {value!r}") from exc
    if field_type == "float":
        try:
            return float(value)
        except Exception as exc:
            raise PayloadBuildError(f"Nilai float tidak valid: {value!r}") from exc
    if field_type == "boolean":
        return parse_bool(value)
    return value


def split_payload_path(path: str) -> List[Tuple[str, int | None]]:
    tokens: List[Tuple[str, int | None]] = []
    for part in path.split("."):
        match = PATH_TOKEN_PATTERN.fullmatch(part)
        if not match:
            raise PayloadBuildError(f"Payload path tidak valid: {path}")
        index = match.group("index")
        tokens.append((match.group("name"), int(index) if index is not None else None))
    return tokens


def set_nested(payload: Dict[str, Any], payload_path: str, value: Any) -> None:
    tokens = split_payload_path(payload_path)
    current: Any = payload
    for i, (name, index) in enumerate(tokens):
        is_last = i == len(tokens) - 1
        if index is None:
            if is_last:
                current[name] = value
                return
            next_name, next_index = tokens[i + 1]
            if name not in current or current[name] is None:
                current[name] = [] if next_index is not None else {}
            current = current[name]
            continue
        if name not in current or current[name] is None:
            current[name] = []
        if not isinstance(current[name], list):
            raise PayloadBuildError(f"Path {name} seharusnya list/array.")
        while len(current[name]) <= index:
            current[name].append({})
        if is_last:
            current[name][index] = value
            return
        current = current[name][index]


def remove_empty_containers(obj: Any) -> Any:
    if isinstance(obj, dict):
        cleaned = {k: remove_empty_containers(v) for k, v in obj.items()}
        return {k: v for k, v in cleaned.items() if v not in ({}, [])}
    if isinstance(obj, list):
        cleaned_list = [remove_empty_containers(v) for v in obj]
        return [v for v in cleaned_list if v not in ({}, [])]
    return obj


def get_row_value(row: pd.Series, column: str, default: Any = None) -> Any:
    if column in row and not is_blank(row[column]):
        return row[column]
    return default


def get_field_default(mapping: Dict[str, Any], excel_column: str, fallback: Any = None) -> Any:
    for field in mapping.get("fields", []):
        if field.get("excel_column") == excel_column:
            return field.get("default", fallback)
    return fallback


def resolve_dynamic_row_value(row: pd.Series, mapping: Dict[str, Any], excel_column: str, raw_value: Any) -> Any:
    if excel_column != "ItemDefinitionOrganizationCode" or not is_blank(raw_value):
        return raw_value
    item_grouping = get_row_value(row, "ItemGroupingCode", get_field_default(mapping, "ItemGroupingCode", "ORA_RCS_IGB_DFTN"))
    org_code = get_row_value(row, "OrganizationCode", get_field_default(mapping, "OrganizationCode"))
    if item_grouping == "ORA_RCS_IGB_DFTN" and not is_blank(org_code):
        return org_code
    return raw_value


def apply_mapping_rules(payload: Dict[str, Any], mapping: Dict[str, Any]) -> Dict[str, Any]:
    api_key = mapping.get("api_key")
    if api_key == "minimal_inventory_organizations":
        item_grouping = payload.get("ItemGroupingCode")
        if item_grouping == "ORA_RCS_IGB_DFTN":
            payload.pop("ItemDefinitionOrganizationId", None)
            if is_blank(payload.get("ItemDefinitionOrganizationCode")) and not is_blank(payload.get("OrganizationCode")):
                payload["ItemDefinitionOrganizationCode"] = payload["OrganizationCode"]
        if item_grouping == "ORA_RCS_IGB_RFRC":
            if is_blank(payload.get("ItemDefinitionOrganizationId")) and is_blank(payload.get("ItemDefinitionOrganizationCode")):
                raise PayloadBuildError("Reference Organization wajib isi ItemDefinitionOrganizationId atau ItemDefinitionOrganizationCode.")
    return payload


def build_payload_from_row(row: pd.Series, mapping: Dict[str, Any], include_nulls: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    errors: List[str] = []

    for field in mapping.get("fields", []):
        excel_column = field.get("excel_column")
        payload_path = field.get("payload_path")
        if not excel_column or not payload_path or field.get("send_to_payload") is False:
            continue
        raw_value = get_row_value(row, excel_column, field.get("default"))
        raw_value = resolve_dynamic_row_value(row, mapping, excel_column, raw_value)

        read_only = bool(field.get("read_only_for_post")) or excel_column in READ_ONLY_POST_COLUMNS
        if read_only and not is_blank(raw_value):
            use_instead = field.get("use_instead") or READ_ONLY_POST_COLUMNS.get(excel_column, "field ID yang sesuai")
            errors.append(f"{excel_column} tidak boleh dikirim. Gunakan {use_instead}.")
            continue

        if is_blank(raw_value):
            if field.get("required") and field.get("required_for_payload", True):
                errors.append(f"{excel_column} wajib diisi")
            if include_nulls:
                set_nested(payload, payload_path, None)
            continue

        try:
            value = cast_value(raw_value, field.get("type", "string"))
        except PayloadBuildError as exc:
            errors.append(f"{excel_column}: {exc}")
            continue

        max_length = field.get("max_length")
        if max_length and isinstance(value, str) and len(value) > int(max_length):
            errors.append(f"{excel_column}: panjang {len(value)} melebihi batas {max_length}")
            continue
        allowed_values = field.get("allowed_values")
        if allowed_values and value not in allowed_values:
            errors.append(f"{excel_column}: nilai {value!r} tidak ada di allowed values {allowed_values}")
            continue
        set_nested(payload, payload_path, value)

    if errors:
        raise PayloadBuildError("; ".join(errors))
    payload = remove_empty_containers(payload)
    payload = apply_mapping_rules(payload, mapping)
    return remove_empty_containers(payload)


def build_payloads_from_dataframe(df: pd.DataFrame, mapping: Dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payloads: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, row in df.iterrows():
        try:
            payloads.append(build_payload_from_row(row, mapping))
        except PayloadBuildError as exc:
            errors.append({"row_number": int(index) + 2, "message": str(exc)})
    return payloads, errors
