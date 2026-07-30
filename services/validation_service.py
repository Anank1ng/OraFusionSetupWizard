from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from services.payload_builder import PayloadBuildError, build_payload_from_row, is_blank, resolve_dynamic_row_value


def expected_columns(mapping: Dict[str, Any]) -> List[str]:
    return [field["excel_column"] for field in mapping.get("fields", []) if field.get("excel_column")]


def validate_columns(df: pd.DataFrame, mapping: Dict[str, Any]) -> Dict[str, Any]:
    expected = expected_columns(mapping)
    actual = [str(col).strip() for col in df.columns.tolist()]
    missing = [col for col in expected if col not in actual]
    extra = [col for col in actual if col not in expected]
    return {
        "expected_columns": expected,
        "actual_columns": actual,
        "missing_columns": missing,
        "extra_columns": extra,
        "order_is_same": actual == expected,
        "exact_match": not missing and not extra and actual == expected,
    }


def validate_required_values(df: pd.DataFrame, mapping: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for index, row in df.iterrows():
        for field in mapping.get("fields", []):
            if not field.get("required"):
                continue
            col = field.get("excel_column")
            raw_value = row.get(col) if col in df.columns else None
            if is_blank(raw_value):
                raw_value = field.get("default")
            raw_value = resolve_dynamic_row_value(row, mapping, col, raw_value)
            if col not in df.columns or is_blank(raw_value):
                rows.append({"excel_row": int(index) + 2, "column": col, "error": "Wajib diisi"})
    return pd.DataFrame(rows)


def validate_payload_build(df: pd.DataFrame, mapping: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for index, row in df.iterrows():
        try:
            build_payload_from_row(row, mapping)
        except PayloadBuildError as exc:
            rows.append({"excel_row": int(index) + 2, "error": str(exc)})
    return pd.DataFrame(rows)


def validation_summary(df: pd.DataFrame, mapping: Dict[str, Any], strict_template: bool = False) -> Dict[str, Any]:
    column_result = validate_columns(df, mapping)
    can_validate_rows = not column_result["missing_columns"]
    required_errors = validate_required_values(df, mapping) if can_validate_rows else pd.DataFrame()
    payload_errors = validate_payload_build(df, mapping) if can_validate_rows else pd.DataFrame()
    column_valid = column_result["exact_match"] if strict_template else not column_result["missing_columns"]
    return {
        "total_rows": len(df),
        "strict_template": strict_template,
        **column_result,
        "required_error_count": len(required_errors),
        "payload_error_count": len(payload_errors),
        "required_errors": required_errors,
        "payload_errors": payload_errors,
        "is_valid": column_valid and required_errors.empty and payload_errors.empty,
    }
