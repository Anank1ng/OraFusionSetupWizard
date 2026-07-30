import json
from copy import deepcopy
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from services.config_loader import filter_mapping_by_columns, load_schema, schema_title
from services.excel_service import make_bundle_zip_bytes, make_json_bytes, make_template_excel_bytes
from services.oracle_client import OracleFusionClient
from services.payload_builder import build_payload_from_row, is_blank
from services.reference_service import fetch_inventory_orgs, fetch_inv_org_parameters
from services.ui_helpers import render_connection_form

st.set_page_config(page_title="IO Update Builder", page_icon="🛠️", layout="wide")
st.title("🛠️ IO Update Builder")
st.caption("Patch v2.4 — satu fetch IO, lalu generate template untuk IO Parameters atau Organization Usage.")

PARAM_MAPPING_BASE = load_schema("io_parameters_update")
USAGE_MAPPING_BASE = load_schema("organization_usage_update")

st.info(
    "Gunakan page ini untuk fetch existing IO dari Oracle, pilih sampai 50 IO, lalu generate template update berbasis current value. "
    "Pilih **IO Parameters** untuk child invOrgParameters, atau **Organization Usage** untuk parent inventoryOrganizations."
)

PARAM_ROUTE_COLUMNS = ["OrganizationCode", "OrganizationId", "OrganizationId2"]
USAGE_ROUTE_COLUMNS = ["OrganizationCode", "OrganizationId"]
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


def _to_editor_bool(value: Any) -> bool:
    if is_blank(value):
        return False
    if isinstance(value, bool):
        return value
    if type(value).__module__.startswith("numpy") and type(value).__name__.startswith("bool"):
        return bool(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "t", "yes", "y", "1", "ya", "iya"}:
            return True
        if normalized in {"false", "f", "no", "n", "0", "tidak", "nggak", "ga", "gak"}:
            return False
    return bool(value)


def _prepare_editor_dataframe(df: pd.DataFrame, mapping: Dict[str, Any]) -> pd.DataFrame:
    edited_df = df.copy().astype(object)
    lookup = _field_lookup(mapping)
    for col in edited_df.columns:
        field = lookup.get(col, {})
        if field.get("type") == "boolean":
            edited_df[col] = edited_df[col].map(_to_editor_bool).astype(bool)
    return edited_df


def _editor_column_config(mapping: Dict[str, Any]) -> Dict[str, Any]:
    lookup = _field_lookup(mapping)
    config: Dict[str, Any] = {}
    for col, field in lookup.items():
        label = field.get("label") or col
        help_text = field.get("description") or field.get("reference_hint") or None
        if field.get("type") == "boolean":
            config[col] = st.column_config.CheckboxColumn(label=label, help=help_text)
        elif field.get("type") == "integer":
            config[col] = st.column_config.NumberColumn(label=label, help=help_text, step=1, format="%d")
        else:
            config[col] = st.column_config.TextColumn(label=label, help=help_text)
    return config


def _field_default_from_reference(field: Dict[str, Any], org_row: Dict[str, Any], param_row: Dict[str, Any]) -> Any:
    col = field.get("excel_column")
    payload_path = field.get("payload_path")
    candidates = [col, payload_path]

    if col == "OrganizationCode":
        return _first_value(org_row, ["OrganizationCode"], "")
    if col == "OrganizationName":
        return _first_value(org_row, ["OrganizationName"], "")
    if col == "OrganizationId":
        return _first_value(org_row, ["OrganizationId"], "")
    if col == "OrganizationId2":
        return _get_org_id2(param_row, _first_value(org_row, ["OrganizationId"], ""))

    # Prefer child parameter values for IO Parameters, then parent org values.
    for candidate in candidates:
        if candidate and candidate in param_row and not is_blank(param_row.get(candidate)):
            return param_row.get(candidate)
    for candidate in candidates:
        if candidate and candidate in org_row and not is_blank(org_row.get(candidate)):
            return org_row.get(candidate)
    return ""


def _make_template_from_reference(rows: List[Dict[str, Any]], mapping: Dict[str, Any]) -> pd.DataFrame:
    columns = [f.get("excel_column") for f in mapping.get("fields", []) if f.get("excel_column")]
    if not rows:
        return pd.DataFrame(columns=columns)
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
    return pd.DataFrame(records, columns=columns).astype(object)


