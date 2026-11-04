"""Full IO GET Reference for demo & safe copy-to-PATCH template generation.

GET only. This page never invokes POST or PATCH.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from services.config_loader import load_schema
from services.excel_service import make_json_bytes, make_template_excel_bytes
from services.full_io_get_service import (
    build_copy_patch,
    compare_parameters,
    compare_copyable_parameters,
    dataframe_book_bytes,
    fetch_full_io,
    fetch_organizations,
    first_parameter,
    json_bytes,
    snapshots_to_dataframes,
)
from services.oracle_client import OracleFusionClient
from services.payload_builder import build_payload_from_row
from services.ui_helpers import render_connection_form

st.set_page_config(page_title="Full IO GET Reference", page_icon="📚", layout="wide")
st.title("📚 Full IO GET Reference")
st.caption("Demo patch — Read-only reference & copy-to-PATCH template. Tidak mengubah data Oracle.")
st.info(
    "Ambil field IO yang sudah tersedia di instance sebagai contoh konfigurasi. "
    "Pilih field tertentu saja kalau ingin menyalin nilainya ke template PATCH."
)

BASE_MAPPING = load_schema("io_parameters_update")

st.subheader("1. Hubungkan ke Oracle Fusion")
base_url, username, password, timeout = render_connection_form("full_io_ref")

c1, c2 = st.columns([1, 2])
with c1:
    fetch_limit = st.number_input("Maksimum IO saat pencarian", min_value=1, max_value=500, value=100, step=10)
with c2:
    q_filter = st.text_input("Oracle q filter (opsional)", placeholder="OrganizationCode LIKE 'WH%'")

if st.button("🔎 Fetch IO List", type="primary", use_container_width=True):
    if not base_url or not username or not password:
        st.error("Isi Base URL, username, dan password Oracle dulu.")
    else:
        with st.spinner("Mengambil daftar IO dari Oracle..."):
            try:
                client = OracleFusionClient(base_url, username, password, timeout=int(timeout))
                rows = fetch_organizations(client, max_rows=int(fetch_limit), query=q_filter)
                st.session_state["full_io_list"] = rows
                st.session_state.pop("full_io_snapshots", None)
                st.session_state["full_io_base"] = base_url.rstrip("/")
                st.success(f"Berhasil mengambil {len(rows)} IO.")
            except Exception as exc:
                st.error(f"Gagal GET daftar IO: {exc}")

org_rows = st.session_state.get("full_io_list") or []
if org_rows:
    st.subheader("2. Pilih IO untuk mengambil konfigurasi lengkap")
    keyword = st.text_input("Cari berdasarkan kode / nama IO", placeholder="Ketik nama warehouse atau kode IO")
    filtered = [row for row in org_rows if keyword.casefold() in (
        str(row.get("OrganizationCode", "")) + " " + str(row.get("OrganizationName", ""))
    ).casefold()]
    listing_cols = ["OrganizationCode", "OrganizationName", "OrganizationId", "Status", "InventoryFlag"]
    st.dataframe(pd.DataFrame(filtered).reindex(columns=listing_cols), hide_index=True, use_container_width=True)

    # Use organization ID as stable option and retain labels without depending on list offsets.
    org_map = {}
    for row in filtered:
        if row.get("OrganizationId") is not None:
            org_map[str(row["OrganizationId"])] = row
    choices = st.multiselect(
        "IO yang mau diambil full detail (maksimal 50)",
        list(org_map),
        format_func=lambda org_id: (
            f"{org_map[org_id].get('OrganizationCode', '')} — "
            f"{org_map[org_id].get('OrganizationName', '')} ({org_id})"
        ),
        help="Setiap IO membutuhkan GET parent + dua child resources.",
    )
    if st.button("📥 GET Full Details", disabled=not choices, use_container_width=True):
        if len(choices) > 50:
            st.error("Maksimal 50 IO dalam satu batch.")
        elif not base_url or not username or not password:
            st.error("Koneksi Oracle belum diisi.")
        elif st.session_state.get("full_io_base") != base_url.rstrip("/"):
            st.error("Instance berubah. Fetch IO List ulang agar tidak mencampur instance berbeda.")
        else:
            client = OracleFusionClient(base_url, username, password, timeout=int(timeout))
            snapshots = []
            failures = []
            bar = st.progress(0, text="GET Full IO sedang diproses...")
            for idx, org_id in enumerate(choices, 1):
                try:
                    snapshots.append(fetch_full_io(client, int(org_id)))
                except Exception as exc:
                    failures.append(f"IO {org_id}: {exc}")
                bar.progress(idx / len(choices), text=f"GET {idx}/{len(choices)} IO")
            st.session_state["full_io_snapshots"] = snapshots
            st.session_state["full_io_errors"] = failures
            if failures:
                st.warning("Sebagian IO gagal: " + " | ".join(failures[:5]))
            if snapshots:
                st.success(f"Berhasil GET konfigurasi {len(snapshots)} IO.")

snapshots = st.session_state.get("full_io_snapshots") or []
if snapshots:
    names = {}
    for snap in snapshots:
        parent = snap.get("parent") or {}
        org_id = str(parent.get("OrganizationId"))
        names[org_id] = f"{parent.get('OrganizationCode','')} — {parent.get('OrganizationName','')}"
    by_id = {str(s["parent"].get("OrganizationId")): s for s in snapshots}
    st.divider()
    st.subheader("3. Preview Full IO & Download Reference")
    x1, x2, x3 = st.columns(3)
    x1.metric("IO berhasil diambil", len(snapshots))
    x2.metric("IO dengan invOrgParameters", sum(bool(first_parameter(s)) for s in snapshots))
    x3.metric("IO dengan plantParameters", sum(bool(s.get("plant_parameters")) for s in snapshots))

    reference_tables = snapshots_to_dataframes(snapshots)
    tabs = st.tabs(["Parent IO", "Inventory Parameters", "Plant Parameters", "Raw JSON"])
    with tabs[0]:
        st.dataframe(reference_tables["IO_Parent"], use_container_width=True, hide_index=True)
    with tabs[1]:
        st.dataframe(reference_tables["InvOrg_Parameters"], use_container_width=True, hide_index=True)
    with tabs[2]:
        st.dataframe(reference_tables["Plant_Parameters"], use_container_width=True, hide_index=True)
    with tabs[3]:
        selected_json_org = st.selectbox("Pilih IO untuk inspect JSON", list(names), format_func=lambda x: names[x])
        st.json(by_id[selected_json_org])
    for warning in reference_tables["Fetch_Warnings"].get("Warning", pd.Series(dtype=str)).dropna().tolist()[:5]:
        st.warning(warning)

    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "Download Full IO Reference Excel",
            data=dataframe_book_bytes(reference_tables),
            file_name="full_io_reference.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Download Full IO Raw JSON",
            data=json_bytes(snapshots),
            file_name="full_io_reference.json",
            mime="application/json",
            use_container_width=True,
        )

    st.divider()
    st.subheader("4. Bandingkan nilai dan siapkan template PATCH (opsional)")
    st.caption(
        "Pilih IO referensi dan IO target. Hanya field invOrgParameters yang "
        "tercantum dalam mapping PATCH akan tersedia untuk disalin."
    )
    if len(by_id) < 2:
        st.info("Fetch minimal dua IO untuk membandingkan konfigurasi dan menyalin ke IO target.")
    else:
        a, b = st.columns(2)
        with a:
            source_id = st.selectbox("Reference IO (contoh konfigurasi)", list(names), format_func=lambda x: names[x], key="full_source")
        with b:
            target_ids = [k for k in names if k != source_id]
            target_id = st.selectbox("Target IO (yang ingin di-PATCH)", target_ids, format_func=lambda x: names[x], key="full_target")
        source = by_id[source_id]
        target = by_id[target_id]

        # Keep choices tied to the actual IO pair (and fetched snapshot batch).
        # If the user changes either IO, prior selections cannot leak into the new PATCH.
        selection_pair = (source_id, target_id, id(snapshots))
        if st.session_state.get("full_copy_pair") != selection_pair:
            st.session_state["full_copy_pair"] = selection_pair
            st.session_state["full_copy_selected"] = []
            st.session_state["full_copy_editor_version"] = st.session_state.get("full_copy_editor_version", 0) + 1

        st.markdown("#### Compare & Copy IO Parameters")
        st.caption(
            "Centang **Copy** pada parameter yang ingin disalin dari Reference IO ke Target IO. "
            "Tabel hanya memuat field invOrgParameters yang ada dalam mapping PATCH dan "
            "mempunyai nilai referensi. False dan 0 tetap dianggap nilai yang valid."
        )

        compare_df = compare_copyable_parameters(source, target, BASE_MAPPING)
        if compare_df.empty:
            st.info("Tidak ada parameter referensi yang dapat dicopy dengan mapping PATCH saat ini.")
        else:
            different_count = int((compare_df["Status"] == "Different").sum())
            metric1, metric2, metric3 = st.columns(3)
            metric1.metric("Parameter tersedia", len(compare_df))
            metric2.metric("Nilai berbeda", different_count)
            metric3.metric("Dipilih untuk Copy", len(st.session_state["full_copy_selected"]))

            only_diff = st.toggle("Different only — tampilkan parameter yang berbeda", value=True)
            actions1, actions2 = st.columns(2)
            if actions1.button("Select All Differences", use_container_width=True):
                st.session_state["full_copy_selected"] = compare_df.loc[
                    compare_df["Status"] == "Different", "Field"
                ].tolist()
                st.session_state["full_copy_editor_version"] += 1
                st.rerun()
            if actions2.button("Clear Selection", use_container_width=True):
                st.session_state["full_copy_selected"] = []
                st.session_state["full_copy_editor_version"] += 1
                st.rerun()

            visible = (
                compare_df.loc[compare_df["Status"] == "Different"].copy()
                if only_diff else compare_df.copy()
            )
            selected_set = set(st.session_state["full_copy_selected"])
            visible.insert(0, "Copy", visible["Field"].isin(selected_set))
            # Filter toggles do not discard selected fields that are temporarily hidden.
            edited = st.data_editor(
                visible,
                hide_index=True,
                use_container_width=True,
                disabled=["Field", "Parameter", "Section", "Reference Value", "Target Value", "Status"],
                column_config={
                    "Copy": st.column_config.CheckboxColumn(
                        "Copy", help="Centang untuk menyalin parameter ke target IO."
                    ),
                    "Field": st.column_config.TextColumn("REST Field"),
                    "Parameter": st.column_config.TextColumn("Parameter"),
                    "Section": st.column_config.TextColumn("Section"),
                    "Reference Value": st.column_config.TextColumn("Reference (After)"),
                    "Target Value": st.column_config.TextColumn("Target (Before)"),
                    "Status": st.column_config.TextColumn("Status"),
                },
                key=(
                    f"full_copy_editor_{source_id}_{target_id}_"
                    f"{'diff' if only_diff else 'all'}_"
                    f"{st.session_state['full_copy_editor_version']}"
                ),
            )
            visible_fields = set(visible["Field"])
            previously_hidden = selected_set - visible_fields
            selected_fields = (
                previously_hidden | set(edited.loc[edited["Copy"], "Field"].tolist())
            )
            # Preserve mapping order for deterministic Excel/JSON export.
            ordered = [
                field for field in compare_df["Field"].tolist() if field in selected_fields
            ]
            st.session_state["full_copy_selected"] = ordered
            st.caption(
                f"{len(ordered)} parameter dipilih. "
                "Filter tidak membatalkan pilihan yang sedang tersembunyi."
            )

            if ordered:
                try:
                    template_df, mapping = build_copy_patch(source, target, BASE_MAPPING, ordered)
                    st.markdown("#### PATCH Preview — Before → After")
                    selection_rows = compare_df.loc[compare_df["Field"].isin(ordered)].copy()
                    st.dataframe(
                        selection_rows[["Parameter", "Field", "Target Value", "Reference Value", "Status"]].rename(
                            columns={
                                "Target Value": "Before (Target)",
                                "Reference Value": "After (Reference)",
                            }
                        ),
                        hide_index=True,
                        use_container_width=True,
                    )
                    st.warning(
                        "Pastikan parameter cocok untuk IO target. Nilai existing dari IO lain "
                        "tidak otomatis menjamin PATCH diterima Oracle. Download dan periksa "
                        "file sebelum melakukan live PATCH di Setup Runner."
                    )
                    payload = build_payload_from_row(template_df.iloc[0], mapping)
                    with st.expander("Preview PATCH payload", expanded=False):
                        st.json(payload)
                        st.caption("Route menuju Target IO; payload berisi field yang dicentang saja.")
                    p1, p2 = st.columns(2)
                    with p1:
                        st.download_button(
                            "Download PATCH Excel Template",
                            data=make_template_excel_bytes(
                                mapping, template_df=template_df, sheet_name="IO_Parameters_Update"
                            ),
                            file_name="io_parameters_patch_from_reference.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                        )
                    with p2:
                        st.download_button(
                            "Download PATCH Mapping JSON",
                            data=make_json_bytes(mapping),
                            file_name="io_parameters_patch_from_reference_mapping.json",
                            mime="application/json",
                            use_container_width=True,
                        )
                except Exception as exc:
                    st.error(f"Template PATCH belum bisa dibuat: {exc}")
            else:
                st.info(
                    "Belum ada parameter dicentang. Pilih Copy pada baris yang diinginkan, "
                    "atau gunakan Select All Differences untuk memilih seluruh perbedaan."
                )

        with st.expander("Advanced: lihat semua field hasil GET (termasuk yang tidak dapat di-PATCH)"):
            st.dataframe(compare_parameters(source, target), hide_index=True, use_container_width=True)

st.caption("Read-only GET pada page ini. Tidak ada POST/PATCH atau penyimpanan password ke file.")
