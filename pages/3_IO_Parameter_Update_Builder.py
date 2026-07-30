import streamlit as st

from services.config_loader import load_schema, schema_title
from services.ui_helpers import field_selector, render_builder_downloads

st.set_page_config(page_title="IO Parameter Update Builder", page_icon="🛠️", layout="wide")
st.title("🛠️ IO Parameter Update Builder")
st.caption("Template untuk PATCH inventory organization parameters setelah IO dan subinventory siap.")

mapping = load_schema("io_parameters_update")
st.info(schema_title(mapping))

st.warning(
    "Untuk PATCH, app butuh OrganizationId dan OrganizationId2. Kalau OrganizationId2 kosong, runner akan GET child invOrgParameters dulu untuk mengambil key-nya."
)

selected_columns = field_selector(mapping, preset="standard", key_prefix="params")
render_builder_downloads(mapping, selected_columns, key_prefix="params")
