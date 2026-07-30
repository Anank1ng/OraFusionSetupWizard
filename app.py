import streamlit as st

st.set_page_config(
    page_title="Oracle Fusion IO Setup Builder",
    page_icon="🏗️",
    layout="wide",
)

st.title("🏗️ Oracle Fusion IO Setup Builder")
st.caption("Create Minimal IO → Create Subinventories → Update IO Parameters")

st.markdown(
    """
Aplikasi yang digunakan untuk melakukan pembuatan Inventory Organization di oracle fusion

### Flow utama
1. **Minimal IO Builder** — buat template 12 field minimal untuk create Inventory Organization.
2. **Subinventory Builder** — buat template subinventory setelah IO terbentuk.
3. **IO Parameter Update Builder** — buat template PATCH parameter IO setelah subinventory tersedia.
4. **Reference Center** — ambil ID referensi dari Fusion: BU, Legal Entity, IO LOV, Schedule, Location, Material Status.
5. **Setup Runner** — jalankan proses step-by-step, dengan Dry Run mode default aktif.

### Prinsip aman
- Jangan langsung kirim field optional terlalu banyak saat create IO.
- `OrganizationCode` dipakai sebagai kunci antar-sheet.
- `OrganizationId` bisa diambil dari response create IO atau dari Reference Center.
- Subinventory dan update parameter dijalankan setelah IO sudah punya `OrganizationId`.
"""
)

c1, c2, c3 = st.columns(3)
with c1:
    st.info("Step 1\n\nCreate Minimal Inventory Organization")
with c2:
    st.info("Step 2\n\nCreate Subinventories by OrganizationId")
with c3:
    st.info("Step 3\n\nPatch Inventory Organization Parameters")

st.divider()
st.subheader("Cara testing awal")
st.markdown(
    """
1. Buka **Minimal IO Builder**, download template dan isi 1 row.
2. Buka **Setup Runner**, upload workbook setup atau template per step.
3. Pastikan **Dry Run Mode** aktif dulu.
4. Preview payload dan log.
5. Baru matikan Dry Run kalau payload sudah aman.
"""
)
