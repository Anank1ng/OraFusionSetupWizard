from __future__ import annotations

import io
import json
import zipfile
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from services.config_loader import fields_by_section, filter_mapping_by_columns
from services.excel_service import fields_to_template_dataframe, make_bundle_zip_bytes, make_json_bytes, make_template_excel_bytes, sample_payload


def render_connection_form(prefix: str = "oracle") -> tuple[str, str, str, int]:
    default_url = ""
    try:
        default_url = st.secrets.get("oracle", {}).get("base_url", "")
    except Exception:
        default_url = ""
    base_url = st.text_input("Oracle Fusion Base URL", value=default_url, placeholder="https://your-instance.fa.ocs.oraclecloud.com", key=f"{prefix}_base_url")
    c1, c2, c3 = st.columns([1, 1, 0.5])
    with c1:
        username = st.text_input("Username", key=f"{prefix}_username")
    with c2:
        password = st.text_input("Password", type="password", key=f"{prefix}_password")
    with c3:
        timeout = st.number_input("Timeout", min_value=10, max_value=300, value=60, key=f"{prefix}_timeout")
    return base_url, username, password, int(timeout)


def field_selector(mapping: Dict[str, Any], preset: str = "minimal", key_prefix: str = "selector") -> List[str]:
    all_fields = mapping.get("fields", [])
    if f"{key_prefix}_selected" not in st.session_state:
        if preset == "minimal":
            st.session_state[f"{key_prefix}_selected"] = [f["excel_column"] for f in all_fields if f.get("include_in_minimal")]
        elif preset == "standard":
            st.session_state[f"{key_prefix}_selected"] = [f["excel_column"] for f in all_fields if f.get("include_in_standard") or f.get("include_in_minimal")]
        else:
            st.session_state[f"{key_prefix}_selected"] = [f["excel_column"] for f in all_fields]

    selected = set(st.session_state[f"{key_prefix}_selected"])
    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        if st.button("Use Minimal", key=f"{key_prefix}_min"):
            selected = {f["excel_column"] for f in all_fields if f.get("include_in_minimal")}
            st.session_state[f"{key_prefix}_selected"] = list(selected)
    with c2:
        if st.button("Use Standard", key=f"{key_prefix}_std"):
            selected = {f["excel_column"] for f in all_fields if f.get("include_in_standard") or f.get("include_in_minimal")}
            st.session_state[f"{key_prefix}_selected"] = list(selected)
    with c3:
        if st.button("Use All", key=f"{key_prefix}_all"):
            selected = {f["excel_column"] for f in all_fields}
            st.session_state[f"{key_prefix}_selected"] = list(selected)

    sections = fields_by_section(mapping)
    for section, fields in sections.items():
        with st.expander(f"{section} ({len(fields)} fields)", expanded=section in {"Core Organization", "Route Key", "Subinventory"}):
            for field in fields:
                col = field["excel_column"]
                label = field.get("label", col)
                required = " ⭐" if field.get("required") else ""
                send = "" if field.get("send_to_payload", True) else " · route key"
                checked = st.checkbox(
                    f"{label}{required}{send}",
                    value=col in selected,
                    help=f"{col} → {field.get('payload_path')}\n{field.get('description','')}",
                    key=f"{key_prefix}_{col}",
                )
                if checked:
                    selected.add(col)
                else:
                    selected.discard(col)
    selected_list = [f["excel_column"] for f in all_fields if f["excel_column"] in selected]
    st.session_state[f"{key_prefix}_selected"] = selected_list
    return selected_list


def render_builder_downloads(mapping: Dict[str, Any], selected_columns: List[str], key_prefix: str) -> Dict[str, Any]:
    selected_mapping = filter_mapping_by_columns(mapping, selected_columns)
    template_df = fields_to_template_dataframe(selected_mapping, sample_rows=3)
    payload_preview = None
    payload_error = None
    try:
        payload_preview = sample_payload(selected_mapping)
    except Exception as exc:
        payload_error = str(exc)

    c1, c2 = st.columns([1, 1])
    with c1:
        st.metric("Selected fields", len(selected_columns))
        st.dataframe(template_df, use_container_width=True)
    with c2:
        st.caption("Sample payload preview")
        if payload_error:
            st.error(payload_error)
        else:
            st.json(payload_preview)

    d1, d2, d3, d4 = st.columns(4)
    with d1:
        st.download_button(
            "Download Template Excel",
            data=make_template_excel_bytes(selected_mapping, template_df=template_df),
            file_name=f"{mapping['api_key']}_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key_prefix}_tpl",
        )
    with d2:
        st.download_button(
            "Download Mapping JSON",
            data=make_json_bytes(selected_mapping),
            file_name=f"{mapping['api_key']}_mapping.json",
            mime="application/json",
            key=f"{key_prefix}_mapping",
        )
    with d3:
        st.download_button(
            "Download Sample Payload",
            data=make_json_bytes(payload_preview or {"error": payload_error}),
            file_name=f"{mapping['api_key']}_sample_payload.json",
            mime="application/json",
            key=f"{key_prefix}_payload",
        )
    with d4:
        st.download_button(
            "Download Bundle ZIP",
            data=make_bundle_zip_bytes(selected_mapping, template_df=template_df),
            file_name=f"{mapping['api_key']}_bundle.zip",
            mime="application/zip",
            key=f"{key_prefix}_bundle",
        )
    return selected_mapping