def _mapping_for_selected(base_mapping: Dict[str, Any], selected_columns: List[str], route_columns: List[str]) -> Dict[str, Any]:
    mapping_with_display = _add_display_field_to_mapping(base_mapping)
    ordered_columns: List[str] = []
    for col in ["OrganizationCode", "OrganizationName", *route_columns]:
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


def _render_field_selector(mapping: Dict[str, Any], state_key: str, route_columns: List[str], default_sections: List[str]) -> List[str]:
    payload_fields = [f for f in mapping.get("fields", []) if f.get("send_to_payload", True)]
    sections = sorted({f.get("section", "General") for f in payload_fields})

    if state_key not in st.session_state:
        st.session_state[state_key] = _selected_columns_from_preset(mapping, "standard")

    st.write("**Pilih field yang ingin disertakan di template PATCH**")
    p1, p2, p3, p4 = st.columns(4)
    if p1.button("Minimal", key=f"{state_key}_minimal", use_container_width=True):
        st.session_state[state_key] = _selected_columns_from_preset(mapping, "minimal")
        st.rerun()
    if p2.button("Standard", key=f"{state_key}_standard", use_container_width=True):
        st.session_state[state_key] = _selected_columns_from_preset(mapping, "standard")
        st.rerun()
    if p3.button("All fields", key=f"{state_key}_all", use_container_width=True):
        st.session_state[state_key] = _selected_columns_from_preset(mapping, "all")
        st.rerun()
    if p4.button("Clear optional", key=f"{state_key}_clear", use_container_width=True):
        st.session_state[state_key] = list(route_columns)
        st.rerun()

    selected = set(st.session_state.get(state_key, []))
    selected.update(route_columns)

    visible_sections = st.multiselect(
        "Section yang ditampilkan",
        options=sections,
        default=[s for s in default_sections if s in sections] or sections[:3],
        key=f"{state_key}_sections",
        help="Filter tampilan field supaya tidak terlalu panjang. Field yang sudah terpilih tidak hilang, hanya tidak ditampilkan.",
    )
    for section in visible_sections:
        fields = [f for f in payload_fields if f.get("section", "General") == section]
        selected_count = sum(1 for f in fields if f.get("excel_column") in selected)
        with st.expander(f"{section} ({selected_count}/{len(fields)} selected)", expanded=True):
            csec1, csec2 = st.columns(2)
            if csec1.button(f"Pilih semua {section}", key=f"{state_key}_pick_{section}", use_container_width=True):
                selected.update(f.get("excel_column") for f in fields if f.get("excel_column"))
                st.session_state[state_key] = list(selected)
                st.rerun()
            if csec2.button(f"Kosongkan {section}", key=f"{state_key}_clear_{section}", use_container_width=True):
                selected.difference_update(f.get("excel_column") for f in fields if f.get("excel_column"))
                selected.update(route_columns)
                st.session_state[state_key] = list(selected)
                st.rerun()
            for field in fields:
                col = field.get("excel_column")
                label = field.get("label", col)
                checked = st.checkbox(
                    f"{label} · `{col}`",
                    value=col in selected,
                    key=f"{state_key}_field_{col}",
                    help=field.get("description", ""),
                )
                if checked:
                    selected.add(col)
                else:
                    selected.discard(col)

    selected.update(route_columns)
    ordered = [f["excel_column"] for f in mapping.get("fields", []) if f.get("excel_column") in selected]
    st.session_state[state_key] = ordered
    return ordered


def _store_selected_reference_rows(org_df: pd.DataFrame, selected_labels: List[str], label_map: Dict[str, int]) -> None:
    rows = []
    for label in selected_labels:
        idx = label_map.get(label)
        if idx is None or idx not in org_df.index:
            continue
        rows.append(org_df.loc[idx].to_dict())
    st.session_state["update_builder_selected_org_rows"] = rows


