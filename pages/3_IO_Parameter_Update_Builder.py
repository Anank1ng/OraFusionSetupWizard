import json
import hashlib
from copy import deepcopy
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from services.config_loader import filter_mapping_by_columns, load_schema, schema_title
from services.excel_service import make_bundle_zip_bytes, make_json_bytes, make_template_excel_bytes
from services.io_parameter_reference import (
    build_reference_template,
    clean_editor_dataframe,
    nullable_checkbox_value,
    missing_child_key_warnings,
    preserve_untouched_blank_booleans,
    reference_status_table,
    selection_signature,
    validate_reference_rows,
)
from services.oracle_client import OracleFusionClient
from services.payload_builder import build_payload_from_row, is_blank
from services.reference_service import fetch_inventory_orgs, fetch_inv_org_parameters
from services.ui_helpers import render_connection_form

st.set_page_config(page_title="IO Parameter Update Builder", page_icon="🛠️", layout="wide")
st.title("🛠️ IO Parameter Update Builder")
st.caption("Patch v2.3 — fetch existing IO, edit True/False langsung di preview, lalu download template PATCH.")

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
        mask = mask | df[col].astype(str).str.lower().str.contains(keyword, na=False, regex=False)
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

def _prepare_editor_dataframe(df: pd.DataFrame, mapping: Dict[str, Any]) -> pd.DataFrame:
    """Pastikan boolean field tampil sebagai checkbox di st.data_editor."""
    edited_df = df.copy().astype(object)
    lookup = _field_lookup(mapping)
    for col in edited_df.columns:
        field = lookup.get(col, {})
        if field.get("type") == "boolean":
            edited_df[col] = pd.Series(
                [nullable_checkbox_value(v) for v in edited_df[col].tolist()],
                index=edited_df.index,
                dtype="boolean",
            )
    return edited_df


