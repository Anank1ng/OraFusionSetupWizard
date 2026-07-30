import json

import pandas as pd
import streamlit as st

from services.config_loader import filter_mapping_by_preset, load_schema
from services.excel_service import make_json_bytes, make_setup_workbook_bytes, read_workbook_sheets
from services.oracle_client import OracleFusionClient
from services.reference_service import fetch_inventory_orgs, organization_lookup_from_reference
from services.setup_runner import RunnerConfig, run_create_io, run_create_subinventories, run_patch_io_parameters
from services.ui_helpers import render_connection_form
from services.validation_service import validation_summary

st.set_page_config(page_title="Setup Runner", page_icon="🚀", layout="wide")
st.title("🚀 Setup Runner")
st.caption("Runner bertahap: Create IO → Create Subinventory → Patch IO Parameters")

io_mapping = filter_mapping_by_preset(load_schema("minimal_inventory_organizations"), "minimal")
subinv_mapping = filter_mapping_by_preset(load_schema("subinventories"), "minimal")
params_mapping = filter_mapping_by_preset(load_schema("io_parameters_update"), "standard")

st.subheader("1. Download setup workbook")
st.download_button(
    "Download Full Setup Workbook",
    data=make_setup_workbook_bytes(io_mapping, subinv_mapping, params_mapping),
    file_name="io_setup_workbook.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()
st.subheader("2. Connection & runner options")
base_url, username, password, timeout = render_connection_form("runner")
c1, c2, c3 = st.columns(3)
with c1:
    dry_run = st.toggle("Dry Run Mode", value=True, help="Aman untuk preview. Tidak mengirim POST/PATCH ke Oracle.")
with c2:
    upsert_io = st.toggle("Upsert IO", value=False)
with c3:
    stop_on_error = st.toggle("Stop on first error", value=False)

step_cols = st.columns(3)
with step_cols[0]:
    run_step_io = st.checkbox("Run Step 1: Create IO", value=True)
with step_cols[1]:
    run_step_subinv = st.checkbox("Run Step 2: Create Subinventories", value=True)
with step_cols[2]:
    run_step_patch = st.checkbox("Run Step 3: Patch IO Parameters", value=True)

uploaded = st.file_uploader("Upload setup workbook (.xlsx)", type=["xlsx"])

if uploaded:
    sheets = read_workbook_sheets(uploaded, [io_mapping["worksheet_name"], subinv_mapping["worksheet_name"], params_mapping["worksheet_name"]])
    tab1, tab2, tab3 = st.tabs(["Inventory_Organizations", "Subinventories", "IO_Parameters_Update"])
    with tab1:
        st.dataframe(sheets[io_mapping["worksheet_name"]], use_container_width=True)
    with tab2:
        st.dataframe(sheets[subinv_mapping["worksheet_name"]], use_container_width=True)
    with tab3:
        st.dataframe(sheets[params_mapping["worksheet_name"]], use_container_width=True)

    st.subheader("3. Validation")
    summaries = {
        "Inventory_Organizations": validation_summary(sheets[io_mapping["worksheet_name"]], io_mapping, strict_template=False),
        "Subinventories": validation_summary(sheets[subinv_mapping["worksheet_name"]], subinv_mapping, strict_template=False),
        "IO_Parameters_Update": validation_summary(sheets[params_mapping["worksheet_name"]], params_mapping, strict_template=False),
    }
    vcols = st.columns(3)
    for i, (name, summary) in enumerate(summaries.items()):
        with vcols[i]:
            st.metric(name, "OK" if summary["is_valid"] else "Check", delta=f"{summary['total_rows']} rows")
            if summary["missing_columns"]:
                st.error(f"Missing: {summary['missing_columns']}")
            if summary["required_error_count"] or summary["payload_error_count"]:
                st.warning("Ada error required/payload")

    if any(not s["is_valid"] for s in summaries.values()):
        with st.expander("Detail validation errors"):
            for name, summary in summaries.items():
                if not summary["required_errors"].empty:
                    st.write(name, "required errors")
                    st.dataframe(summary["required_errors"], use_container_width=True)
                if not summary["payload_errors"].empty:
                    st.write(name, "payload errors")
                    st.dataframe(summary["payload_errors"], use_container_width=True)

    st.subheader("4. Run")
    st.caption("Dry Run aktif secara default. Matikan hanya kalau payload dan urutan sudah aman.")
    if st.button("Run Setup Flow", type="primary"):
        if not dry_run and (not base_url or not username or not password):
            st.error("Untuk live run, base URL, username, dan password wajib diisi.")
        else:
            client = None if dry_run else OracleFusionClient(base_url, username, password, timeout=timeout)
            cfg = RunnerConfig(dry_run=dry_run, upsert_io=upsert_io, stop_on_error=stop_on_error)
            org_map = {}
            all_logs = []

            # Try to preload org lookup for existing orgs when live and Step 1 disabled/skipped.
            if not dry_run and client is not None:
                try:
                    ref_df, _ = fetch_inventory_orgs(client, limit=300, expand_children=False)
                    org_map.update(organization_lookup_from_reference(ref_df))
                except Exception:
                    pass

            if run_step_io:
                io_log, created_map = run_create_io(sheets[io_mapping["worksheet_name"]], io_mapping, client, cfg)
                org_map.update(created_map)
                all_logs.append(io_log)
            if run_step_subinv:
                sub_log = run_create_subinventories(sheets[subinv_mapping["worksheet_name"]], subinv_mapping, client, cfg, org_map)
                all_logs.append(sub_log)
            if run_step_patch:
                patch_log = run_patch_io_parameters(sheets[params_mapping["worksheet_name"]], params_mapping, client, cfg, org_map)
                all_logs.append(patch_log)

            final_log = pd.concat(all_logs, ignore_index=True) if all_logs else pd.DataFrame()
            st.session_state["setup_log"] = final_log
            st.session_state["setup_org_map"] = org_map

log_df = st.session_state.get("setup_log")
if isinstance(log_df, pd.DataFrame) and not log_df.empty:
    st.subheader("Result Log")
    view = log_df.copy()
    for col in ["request_payload", "response_body"]:
        if col in view.columns:
            view[col] = view[col].apply(lambda v: json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v)
    st.dataframe(view, use_container_width=True)
    st.download_button(
        "Download Setup Log CSV",
        data=view.to_csv(index=False).encode("utf-8"),
        file_name="io_setup_log.csv",
        mime="text/csv",
    )
    st.download_button(
        "Download Setup Log JSON",
        data=make_json_bytes(log_df.to_dict(orient="records")),
        file_name="io_setup_log.json",
        mime="application/json",
    )
    st.caption(f"Organization map: {st.session_state.get('setup_org_map', {})}")