def _render_downloads(mapping: Dict[str, Any], edited_template_df: pd.DataFrame, raw_get: Dict[str, Any], sample_payload: Dict[str, Any], error: str | None, prefix: str, title: str) -> None:
    with st.expander(f"Preview sample PATCH payload dari row pertama — {title}", expanded=True):
        if error:
            st.error(error)
        else:
            st.json(sample_payload)

    extra_files = {
        f"{prefix}_raw_get.json": make_json_bytes(raw_get),
        f"{prefix}_sample_patch_payload.json": make_json_bytes(sample_payload if not error else {"error": error}),
    }
    d1, d2, d3, d4 = st.columns(4)
    with d1:
        st.download_button(
            "Download Template Excel",
            data=make_template_excel_bytes(mapping, template_df=edited_template_df, sheet_name=mapping.get("worksheet_name", "Update_Template")),
            file_name=f"{prefix}_from_oracle_reference.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Download Mapping JSON",
            data=make_json_bytes(mapping),
            file_name=f"{prefix}_mapping.json",
            mime="application/json",
            use_container_width=True,
        )
    with d3:
        st.download_button(
            "Download Sample PATCH JSON",
            data=make_json_bytes(sample_payload if not error else {"error": error}),
            file_name=f"{prefix}_sample_payload.json",
            mime="application/json",
            use_container_width=True,
        )
    with d4:
        st.download_button(
            "Download Bundle ZIP",
            data=make_bundle_zip_bytes(mapping, template_df=edited_template_df, extra_files=extra_files),
            file_name=f"{prefix}_reference_bundle.zip",
            mime="application/zip",
            use_container_width=True,
        )
    st.download_button(
        "Download Raw GET JSON",
        data=make_json_bytes(raw_get),
        file_name=f"{prefix}_raw_get.json",
        mime="application/json",
        use_container_width=True,
    )


def _render_template_preview(update_area: str, rows: List[Dict[str, Any]]) -> None:
    if update_area == "IO Parameters":
        base_mapping = PARAM_MAPPING_BASE
        route_cols = PARAM_ROUTE_COLUMNS
        state_key = "param_builder_selected_payload_v24"
        default_sections = ["Inventory Settings", "Subinventory Defaults", "Movement Request"]
        prefix = "io_parameters_update"
        title = "IO Parameters"
    else:
        base_mapping = USAGE_MAPPING_BASE
        route_cols = USAGE_ROUTE_COLUMNS
        state_key = "usage_builder_selected_payload_v24"
        default_sections = ["Additional Usages"]
        prefix = "organization_usage_update"
        title = "Organization Usage"

    st.subheader(f"3. Pilih field tambahan untuk template PATCH — {title}")
    selected_columns = _render_field_selector(base_mapping, state_key, route_cols, default_sections)
    selected_mapping = _mapping_for_selected(base_mapping, selected_columns, route_cols)
    template_df = _make_template_from_reference(rows, selected_mapping)

    st.subheader("4. Preview template dari hasil fetch")
    c1, c2, c3, c4 = st.columns(4)
    payload_fields_count = sum(1 for f in selected_mapping.get("fields", []) if f.get("send_to_payload", True))
    c1.metric("Selected IO", len(template_df))
    c2.metric("Excel columns", len(template_df.columns))
    c3.metric("Payload fields", payload_fields_count)
    c4.metric("Route/display fields", len(template_df.columns) - payload_fields_count)

    with st.expander("Preview & edit data template", expanded=True):
        st.caption("Field True/False bisa diedit langsung sebagai checkbox. Route/display field dikunci supaya tidak sengaja berubah.")
        editor_df = _prepare_editor_dataframe(template_df, selected_mapping)
        disabled_columns = [col for col in ["OrganizationCode", "OrganizationName", "OrganizationId", "OrganizationId2"] if col in editor_df.columns]
        edited_template_df = st.data_editor(
            editor_df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            disabled=disabled_columns,
            column_config=_editor_column_config(selected_mapping),
            key=f"{prefix}_template_editor",
        )

    sample_payload = {}
    sample_error = None
    if not edited_template_df.empty:
        try:
            sample_payload = build_payload_from_row(edited_template_df.iloc[0], selected_mapping)
        except Exception as exc:
            sample_error = str(exc)

    raw_get = {
        "inventory_organizations": st.session_state.get("update_builder_org_raw", {}),
        "inv_org_parameters": st.session_state.get("update_builder_raw_params", {}) if update_area == "IO Parameters" else {},
    }
    _render_downloads(selected_mapping, edited_template_df, raw_get, sample_payload, sample_error, prefix, title)


# 1. Connection and fetch IO list
st.subheader("1. Fetch Inventory Organizations dari Oracle")
base_url, username, password, timeout = render_connection_form("update_builder_fetch")
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
            org_df, org_resp = fetch_inventory_orgs(client, limit=int(limit), q_filter=q_filter, expand_children=False)
            if int(offset) > 0 and not org_df.empty:
                org_df = org_df.iloc[int(offset):].copy()
            st.session_state["update_builder_org_df"] = org_df
            st.session_state["update_builder_org_raw"] = {"ok": org_resp.ok, "status_code": org_resp.status_code, "url": org_resp.url, "body": org_resp.body}
            st.session_state.pop("update_builder_reference_rows", None)
            st.success(f"Fetched {len(org_df)} Inventory Organization row(s).")
        except Exception as exc:
            st.error(f"Gagal fetch Inventory Organizations: {exc}")

