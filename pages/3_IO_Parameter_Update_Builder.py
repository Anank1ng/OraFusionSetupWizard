import json
from copy import deepcopy
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from services.config_loader import filter_mapping_by_columns, load_schema, schema_title
from services.excel_service import make_bundle_zip_bytes, make_json_bytes, make_template_excel_bytes
from services.oracle_client import OracleFusionClient
from services.payload_builder import build_payload_from_row, is_blank
from services.reference_service import fetch_inventory_orgs, fetch_inv_org_parameters
from services.ui_helpers import render_connection_form

st.set_page_config(page_title="IO Parameter Update Builder", page_icon="🛠️", layout="wide")
st.title("🛠️ IO Parameter Update Builder")
st.caption("Patch v2.2 — fetch existing IO, pilih sampai 50 IO, lalu generate template PATCH dari current value Oracle.")

BASE_MAPPING = load_schema("io_parameters_update")
st.info(schema_title(BASE_MAPPING))
st.warning(
    "Untuk PATCH, template butuh OrganizationId dan OrganizationId2. Page ini bisa mengambil keduanya otomatis dari Oracle, "
    "lalu membuat template update berbasis current value."
)

ROUTE_COLUMNS = ["OrganizationCode", "OrganizationId", "OrganizationId2"]
DISPLAY_COLUMNS = ["OrganizationName"]
MAX_SELECTED_IO = 50


def _make_client(base_url: str, username: str, password: str, timeout: int) -> OracleFusionClient | None:
    if not base_url or not username or not password:
        st.error("Isi Oracle Base URL, username, dan password dulu.")
        return None
    return OracleFusionClient(base_url, username, password, timeout=timeout)


def _safe_int(value: Any) -> int | None:
    if is_blank(value):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _first_value(row: pd.Series | Dict[str, Any], candidates: List[str], default: Any = "") -> Any:
    for key in candidates:
        if isinstance(row, pd.Series):
            if key in row and not is_blank(row.get(key)):
                return row.get(key)
        elif key in row and not is_blank(row.get(key)):
            return row.get(key)
    return default


def _get_org_id2(param_row: pd.Series | Dict[str, Any], organization_id: Any) -> Any:
    value = _first_value(param_row, ["OrganizationId2", "OrgParameterId", "InvOrgParameterId", "ParameterId"], "")
    if not is_blank(value):
        return value
    # Banyak instance memakai OrganizationId yang sama sebagai key child parameter.
    return organization_id


def _label_for_org(row: pd.Series) -> str:
    code = str(_first_value(row, ["OrganizationCode"], "")).strip()
    name = str(_first_value(row, ["OrganizationName"], "")).strip()
    org_id = str(_first_value(row, ["OrganizationId"], "")).strip()
    label = " | ".join([x for x in [code, name, org_id] if x])
    return label or f"row-{int(row.name) + 1}"


