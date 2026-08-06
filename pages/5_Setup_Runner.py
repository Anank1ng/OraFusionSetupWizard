import json
from typing import Any, Dict, Tuple

import pandas as pd
import streamlit as st

from services.config_loader import filter_mapping_by_preset, load_schema, schema_title
from services.excel_service import (
    make_json_bytes,
    make_setup_workbook_bytes,
    make_template_excel_bytes,
    read_upload_dataframe,
)
from services.oracle_client import OracleFusionClient
from services.reference_service import fetch_inventory_orgs, organization_lookup_from_reference
from services.setup_runner import RunnerConfig, run_create_io, run_create_subinventories, run_patch_io_parameters
from services.ui_helpers import render_connection_form
from services.validation_service import validation_summary

st.set_page_config(page_title="Setup Runner", page_icon="🚀", layout="wide")
st.title("🚀 Setup Runner")
st.caption("Patch v2.1 — runner dipisah per entitas: Create Minimal IO, Create Subinventories, atau Patch IO Parameters.")

BUILT_IN_MAPPINGS = {
    "Create Minimal IO": filter_mapping_by_preset(load_schema("minimal_inventory_organizations"), "minimal"),
    "Create Subinventories": filter_mapping_by_preset(load_schema("subinventories"), "minimal"),
    "Patch IO Parameters": filter_mapping_by_preset(load_schema("io_parameters_update"), "standard"),
}

PROCESS_NOTES = {
    "Create Minimal IO": {
        "sheet": "Inventory_Organizations",
        "desc": "Membuat Inventory Organization minimal. Output penting: OrganizationCode → OrganizationId.",
        "needs": "Tidak butuh OrganizationId karena Oracle akan generate saat create.",
    },
    "Create Subinventories": {
        "sheet": "Subinventories",
        "desc": "Membuat subinventory untuk IO yang sudah ada/baru dibuat.",
        "needs": "Butuh OrganizationId. Bisa isi langsung di Excel, atau isi OrganizationCode lalu app akan coba resolve dari existing IO saat live run.",
    },
    "Patch IO Parameters": {
        "sheet": "IO_Parameters_Update",
        "desc": "Update parameter IO seperti default subinventory, inventory settings, lot/serial, movement request, dan lainnya.",
        "needs": "Butuh OrganizationId dan child key invOrgParameters. App akan GET child invOrgParameters saat live run.",
    },
}