org_df = st.session_state.get("update_builder_org_df")
if isinstance(org_df, pd.DataFrame) and not org_df.empty:
    st.subheader("2. Pilih IO dan area update")
    keyword = st.text_input("Search/filter hasil fetch", placeholder="Cari OrganizationCode atau OrganizationName")
    filtered_org_df = _filter_org_df(org_df, keyword)
    st.caption(f"Menampilkan {len(filtered_org_df)} dari {len(org_df)} IO. Pilih maksimal {MAX_SELECTED_IO} IO.")

    with st.expander("Preview IO hasil fetch", expanded=False):
        preferred_cols = [c for c in ["OrganizationCode", "OrganizationName", "OrganizationId", "Status", "LocationId", "ManufacturingPlantFlag", "MaintenanceEnabledFlag", "ContractManufacturingFlag", "IntegratedSystemType"] if c in filtered_org_df.columns]
        st.dataframe(filtered_org_df[preferred_cols] if preferred_cols else filtered_org_df, use_container_width=True)

    labels = [_label_for_org(row) for _, row in filtered_org_df.iterrows()]
    label_map = {label: idx for label, idx in zip(labels, filtered_org_df.index)}
    selected_labels = st.multiselect("Select IO", labels, max_selections=MAX_SELECTED_IO)
    _store_selected_reference_rows(filtered_org_df, selected_labels, label_map)

    update_area = st.radio(
        "Update area",
        ["IO Parameters", "Organization Usage"],
        horizontal=True,
        help="IO Parameters = child invOrgParameters. Organization Usage = parent inventoryOrganizations Additional Usages.",
    )

    selected_org_rows = st.session_state.get("update_builder_selected_org_rows", [])
    if selected_labels:
        st.success(f"{len(selected_labels)} IO dipilih untuk {update_area}.")
    else:
        st.info("Pilih minimal 1 IO untuk generate template update.")

    if update_area == "Organization Usage":
        if selected_org_rows:
            rows = [{"org": org, "params": {}, "error": ""} for org in selected_org_rows]
            st.session_state["update_builder_reference_rows_usage"] = rows
            _render_template_preview("Organization Usage", rows)
    else:
        if st.button("📥 Fetch selected IO parameters", disabled=not selected_labels, use_container_width=True):
            client = _make_client(base_url, username, password, int(timeout))
            if client:
                reference_rows: List[Dict[str, Any]] = []
                raw: Dict[str, Any] = {}
                progress = st.progress(0)
                for pos, org in enumerate(selected_org_rows):
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
                    progress.progress((pos + 1) / max(len(selected_org_rows), 1))
                st.session_state["update_builder_reference_rows_params"] = reference_rows
                st.session_state["update_builder_raw_params"] = raw
                errors = [r for r in reference_rows if r.get("error")]
                if errors:
                    st.warning(f"Fetched {len(reference_rows)} IO. Ada {len(errors)} IO dengan warning/error, cek preview.")
                else:
                    st.success(f"Fetched invOrgParameters untuk {len(reference_rows)} IO.")

        reference_rows = st.session_state.get("update_builder_reference_rows_params")
        if reference_rows:
            _render_template_preview("IO Parameters", reference_rows)
else:
    st.divider()
    st.subheader("Fallback: buat template kosong")
    st.caption("Pakai ini kalau belum mau fetch Oracle. Untuk template berbasis current value, fetch IO dulu.")
    from services.ui_helpers import render_builder_downloads

    fb_mode = st.radio("Template kosong untuk", ["IO Parameters", "Organization Usage"], horizontal=True)
    if fb_mode == "IO Parameters":
        fb_mapping = _mapping_for_selected(PARAM_MAPPING_BASE, _selected_columns_from_preset(PARAM_MAPPING_BASE, "standard"), PARAM_ROUTE_COLUMNS)
    else:
        fb_mapping = _mapping_for_selected(USAGE_MAPPING_BASE, _selected_columns_from_preset(USAGE_MAPPING_BASE, "standard"), USAGE_ROUTE_COLUMNS)
    render_builder_downloads(fb_mapping, [f.get("excel_column") for f in fb_mapping.get("fields", [])], key_prefix="update_fallback")
