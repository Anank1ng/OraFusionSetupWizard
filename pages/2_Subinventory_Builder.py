import streamlit as st

from services.config_loader import load_schema, schema_title
from services.ui_helpers import field_selector, render_builder_downloads

st.set_page_config(page_title="Subinventory Builder", page_icon="📦", layout="wide")
st.title("📦 Subinventory Builder")
st.caption("Buat template create Subinventory setelah IO sudah terbentuk.")

mapping = load_schema("subinventories")
st.info(schema_title(mapping))

st.markdown(
    """
Gunakan `OrganizationCode` sebagai kunci. Saat runner jalan, app akan mencoba resolve ke `OrganizationId`
dari hasil create IO atau dari reference existing IO. Kalau sudah punya `OrganizationId`, boleh isi langsung.
"""
)

selected_columns = field_selector(mapping, preset="minimal", key_prefix="subinv")
render_builder_downloads(mapping, selected_columns, key_prefix="subinv")