def _editor_column_config(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Konfigurasi kolom agar boolean editable sebagai checkbox dan route field terkunci."""
    lookup = _field_lookup(mapping)
    config: Dict[str, Any] = {}
    for col, field in lookup.items():
        label = field.get("label") or col
        help_text = field.get("description") or field.get("reference_hint") or None
        if field.get("type") == "boolean":
            config[col] = st.column_config.CheckboxColumn(label=label, help=help_text, required=False)
        elif field.get("type") == "integer":
            config[col] = st.column_config.NumberColumn(label=label, help=help_text, step=1, format="%d")
        else:
            config[col] = st.column_config.TextColumn(label=label, help=help_text)
    return config


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
    preset_buttons = {
        "minimal": p1.button("Minimal", use_container_width=True),
        "standard": p2.button("Standard", use_container_width=True),
        "all": p3.button("All fields", use_container_width=True),
        "clear": p4.button("Clear optional", use_container_width=True),
    }
    for preset, clicked in preset_buttons.items():
        if clicked:
            chosen = (
                set(ROUTE_COLUMNS) if preset == "clear"
                else set(_selected_columns_from_preset(mapping, preset))
            )
            st.session_state["param_builder_selected_payload"] = list(chosen)
            # Streamlit checkbox keys persist across reruns. Update them with the
            # preset, otherwise visually checked values can contradict selection.
            for field in payload_fields:
                col = field.get("excel_column")
                if col:
                    st.session_state[f"param_field_{col}"] = col in chosen
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
    limit = st.number_input(
        "Fetch limit", min_value=1, max_value=500, value=60, step=1,
        help="Boleh fetch lebih dari 50; maksimal 50 IO untuk template."
    )
with fc2:
    offset = st.number_input("Offset", min_value=0, value=0, step=1)
with fc3:
    q_filter = st.text_input("Optional q filter", placeholder="Contoh: OrganizationCode LIKE 'WH%'")

instance_context = (base_url.strip().rstrip("/").lower(), username.strip())
if st.button("🔎 Fetch IO List", type="primary", use_container_width=True):
    # Any new list request invalidates previously fetched parameters and editable data.
    for key in (
        "param_builder_org_df", "param_builder_org_raw", "param_builder_list_context",
        "param_builder_reference_rows", "param_builder_reference_signature",
        "param_builder_raw_params",
    ):
        st.session_state.pop(key, None)
    st.session_state["param_builder_list_generation"] = (
        st.session_state.get("param_builder_list_generation", 0) + 1
    )
    client = _make_client(base_url, username, password, int(timeout))
    if client:
        try:
            org_df, org_resp = fetch_inventory_orgs(
                client, limit=int(limit), q_filter=q_filter, expand_children=False
            )
            if not org_resp.ok:
                raise RuntimeError(
                    f"HTTP {org_resp.status_code}: {str(org_resp.body)[:400]}"
                )
            if int(offset) > 0 and not org_df.empty:
                # Existing fetch helper does not yet support server-side offset.
                org_df = org_df.iloc[int(offset):].copy()
            st.session_state["param_builder_org_df"] = org_df
            st.session_state["param_builder_org_raw"] = {
                "ok": org_resp.ok, "status_code": org_resp.status_code,
                "url": org_resp.url, "body": org_resp.body,
            }
            st.session_state["param_builder_list_context"] = instance_context
            st.success(f"Fetched {len(org_df)} Inventory Organization row(s).")
        except Exception as exc:
            st.error(f"Gagal GET Inventory Organizations: {exc}")

org_df = st.session_state.get("param_builder_org_df")
list_context_matches = st.session_state.get("param_builder_list_context") == instance_context
if isinstance(org_df, pd.DataFrame) and not list_context_matches:
    st.warning("Instance atau username berubah. Klik Fetch IO List lagi sebelum memilih IO.")

selected_org_rows: List[Dict[str, Any]] = []
current_signature = None
if isinstance(org_df, pd.DataFrame) and not org_df.empty and list_context_matches:
    st.subheader("2. Pilih IO yang mau dibuatkan template update")
    keyword = st.text_input(
        "Search/filter hasil fetch",
        placeholder="Cari OrganizationCode atau OrganizationName"
    )
    filtered_org_df = _filter_org_df(org_df, keyword)
    st.caption(
        f"Menampilkan {len(filtered_org_df)} dari {len(org_df)} IO. "
        f"Pilih maksimal {MAX_SELECTED_IO} IO."
    )

    with st.expander("Preview IO hasil fetch", expanded=False):
        preferred_cols = [
            col for col in ["OrganizationCode", "OrganizationName", "OrganizationId", "Status", "LocationId"]
            if col in filtered_org_df.columns
        ]
        st.dataframe(
            filtered_org_df[preferred_cols] if preferred_cols else filtered_org_df,
            use_container_width=True,
        )

    labels = [_label_for_org(row) for _, row in filtered_org_df.iterrows()]
    label_map = {label: idx for label, idx in zip(labels, filtered_org_df.index)}
    selection_key = f"param_builder_select_{st.session_state.get('param_builder_list_generation', 0)}"
    selected_labels = st.multiselect(
        "Select IO", labels, max_selections=MAX_SELECTED_IO, key=selection_key
    )
    selected_org_rows = [
        filtered_org_df.loc[label_map[label]].to_dict()
        for label in selected_labels if label in label_map
    ]
    current_signature = selection_signature(
        base_url, username, selected_org_rows,
        st.session_state.get("param_builder_list_generation", 0),
    ) if selected_org_rows else None

    if selected_org_rows:
        st.success(f"{len(selected_org_rows)} IO dipilih.")
    else:
        st.info("Pilih minimal 1 IO untuk fetch child invOrgParameters.")

    # This button is mandatory; choosing an IO alone never generates an Oracle template.
    if st.button(
        "📥 Fetch Selected IO Parameters — wajib sebelum download",
        disabled=not selected_org_rows, use_container_width=True, type="primary",
    ):
        client = _make_client(base_url, username, password, int(timeout))
        if client:
            reference_rows: List[Dict[str, Any]] = []
            raw: Dict[str, Any] = {}
            progress = st.progress(0, text="GET child invOrgParameters...")
            for pos, org in enumerate(selected_org_rows):
                org_id = _safe_int(org.get("OrganizationId"))
                org_code = str(org.get("OrganizationCode") or org_id or pos)
                params: Dict[str, Any] = {}
                error = ""
                if org_id is None:
                    error = "OrganizationId kosong/tidak valid"
                    raw[org_code] = {"error": error}
                else:
                    try:
                        params_df, params_resp = fetch_inv_org_parameters(client, org_id)
                        raw[org_code] = {
                            "ok": params_resp.ok, "status_code": params_resp.status_code,
                            "url": params_resp.url, "body": params_resp.body,
                        }
                        if not params_resp.ok:
                            error = (
                                f"GET invOrgParameters gagal (HTTP {params_resp.status_code}): "
                                f"{str(params_resp.body)[:180]}"
                            )
                        elif params_df.empty:
                            error = "GET invOrgParameters berhasil tetapi tidak ada data child."
                        else:
                            params = params_df.iloc[0].to_dict()
                    except Exception as exc:
                        error = str(exc)
                        raw[org_code] = {"error": error}
                reference_rows.append({"org": org, "params": params, "error": error})
                progress.progress(
                    (pos + 1) / max(len(selected_org_rows), 1),
                    text=f"GET {pos + 1}/{len(selected_org_rows)} IO",
                )
            st.session_state["param_builder_reference_rows"] = reference_rows
            st.session_state["param_builder_reference_signature"] = current_signature
            st.session_state["param_builder_raw_params"] = raw
            st.session_state["param_builder_reference_generation"] = (
                st.session_state.get("param_builder_reference_generation", 0) + 1
            )
            blockers = validate_reference_rows(reference_rows)
            if blockers:
                st.error(
                    f"GET selesai, tetapi {len(blockers)} IO belum siap untuk template PATCH. "
                    "Lihat status di bawah."
                )
            else:
                st.success(
                    f"GET berhasil untuk {len(reference_rows)} IO. "
                    "Template sekarang menggunakan nilai Oracle yang dipilih."
                )

stored_rows = st.session_state.get("param_builder_reference_rows") or []
fetched_signature = st.session_state.get("param_builder_reference_signature")
has_current_get = bool(stored_rows) and bool(current_signature) and (
    fetched_signature == current_signature and len(stored_rows) == len(selected_org_rows)
)
if stored_rows and not has_current_get:
    st.warning(
        "Pilihan IO, instance, atau daftar fetch sudah berubah sejak GET terakhir. "
        "Hasil GET lama tidak dipakai. Klik Fetch Selected IO Parameters lagi."
    )

if has_current_get:
    st.divider()
    st.subheader("3. Pilih parameter untuk PATCH")
    selected_columns = _render_parameter_selector(BASE_MAPPING)
    selected_mapping = _make_mapping_for_selected(selected_columns)
    st.subheader("4. Validasi GET dan preview template")
    st.caption(
        "Status berikut menunjukkan IO yang benar-benar diambil dari Oracle. "
        "Field optional kosong tidak diisi dengan sample dan tidak dikirim saat PATCH."
    )
    status_table = reference_status_table(stored_rows, selected_mapping)
    st.dataframe(status_table, use_container_width=True, hide_index=True)
    blocked = validate_reference_rows(stored_rows)
    if blocked:
        st.error(
            "Download template hasil Oracle dinonaktifkan karena data GET belum lengkap "
            "atau child key tidak tersedia. Fetch ulang setelah masalah diperbaiki."
        )
        with st.expander("Detail GET yang perlu diperbaiki", expanded=True):
            for issue in blocked:
                st.write("- " + issue)
    else:
        template_df = build_reference_template(stored_rows, selected_mapping)
        missing_count = int(status_table["Field kosong/tidak tersedia"].sum())
        if missing_count:
            st.warning(
                f"{missing_count} nilai field parameter dari IO terpilih kosong/tidak tersedia "
                "(jumlah lintas IO). Nilai tersebut tetap kosong; bukan otomatis False atau sample."
            )

        m1, m2, m3, m4 = st.columns(4)
        payload_fields_count = sum(
            1 for field in selected_mapping.get("fields", [])
            if field.get("send_to_payload", True)
        )
        m1.metric("Selected IO", len(template_df))
        m2.metric("Excel columns", len(template_df.columns))
        m3.metric("Payload fields", payload_fields_count)
        m4.metric("Route/display fields", len(template_df.columns) - payload_fields_count)

        with st.expander("Preview & edit data template dari Oracle", expanded=True):
            st.caption(
                "Semua route key berasal dari GET IO yang dipilih. "
                "Checkbox boolean yang kosong tetap kosong hingga sengaja kamu edit; "
                "route/display fields dikunci."
            )
            editor_df = _prepare_editor_dataframe(template_df, selected_mapping)
            disabled_columns = [
                col for col in ["OrganizationCode", "OrganizationName", "OrganizationId", "OrganizationId2"]
                if col in editor_df.columns
            ]
            field_signature = hashlib.sha256(
                ",".join(editor_df.columns).encode("utf-8")
            ).hexdigest()[:10]
            editor_key = (
                f"param_update_editor_{st.session_state.get('param_builder_reference_generation', 0)}_"
                f"{current_signature}_{field_signature}"
            )
            edited_template_df = st.data_editor(
                editor_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                disabled=disabled_columns,
                column_config=_editor_column_config(selected_mapping),
                key=editor_key,
            )
            edited_template_df = preserve_untouched_blank_booleans(
                editor_df, edited_template_df, selected_mapping,
                st.session_state.get(editor_key),
            )

        payload_errors = []
        sample_patch_payload: Dict[str, Any] = {}
        for row_no, (_, row) in enumerate(edited_template_df.iterrows(), 1):
            try:
                payload = build_payload_from_row(row, selected_mapping)
                if row_no == 1:
                    sample_patch_payload = payload
            except Exception as exc:
                payload_errors.append(f"Row {row_no}: {exc}")

        with st.expander("Preview sample PATCH payload dari row pertama", expanded=True):
            if payload_errors:
                st.error("Ada field dengan nilai invalid. Periksa semua baris.")
                for msg in payload_errors[:10]:
                    st.write(msg)
            else:
                st.json(sample_patch_payload)
                if not sample_patch_payload:
                    st.info("Payload pertama kosong. Pilih/isi parameter jika ingin mengirim perubahan.")

        if payload_errors:
            st.error("Download dinonaktifkan hingga semua nilai template valid.")
        else:
            raw_get = {
                "inventory_organizations": st.session_state.get("param_builder_org_raw", {}),
                "inv_org_parameters": st.session_state.get("param_builder_raw_params", {}),
            }
            extra_files = {
                "raw_get_io_parameters.json": make_json_bytes(raw_get),
                "sample_patch_payload_from_reference.json": make_json_bytes(sample_patch_payload),
            }
            d1, d2, d3, d4 = st.columns(4)
            with d1:
                st.download_button(
                    "Download Oracle-based Template Excel",
                    data=make_template_excel_bytes(
                        selected_mapping, template_df=clean_editor_dataframe(edited_template_df),
                        sheet_name=selected_mapping.get("worksheet_name", "IO_Parameters_Update"),
                    ),
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
                    data=make_json_bytes(sample_patch_payload),
                    file_name="io_parameters_update_sample_payload.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with d4:
                st.download_button(
                    "Download Bundle ZIP",
                    data=make_bundle_zip_bytes(
                        selected_mapping, template_df=clean_editor_dataframe(edited_template_df),
                        extra_files=extra_files,
                    ),
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
    st.info(
        "Untuk template berbasis Oracle: Fetch IO List → Select IO → "
        "Fetch Selected IO Parameters → Preview → Download."
    )
    with st.expander("Blank / Sample Template — bukan hasil Oracle (IO_TEST01)", expanded=False):
        st.warning(
            "Template pada bagian ini hanya contoh dari schema (misalnya IO_TEST01). "
            "Jangan gunakan sebagai template untuk IO pilihanmu tanpa mengganti datanya."
        )
        fallback_columns = _render_parameter_selector(BASE_MAPPING)
        fallback_mapping = _make_mapping_for_selected(fallback_columns)
        from services.ui_helpers import render_builder_downloads

        render_builder_downloads(
            fallback_mapping,
            [f.get("excel_column") for f in fallback_mapping.get("fields", [])],
            key_prefix="params_fallback",
        )