def _filter_org_df(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    if df.empty or not keyword.strip():
        return df
    keyword = keyword.strip().lower()
    cols = [c for c in ["OrganizationCode", "OrganizationName", "Status", "OrganizationId"] if c in df.columns]
    if not cols:
        return df
    mask = pd.Series(False, index=df.index)
    for col in cols:
        mask = mask | df[col].astype(str).str.lower().str.contains(keyword, na=False)
    return df[mask]


def _add_display_field_to_mapping(mapping: Dict[str, Any]) -> Dict[str, Any]:
    mapping = deepcopy(mapping)
    existing = {f.get("excel_column") for f in mapping.get("fields", [])}
    if "OrganizationName" not in existing:
        fields = mapping.get("fields", [])
        insert_at = 1 if fields else 0
        fields.insert(
            insert_at,
            {
                "section": "Route Key",
                "label": "Organization Name",
                "excel_column": "OrganizationName",
                "payload_path": "OrganizationName",
                "type": "string",
                "required": False,
                "sample": "",
                "send_to_payload": False,
                "description": "Display/reference only. Tidak dikirim ke PATCH payload.",
                "reference_hint": "",
                "include_in_minimal": True,
                "include_in_standard": True,
            },
        )
    return mapping


def _field_lookup(mapping: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {f.get("excel_column"): f for f in mapping.get("fields", []) if f.get("excel_column")}


def _field_default_from_reference(field: Dict[str, Any], org_row: Dict[str, Any], param_row: Dict[str, Any]) -> Any:
    col = field.get("excel_column")
    payload_path = field.get("payload_path")
    candidates = [col, payload_path]
    # Jadikan beberapa nama variasi agar hasil GET yang berbeda tetap bisa masuk.
    if col == "ScheduleId":
        candidates += ["ScheduleId"]
    if col == "OrganizationCode":
        return _first_value(org_row, ["OrganizationCode"], "")
    if col == "OrganizationName":
        return _first_value(org_row, ["OrganizationName"], "")
    if col == "OrganizationId":
        return _first_value(org_row, ["OrganizationId"], "")
    if col == "OrganizationId2":
        return _get_org_id2(param_row, _first_value(org_row, ["OrganizationId"], ""))

    for candidate in candidates:
        if candidate and candidate in param_row and not is_blank(param_row.get(candidate)):
            return param_row.get(candidate)
    for candidate in candidates:
        if candidate and candidate in org_row and not is_blank(org_row.get(candidate)):
            return org_row.get(candidate)
    return ""


def _make_template_from_reference(rows: List[Dict[str, Any]], mapping: Dict[str, Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=[f.get("excel_column") for f in mapping.get("fields", []) if f.get("excel_column")])
    records: List[Dict[str, Any]] = []
    for row in rows:
        org_row = row.get("org", {}) or {}
        param_row = row.get("params", {}) or {}
        record: Dict[str, Any] = {}
        for field in mapping.get("fields", []):
            col = field.get("excel_column")
            if not col:
                continue
            record[col] = _field_default_from_reference(field, org_row, param_row)
        records.append(record)
    return pd.DataFrame(records).astype(object)


def _make_mapping_for_selected(selected_columns: List[str]) -> Dict[str, Any]:
    mapping_with_display = _add_display_field_to_mapping(BASE_MAPPING)
    ordered_columns: List[str] = []
    for col in ["OrganizationCode", "OrganizationName", "OrganizationId", "OrganizationId2"]:
        if col not in ordered_columns:
            ordered_columns.append(col)
    for col in selected_columns:
        if col not in ordered_columns:
            ordered_columns.append(col)
    return filter_mapping_by_columns(mapping_with_display, ordered_columns)


def _selected_columns_from_preset(mapping: Dict[str, Any], preset: str) -> List[str]:
    fields = mapping.get("fields", [])
    if preset == "minimal":
        return [f["excel_column"] for f in fields if f.get("include_in_minimal")]
    if preset == "standard":
        return [f["excel_column"] for f in fields if f.get("include_in_minimal") or f.get("include_in_standard")]
    if preset == "all":
        return [f["excel_column"] for f in fields]
    return [f["excel_column"] for f in fields if f.get("include_in_minimal")]


def _render_parameter_selector(mapping: Dict[str, Any]) -> List[str]:
    payload_fields = [f for f in mapping.get("fields", []) if f.get("send_to_payload", True)]
    sections = sorted({f.get("section", "General") for f in payload_fields})

    if "param_builder_selected_payload" not in st.session_state:
        st.session_state["param_builder_selected_payload"] = _selected_columns_from_preset(mapping, "standard")

    st.write("**Pilih field parameter yang ingin disertakan di template PATCH**")
    p1, p2, p3, p4 = st.columns(4)
    if p1.button("Minimal", use_container_width=True):
        st.session_state["param_builder_selected_payload"] = _selected_columns_from_preset(mapping, "minimal")
        st.rerun()
    if p2.button("Standard", use_container_width=True):
        st.session_state["param_builder_selected_payload"] = _selected_columns_from_preset(mapping, "standard")
        st.rerun()
    if p3.button("All fields", use_container_width=True):
        st.session_state["param_builder_selected_payload"] = _selected_columns_from_preset(mapping, "all")
        st.rerun()
    if p4.button("Clear optional", use_container_width=True):
        st.session_state["param_builder_selected_payload"] = [c for c in ROUTE_COLUMNS]
        st.rerun()

    selected = set(st.session_state.get("param_builder_selected_payload", []))
    selected.update(ROUTE_COLUMNS)

    visible_sections = st.multiselect(
        "Section yang ditampilkan",
        options=sections,
        default=[s for s in ["Inventory Settings", "Subinventory Defaults", "Movement Request"] if s in sections],
        help="Filter tampilan field supaya tidak terlalu panjang. Field yang sudah terpilih tidak hilang, hanya tidak ditampilkan.",
    )
    for section in visible_sections:
        fields = [f for f in payload_fields if f.get("section", "General") == section]
        selected_count = sum(1 for f in fields if f.get("excel_column") in selected)
        with st.expander(f"{section} ({selected_count}/{len(fields)} selected)", expanded=section in {"Inventory Settings", "Subinventory Defaults"}):
            csec1, csec2 = st.columns(2)
            if csec1.button(f"Pilih semua {section}", key=f"param_pick_{section}", use_container_width=True):
                selected.update(f.get("excel_column") for f in fields if f.get("excel_column"))
                st.session_state["param_builder_selected_payload"] = list(selected)
                st.rerun()
            if csec2.button(f"Kosongkan {section}", key=f"param_clear_{section}", use_container_width=True):
                selected.difference_update(f.get("excel_column") for f in fields if f.get("excel_column"))
                selected.update(ROUTE_COLUMNS)
                st.session_state["param_builder_selected_payload"] = list(selected)
                st.rerun()
            for field in fields:
                col = field.get("excel_column")
                label = field.get("label", col)
                checked = st.checkbox(
                    f"{label} · `{col}`",
                    value=col in selected,
                    key=f"param_field_{col}",
                    help=field.get("description", ""),
                )
                if checked:
                    selected.add(col)
                else:
                    selected.discard(col)

    selected.update(ROUTE_COLUMNS)
    ordered = [f["excel_column"] for f in mapping.get("fields", []) if f.get("excel_column") in selected]
    st.session_state["param_builder_selected_payload"] = ordered
    return ordered


def _store_selected_reference_rows(org_df: pd.DataFrame, selected_labels: List[str], label_map: Dict[str, int]) -> None:
    rows = []
    for label in selected_labels:
        idx = label_map.get(label)
        if idx is None or idx not in org_df.index:
            continue
        rows.append(org_df.loc[idx].to_dict())
    st.session_state["param_builder_selected_org_rows"] = rows


# 1. Connection and fetch IO list
st.subheader("1. Fetch Inventory Organizations dari Oracle")
base_url, username, password, timeout = render_connection_form("param_fetch")
fc1, fc2, fc3 = st.columns([0.6, 0.6, 1.3])
with fc1:
    limit = st.number_input("Fetch limit", min_value=1, max_value=500, value=60, step=1, help="Boleh fetch lebih dari 50, tapi selection untuk template dibatasi maksimal 50 IO.")
with fc2:
    offset = st.number_input("Offset", min_value=0, value=0, step=1)
with fc3:
    q_filter = st.text_input("Optional q filter", placeholder="Contoh: OrganizationCode LIKE 'WH%'")

if st.button("🔎 Fetch IO List", type="primary", use_container_width=True):
    client = _make_client(base_url, username, password, int(timeout))
    if client:
        try:
            # fetch_inventory_orgs belum punya offset; pakai q filter/limit dulu agar sederhana.
            org_df, org_resp = fetch_inventory_orgs(client, limit=int(limit), q_filter=q_filter, expand_children=False)
            if int(offset) > 0 and not org_df.empty:
                org_df = org_df.iloc[int(offset):].copy()
            st.session_state["param_builder_org_df"] = org_df
            st.session_state["param_builder_org_raw"] = {"ok": org_resp.ok, "status_code": org_resp.status_code, "url": org_resp.url, "body": org_resp.body}
            st.session_state.pop("param_builder_reference_rows", None)
            st.success(f"Fetched {len(org_df)} Inventory Organization row(s).")
        except Exception as exc:
            st.error(f"Gagal fetch Inventory Organizations: {exc}")

org_df = st.session_state.get("param_builder_org_df")
if isinstance(org_df, pd.DataFrame) and not org_df.empty:
    st.subheader("2. Pilih IO yang mau dibuatkan template update")
    keyword = st.text_input("Search/filter hasil fetch", placeholder="Cari OrganizationCode atau OrganizationName")
    filtered_org_df = _filter_org_df(org_df, keyword)
    st.caption(f"Menampilkan {len(filtered_org_df)} dari {len(org_df)} IO. Pilih maksimal {MAX_SELECTED_IO} IO.")

    with st.expander("Preview IO hasil fetch", expanded=False):
        preferred_cols = [c for c in ["OrganizationCode", "OrganizationName", "OrganizationId", "Status", "LocationId"] if c in filtered_org_df.columns]
        st.dataframe(filtered_org_df[preferred_cols] if preferred_cols else filtered_org_df, use_container_width=True)

    labels = [_label_for_org(row) for _, row in filtered_org_df.iterrows()]
    label_map = {label: idx for label, idx in zip(labels, filtered_org_df.index)}
    selected_labels = st.multiselect("Select IO", labels, max_selections=MAX_SELECTED_IO)
    _store_selected_reference_rows(filtered_org_df, selected_labels, label_map)

    if selected_labels:
        st.success(f"{len(selected_labels)} IO dipilih.")
    else:
        st.info("Pilih minimal 1 IO untuk fetch child invOrgParameters.")

    if st.button("📥 Fetch selected IO parameters", disabled=not selected_labels, use_container_width=True):
        client = _make_client(base_url, username, password, int(timeout))
        if client:
            selected_rows = st.session_state.get("param_builder_selected_org_rows", [])
            reference_rows: List[Dict[str, Any]] = []
            raw: Dict[str, Any] = {}
            progress = st.progress(0)
            for pos, org in enumerate(selected_rows):
                org_id = _safe_int(org.get("OrganizationId"))
                org_code = org.get("OrganizationCode", "")
                if org_id is None:
                    reference_rows.append({"org": org, "params": {}, "error": "OrganizationId kosong/tidak valid"})
                    raw[str(org_code or pos)] = {"error": "OrganizationId kosong/tidak valid"}
                    continue
                try:
                    params_df, params_resp = fetch_inv_org_parameters(client, org_id)
                    param_dict = params_df.iloc[0].to_dict() if not params_df.empty else {}
                    reference_rows.append({"org": org, "params": param_dict, "error": "" if not params_df.empty else "invOrgParameters kosong"})
                    raw[str(org_code or org_id)] = {"ok": params_resp.ok, "status_code": params_resp.status_code, "url": params_resp.url, "body": params_resp.body}
                except Exception as exc:
                    reference_rows.append({"org": org, "params": {}, "error": str(exc)})
                    raw[str(org_code or org_id)] = {"error": str(exc)}
                progress.progress((pos + 1) / max(len(selected_rows), 1))
            st.session_state["param_builder_reference_rows"] = reference_rows
            st.session_state["param_builder_raw_params"] = raw
            errors = [r for r in reference_rows if r.get("error")]
            if errors:
                st.warning(f"Fetched {len(reference_rows)} IO. Ada {len(errors)} IO dengan warning/error, cek preview.")
            else:
                st.success(f"Fetched invOrgParameters untuk {len(reference_rows)} IO.")

reference_rows = st.session_state.get("param_builder_reference_rows")
if reference_rows:
    st.divider()
    st.subheader("3. Pilih field tambahan untuk template PATCH")
    selected_columns = _render_parameter_selector(BASE_MAPPING)
    selected_mapping = _make_mapping_for_selected(selected_columns)
    template_df = _make_template_from_reference(reference_rows, selected_mapping)

    st.subheader("4. Preview template dari hasil fetch")
    m1, m2, m3, m4 = st.columns(4)
    payload_fields_count = sum(1 for f in selected_mapping.get("fields", []) if f.get("send_to_payload", True))
    m1.metric("Selected IO", len(template_df))
    m2.metric("Excel columns", len(template_df.columns))
    m3.metric("Payload fields", payload_fields_count)
    m4.metric("Route/display fields", len(template_df.columns) - payload_fields_count)

    with st.expander("Preview data template", expanded=True):
        st.dataframe(template_df, use_container_width=True)

    sample_patch_payload = {}
    sample_payload_error = None
    if not template_df.empty:
        try:
            sample_patch_payload = build_payload_from_row(template_df.iloc[0], selected_mapping)
        except Exception as exc:
            sample_payload_error = str(exc)
    with st.expander("Preview sample PATCH payload dari row pertama", expanded=True):
        if sample_payload_error:
            st.error(sample_payload_error)
        else:
            st.json(sample_patch_payload)

    raw_get = {
        "inventory_organizations": st.session_state.get("param_builder_org_raw", {}),
        "inv_org_parameters": st.session_state.get("param_builder_raw_params", {}),
    }
    extra_files = {
        "raw_get_io_parameters.json": make_json_bytes(raw_get),
        "sample_patch_payload_from_reference.json": make_json_bytes(sample_patch_payload if not sample_payload_error else {"error": sample_payload_error}),
    }

    d1, d2, d3, d4 = st.columns(4)
    with d1:
        st.download_button(
            "Download Template Excel",
            data=make_template_excel_bytes(selected_mapping, template_df=template_df, sheet_name=selected_mapping.get("worksheet_name", "IO_Parameters_Update")),
            file_name="io_parameters_update_from_oracle_reference.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Download Mapping JSON",
            data=make_json_bytes(selected_mapping),
            file_name="io_parameters_update_mapping.json",
            mime="application/json",
            use_container_width=True,
        )
    with d3:
        st.download_button(
            "Download Sample PATCH JSON",
            data=make_json_bytes(sample_patch_payload if not sample_payload_error else {"error": sample_payload_error}),
            file_name="io_parameters_update_sample_payload.json",
            mime="application/json",
            use_container_width=True,
        )
    with d4:
        st.download_button(
            "Download Bundle ZIP",
            data=make_bundle_zip_bytes(selected_mapping, template_df=template_df, extra_files=extra_files),
            file_name="io_parameters_update_reference_bundle.zip",
            mime="application/zip",
            use_container_width=True,
        )

    st.download_button(
        "Download Raw GET JSON",
        data=make_json_bytes(raw_get),
        file_name="io_parameters_update_raw_get.json",
        mime="application/json",
        use_container_width=True,
    )
else:
    st.divider()
    st.subheader("Fallback: buat template kosong")
    st.caption("Pakai ini kalau belum mau fetch Oracle. Untuk template berbasis current value, fetch IO dan child params dulu.")
    fallback_columns = _render_parameter_selector(BASE_MAPPING)
    fallback_mapping = _make_mapping_for_selected(fallback_columns)
    # Untuk fallback kosong, tetap pakai sample standar dari schema.
    from services.ui_helpers import render_builder_downloads

    render_builder_downloads(fallback_mapping, [f.get("excel_column") for f in fallback_mapping.get("fields", [])], key_prefix="params_fallback")