def _json_previewable(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _load_custom_mapping(uploaded_mapping) -> Tuple[Dict[str, Any] | None, str | None]:
    if uploaded_mapping is None:
        return None, None
    try:
        raw = uploaded_mapping.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        mapping = json.loads(raw)
        if not isinstance(mapping, dict):
            return None, "Mapping JSON harus berupa object/dictionary."
        if "fields" not in mapping or not isinstance(mapping.get("fields"), list):
            return None, "Mapping JSON tidak valid: key 'fields' tidak ditemukan atau bukan list."
        if not mapping.get("worksheet_name"):
            mapping["worksheet_name"] = "Upload_Template"
        return mapping, None
    except Exception as exc:
        return None, f"Gagal membaca mapping JSON: {exc}"


def _extract_org_map_from_log(uploaded_log) -> Dict[str, int]:
    """Optional helper: seed OrganizationCode -> OrganizationId from previous Setup Log CSV/JSON."""
    result: Dict[str, int] = {}
    if uploaded_log is None:
        return result
    try:
        name = uploaded_log.name.lower()
        if name.endswith(".json"):
            data = json.loads(uploaded_log.read().decode("utf-8"))
            df = pd.DataFrame(data)
        else:
            df = pd.read_csv(uploaded_log)
        for _, row in df.iterrows():
            org_id = row.get("OrganizationId")
            key = row.get("business_key") or row.get("OrganizationCode")
            if pd.isna(org_id) or not key:
                continue
            key = str(key).strip().split("/")[0]
            try:
                result[key] = int(float(org_id))
            except Exception:
                continue
    except Exception:
        return {}
    return result


# Built-in downloads are kept at the top so users can grab files before running.
st.subheader("1. Download template bawaan")
io_mapping_builtin = BUILT_IN_MAPPINGS["Create Minimal IO"]
subinv_mapping_builtin = BUILT_IN_MAPPINGS["Create Subinventories"]
params_mapping_builtin = BUILT_IN_MAPPINGS["Patch IO Parameters"]

c_dl1, c_dl2, c_dl3, c_dl4 = st.columns(4)
with c_dl1:
    st.download_button(
        "Download Full Setup Workbook",
        data=make_setup_workbook_bytes(io_mapping_builtin, subinv_mapping_builtin, params_mapping_builtin),
        file_name="io_setup_workbook.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
with c_dl2:
    st.download_button(
        "Template Minimal IO",
        data=make_template_excel_bytes(io_mapping_builtin),
        file_name="minimal_inventory_organizations_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
with c_dl3:
    st.download_button(
        "Template Subinventories",
        data=make_template_excel_bytes(subinv_mapping_builtin),
        file_name="subinventories_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
with c_dl4:
    st.download_button(
        "Template IO Parameters",
        data=make_template_excel_bytes(params_mapping_builtin),
        file_name="io_parameters_update_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

st.divider()
st.subheader("2. Pilih proses yang mau dijalankan")
process = st.radio(
    "Entity process",
    ["Create Minimal IO", "Create Subinventories", "Patch IO Parameters"],
    horizontal=True,
    label_visibility="collapsed",
)

note = PROCESS_NOTES[process]
st.info(f"**{process}** — {note['desc']}\n\n**Kebutuhan:** {note['needs']}")

st.subheader("3. Mapping & input file")
builtin_mapping = BUILT_IN_MAPPINGS[process]

m1, m2 = st.columns([1, 1])
with m1:
    mapping_source = st.radio(
        "Mapping source",
        ["Use built-in mapping", "Upload custom mapping JSON"],
        horizontal=True,
    )
with m2:
    st.caption("Mapping aktif")
    st.code(schema_title(builtin_mapping), language="text")

custom_mapping = None
if mapping_source == "Upload custom mapping JSON":
    mapping_file = st.file_uploader("Upload mapping JSON", type=["json"], key=f"mapping_{process}")
    custom_mapping, mapping_error = _load_custom_mapping(mapping_file)
    if mapping_error:
        st.error(mapping_error)
        st.stop()

mapping = custom_mapping or builtin_mapping

c_map1, c_map2 = st.columns(2)
with c_map1:
    st.download_button(
        "Download mapping JSON aktif",
        data=make_json_bytes(mapping),
        file_name=f"{mapping.get('api_key', process.lower().replace(' ', '_'))}_mapping.json",
        mime="application/json",
        use_container_width=True,
    )
with c_map2:
    st.download_button(
        "Download template dari mapping aktif",
        data=make_template_excel_bytes(mapping),
        file_name=f"{mapping.get('api_key', process.lower().replace(' ', '_'))}_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

uploaded = st.file_uploader(
    f"Upload Excel/CSV untuk {process}",
    type=["xlsx", "csv"],
    help=f"Kalau memakai full setup workbook, app akan membaca sheet {mapping.get('worksheet_name')}. Kalau file hanya 1 sheet, app akan membaca sheet pertama.",
)

st.divider()
st.subheader("4. Connection & run options")
base_url, username, password, timeout = render_connection_form("runner_entity")

opt_cols = st.columns(4)
with opt_cols[0]:
    dry_run = st.toggle("Dry Run Mode", value=True, help="Aman untuk preview. Tidak mengirim POST/PATCH ke Oracle.")
with opt_cols[1]:
    upsert_io = st.toggle("Upsert IO", value=False, disabled=(process != "Create Minimal IO"))
with opt_cols[2]:
    stop_on_error = st.toggle("Stop on first error", value=False)
with opt_cols[3]:
    test_row_idx = st.number_input("Test row index", min_value=0, value=0, step=1)

with st.expander("Opsional: seed OrganizationId dari log sebelumnya"):
    st.caption("Dipakai untuk Create Subinventories / Patch IO Parameters kalau Excel hanya punya OrganizationCode. Format boleh CSV/JSON dari hasil Setup Log sebelumnya.")
    previous_log_file = st.file_uploader("Upload previous IO log CSV/JSON", type=["csv", "json"], key="previous_log")
    seeded_org_map = _extract_org_map_from_log(previous_log_file)
    if seeded_org_map:
        st.success(f"Loaded {len(seeded_org_map)} OrganizationCode → OrganizationId dari log sebelumnya.")
        st.json(seeded_org_map)

if uploaded:
    try:
        df = read_upload_dataframe(uploaded, sheet_name=mapping.get("worksheet_name"))
    except Exception as exc:
        st.error(f"Gagal membaca file upload: {exc}")
        st.stop()

    st.subheader("5. Preview & validation")
    st.caption(f"Worksheet target: {mapping.get('worksheet_name', 'Upload_Template')} | Rows terbaca: {len(df)}")
    st.dataframe(df, use_container_width=True)

    summary = validation_summary(df, mapping, strict_template=False)
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Rows", summary["total_rows"])
    v2.metric("Missing columns", len(summary["missing_columns"]))
    v3.metric("Required errors", summary["required_error_count"])
    v4.metric("Payload errors", summary["payload_error_count"])

    if summary["is_valid"]:
        st.success("Validasi awal OK untuk mapping aktif.")
    else:
        st.warning("Ada hal yang perlu dicek sebelum live run.")
        if summary["missing_columns"]:
            st.error(f"Missing columns: {summary['missing_columns']}")
        with st.expander("Detail validation errors"):
            if not summary["required_errors"].empty:
                st.write("Required errors")
                st.dataframe(summary["required_errors"], use_container_width=True)
            if not summary["payload_errors"].empty:
                st.write("Payload errors")
                st.dataframe(summary["payload_errors"], use_container_width=True)

    st.subheader("6. Run selected process")
    cfg = RunnerConfig(dry_run=dry_run, upsert_io=upsert_io, stop_on_error=stop_on_error)

    if not df.empty:
        selected_idx = min(int(test_row_idx), len(df) - 1)
        st.caption(f"Test selected row akan memakai row index {selected_idx} / Excel row {selected_idx + 2}.")

    run_cols = st.columns(3)
    run_test = run_cols[0].button("🧪 Test Selected Row", type="secondary", use_container_width=True)
    run_all = run_cols[1].button("🚀 Run All Rows", type="primary", use_container_width=True)
    clear_log = run_cols[2].button("🧹 Clear Result", use_container_width=True)

    if clear_log:
        st.session_state.pop("entity_runner_log", None)
        st.session_state.pop("entity_runner_org_map", None)
        st.rerun()

    if run_test or run_all:
        if not dry_run and (not base_url or not username or not password):
            st.error("Untuk live run, base URL, username, dan password wajib diisi.")
            st.stop()

        if df.empty:
            st.error("File upload tidak punya row data.")
            st.stop()

        run_df = df.iloc[[min(int(test_row_idx), len(df) - 1)]].copy() if run_test else df.copy()
        client = None if dry_run else OracleFusionClient(base_url, username, password, timeout=timeout)

        org_map: Dict[str, int] = dict(seeded_org_map)
        # For isolated Subinventory/Patch runs, resolve OrganizationCode from Oracle when live.
        if process in {"Create Subinventories", "Patch IO Parameters"} and not dry_run and client is not None:
            try:
                ref_df, _ = fetch_inventory_orgs(client, limit=500, expand_children=False)
                org_map.update(organization_lookup_from_reference(ref_df))
                st.info(f"Resolved existing IO reference: {len(org_map)} OrganizationCode → OrganizationId tersedia.")
            except Exception as exc:
                st.warning(f"Gagal fetch existing IO untuk resolve OrganizationCode: {exc}")

        if process == "Create Minimal IO":
            log_df, created_map = run_create_io(run_df, mapping, client, cfg)
            org_map.update(created_map)
        elif process == "Create Subinventories":
            log_df = run_create_subinventories(run_df, mapping, client, cfg, org_map)
        else:
            log_df = run_patch_io_parameters(run_df, mapping, client, cfg, org_map)

        st.session_state["entity_runner_log"] = log_df
        st.session_state["entity_runner_org_map"] = org_map

log_df = st.session_state.get("entity_runner_log")
if isinstance(log_df, pd.DataFrame) and not log_df.empty:
    st.subheader("Result Log")
    view = log_df.copy()
    for col in ["request_payload", "response_body"]:
        if col in view.columns:
            view[col] = view[col].apply(lambda v: _json_previewable(v) if isinstance(v, (dict, list)) else v)
    st.dataframe(view, use_container_width=True)
    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "Download Entity Runner Log CSV",
            data=view.to_csv(index=False).encode("utf-8"),
            file_name="entity_runner_log.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Download Entity Runner Log JSON",
            data=make_json_bytes(log_df.to_dict(orient="records")),
            file_name="entity_runner_log.json",
            mime="application/json",
            use_container_width=True,
        )
    if st.session_state.get("entity_runner_org_map"):
        with st.expander("OrganizationCode → OrganizationId map"):
            st.json(st.session_state.get("entity_runner_org_map"))
else:
    st.caption("Upload file dan pilih Test/Run untuk melihat result log.")
