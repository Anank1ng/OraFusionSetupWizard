import json

import pandas as pd
import streamlit as st

from services.excel_service import make_json_bytes, make_reference_excel_bytes
from services.oracle_client import OracleFusionClient
from services.reference_service import REFERENCE_COLLECTIONS, fetch_inventory_orgs, fetch_reference_collection
from services.ui_helpers import render_connection_form

st.set_page_config(page_title="Reference Center", page_icon="🔎", layout="wide")
st.title("🔎 Reference Center")
st.caption("Ambil ID referensi dari Oracle Fusion untuk isi template upload.")

base_url, username, password, timeout = render_connection_form("ref")
limit = st.number_input("Limit data per reference", min_value=1, max_value=500, value=60)
q_filter = st.text_input("Optional q filter", placeholder="Contoh: Name LIKE 'Vision%'", help="Kosongkan kalau belum yakin field filter didukung semua endpoint.")

st.subheader("Reference LOV")
selected_names = []
cols = st.columns(2)
for idx, (name, cfg) in enumerate(REFERENCE_COLLECTIONS.items()):
    with cols[idx % 2]:
        checked = st.checkbox(name, value=name in ["Business Units", "Profit Center Business Units", "Legal Entities", "Inventory Organizations LOV", "Schedules", "Locations LOV", "Material Statuses"], key=f"ref_{name}")
        st.caption(f"{cfg['endpoint']} — {cfg.get('description','')}")
        if checked:
            selected_names.append(name)

fetch_io = st.checkbox("Fetch existing Inventory Organizations with child params", value=True)

if st.button("Fetch Selected References", type="primary"):
    if not base_url or not username or not password:
        st.error("Isi base URL, username, dan password dulu.")
    else:
        client = OracleFusionClient(base_url, username, password, timeout=timeout)
        results = {}
        raw = {}
        for name in selected_names:
            try:
                df, resp = fetch_reference_collection(client, REFERENCE_COLLECTIONS[name], limit=int(limit), q_filter=q_filter)
                results[name] = df
                raw[name] = {"ok": resp.ok, "status_code": resp.status_code, "url": resp.url, "body": resp.body}
            except Exception as exc:
                results[name] = pd.DataFrame([{"error": str(exc)}])
                raw[name] = {"error": str(exc)}
        if fetch_io:
            try:
                df, resp = fetch_inventory_orgs(client, limit=int(limit), q_filter="", expand_children=True)
                results["Existing Inventory Organizations"] = df
                raw["Existing Inventory Organizations"] = {"ok": resp.ok, "status_code": resp.status_code, "url": resp.url, "body": resp.body}
            except Exception as exc:
                results["Existing Inventory Organizations"] = pd.DataFrame([{"error": str(exc)}])
                raw["Existing Inventory Organizations"] = {"error": str(exc)}
        st.session_state["reference_results"] = results
        st.session_state["reference_raw"] = raw

results = st.session_state.get("reference_results", {})
raw = st.session_state.get("reference_raw", {})
if results:
    st.success(f"Fetched {len(results)} reference collection(s).")
    for name, df in results.items():
        with st.expander(f"{name} — {len(df)} rows", expanded=False):
            st.dataframe(df, use_container_width=True)
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "Download Reference Excel",
            data=make_reference_excel_bytes(results),
            file_name="reference_ids.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with c2:
        st.download_button(
            "Download Raw GET JSON",
            data=make_json_bytes(raw),
            file_name="reference_raw_get.json",
            mime="application/json",
        )
