from __future__ import annotations

import io
import json
import zipfile
from copy import copy
from typing import Any, Dict, List, Optional

import pandas as pd

from services.payload_builder import build_payload_from_row


def fields_to_template_dataframe(mapping: Dict[str, Any], sample_rows: int = 3, selected_columns: Optional[List[str]] = None) -> pd.DataFrame:
    data: Dict[str, List[Any]] = {}
    selected = set(selected_columns or [])
    for field in mapping.get("fields", []):
        col = field.get("excel_column")
        if not col:
            continue
        if selected_columns is not None and col not in selected:
            continue
        default = field.get("default", "")
        data[col] = [default if default is not None else "" for _ in range(sample_rows)]
    df = pd.DataFrame(data).astype(object)
    if sample_rows > 0 and not df.empty:
        for field in mapping.get("fields", []):
            col = field.get("excel_column")
            if col in df.columns and field.get("sample") is not None:
                df.loc[0, col] = field.get("sample")
    return df


def dictionary_dataframe(mapping: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for field in mapping.get("fields", []):
        rows.append({
            "section": field.get("section", "General"),
            "label": field.get("label", field.get("excel_column")),
            "excel_column": field.get("excel_column"),
            "payload_path": field.get("payload_path"),
            "type": field.get("type"),
            "required": field.get("required"),
            "send_to_payload": field.get("send_to_payload", True),
            "default": field.get("default"),
            "max_length": field.get("max_length"),
            "allowed_values": ", ".join(field.get("allowed_values") or []),
            "reference_hint": field.get("reference_hint", ""),
            "description": field.get("description", ""),
        })
    return pd.DataFrame(rows)


def _format_workbook(writer: pd.ExcelWriter) -> None:
    for sheet_name in writer.book.sheetnames:
        ws = writer.book[sheet_name]
        ws.freeze_panes = "A2"
        for cell in ws[1]:
            font = copy(cell.font)
            font.bold = True
            cell.font = font
        for column_cells in ws.columns:
            max_len = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
            ws.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 58)


def make_template_excel_bytes(mapping: Dict[str, Any], template_df: pd.DataFrame | None = None, sheet_name: str | None = None) -> bytes:
    if template_df is None:
        template_df = fields_to_template_dataframe(mapping, sample_rows=3)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        template_df.to_excel(writer, sheet_name=sheet_name or mapping.get("worksheet_name", "Upload_Template"), index=False)
        dictionary_dataframe(mapping).to_excel(writer, sheet_name="Field_Dictionary", index=False)
        pd.DataFrame([
            {"key": "api_key", "value": mapping.get("api_key")},
            {"key": "api_name", "value": mapping.get("api_name")},
            {"key": "method", "value": mapping.get("method")},
            {"key": "endpoint", "value": mapping.get("endpoint") or mapping.get("endpoint_template")},
            {"key": "note", "value": "Kosongkan field optional kalau tidak ingin dikirim ke payload."},
            {"key": "note", "value": "Kolom yang send_to_payload=False dipakai app sebagai route key/context, bukan dikirim ke Oracle."},
        ]).to_excel(writer, sheet_name="README", index=False)
        _format_workbook(writer)
    return output.getvalue()


def make_setup_workbook_bytes(io_mapping: Dict[str, Any], subinv_mapping: Dict[str, Any], params_mapping: Dict[str, Any]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        fields_to_template_dataframe(io_mapping, sample_rows=3).to_excel(writer, sheet_name=io_mapping["worksheet_name"], index=False)
        fields_to_template_dataframe(subinv_mapping, sample_rows=3).to_excel(writer, sheet_name=subinv_mapping["worksheet_name"], index=False)
        fields_to_template_dataframe(params_mapping, sample_rows=3).to_excel(writer, sheet_name=params_mapping["worksheet_name"], index=False)
        pd.concat([
            dictionary_dataframe(io_mapping).assign(template=io_mapping["worksheet_name"]),
            dictionary_dataframe(subinv_mapping).assign(template=subinv_mapping["worksheet_name"]),
            dictionary_dataframe(params_mapping).assign(template=params_mapping["worksheet_name"]),
        ], ignore_index=True).to_excel(writer, sheet_name="Field_Dictionary", index=False)
        pd.DataFrame([
            {"step": 1, "sheet": io_mapping["worksheet_name"], "action": "Create Minimal Inventory Organization"},
            {"step": 2, "sheet": subinv_mapping["worksheet_name"], "action": "Create Subinventories after OrganizationId exists"},
            {"step": 3, "sheet": params_mapping["worksheet_name"], "action": "Patch IO Parameters after subinventories exist"},
        ]).to_excel(writer, sheet_name="README", index=False)
        _format_workbook(writer)
    return output.getvalue()


def make_reference_excel_bytes(reference_results: Dict[str, pd.DataFrame]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, df in reference_results.items():
            safe = name[:31].replace("/", "-").replace("\\", "-")
            (df if not df.empty else pd.DataFrame([{"info": "No data"}])).to_excel(writer, sheet_name=safe, index=False)
        _format_workbook(writer)
    return output.getvalue()


def make_json_bytes(data: Dict[str, Any] | List[Any]) -> bytes:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")


def sample_payload(mapping: Dict[str, Any]) -> Dict[str, Any]:
    df = fields_to_template_dataframe(mapping, sample_rows=1)
    if df.empty:
        return {}
    return build_payload_from_row(df.iloc[0], mapping)


def make_bundle_zip_bytes(mapping: Dict[str, Any], template_df: pd.DataFrame | None = None, extra_files: Optional[Dict[str, bytes]] = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        name = mapping.get("api_key", "template")
        zf.writestr(f"{name}_template.xlsx", make_template_excel_bytes(mapping, template_df=template_df))
        zf.writestr(f"{name}_mapping.json", make_json_bytes(mapping))
        try:
            zf.writestr(f"{name}_sample_payload.json", make_json_bytes(sample_payload(mapping)))
        except Exception as exc:
            zf.writestr(f"{name}_sample_payload_error.txt", str(exc))
        zf.writestr("README.txt", "Upload template, mapping JSON, dan sample payload untuk Oracle Fusion IO Setup Builder.\n")
        for filename, content in (extra_files or {}).items():
            zf.writestr(filename, content)
    return output.getvalue()


def read_upload_dataframe(uploaded_file, sheet_name: str | None = None) -> pd.DataFrame:
    filename = uploaded_file.name.lower()
    if filename.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        try:
            df = pd.read_excel(uploaded_file, sheet_name=sheet_name or "Upload_Template")
        except ValueError:
            uploaded_file.seek(0)
            df = pd.read_excel(uploaded_file)
    df = df.dropna(how="all")
    if not df.empty:
        df = df.loc[~df.apply(lambda row: all(pd.isna(v) or str(v).strip() == "" for v in row), axis=1)]
    return df


def read_workbook_sheets(uploaded_file, sheet_names: List[str]) -> Dict[str, pd.DataFrame]:
    result = {}
    for sheet in sheet_names:
        try:
            uploaded_file.seek(0)
            result[sheet] = pd.read_excel(uploaded_file, sheet_name=sheet).dropna(how="all")
        except Exception:
            result[sheet] = pd.DataFrame()
    return result
