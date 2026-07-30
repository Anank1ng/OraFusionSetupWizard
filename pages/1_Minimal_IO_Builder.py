import streamlit as st

from services.config_loader import load_schema, schema_title
from services.ui_helpers import field_selector, render_builder_downloads

st.set_page_config(page_title="Minimal IO Builder", page_icon="🏢", layout="wide")
st.title("🏢 Minimal IO Builder")
st.caption("Template khusus 12 field minimal untuk create Inventory Organization.")

mapping = load_schema("minimal_inventory_organizations")
st.info(schema_title(mapping))

st.markdown(
    """
Preset **Minimal** di page ini sengaja dikunci sebagai baseline create IO:
`OrganizationCode`, `OrganizationName`, BU/LE/Profit Center, status, location, inventory flag,
master org, item grouping, item definition code, dan `ScheduleId`.
"""
)

selected_columns = field_selector(mapping, preset="minimal", key_prefix="minimal_io")
render_builder_downloads(mapping, selected_columns, key_prefix="minimal_io")
