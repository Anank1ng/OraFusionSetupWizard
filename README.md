# Oracle Fusion IO Setup Builder

Streamlit app untuk membantu setup Inventory Organization Oracle Fusion secara bertahap:

1. Create Minimal Inventory Organization
2. Create Subinventories setelah OrganizationId tersedia
3. Patch Inventory Organization Parameters setelah subinventory dibuat

## Endpoint utama

- Minimal IO: `POST /fscmRestApi/resources/11.13.18.05/inventoryOrganizations`
- Subinventory: `POST /fscmRestApi/resources/11.13.18.05/subinventories`
- IO Parameters: `PATCH /fscmRestApi/resources/11.13.18.05/inventoryOrganizations/{OrganizationId}/child/invOrgParameters/{OrganizationId2}`

## Cara menjalankan lokal

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Mac/Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Cara testing aman

1. Jalankan app.
2. Buka `Setup Runner`.
3. Download `Full Setup Workbook`.
4. Isi sheet minimal.
5. Upload workbook lagi di `Setup Runner`.
6. Pastikan `Dry Run Mode` aktif.
7. Klik `Run Setup Flow`.
8. Cek payload dan log.
9. Baru matikan `Dry Run Mode` kalau payload aman.

## Struktur penting

```text
app.py
pages/
  1_Minimal_IO_Builder.py
  2_Subinventory_Builder.py
  3_IO_Parameter_Update_Builder.py
  4_Reference_Center.py
  5_Setup_Runner.py
services/
  oracle_client.py
  payload_builder.py
  excel_service.py
  validation_service.py
  reference_service.py
  setup_runner.py
schemas/
  minimal_inventory_organizations.json
  subinventories.json
  io_parameters_update.json
```

## Catatan penting

- App ini default **Dry Run**, jadi tidak akan upload ke Oracle sampai mode itu dimatikan.
- `OrganizationCode` dipakai sebagai kunci antar-step.
- `OrganizationId` diambil dari response create IO atau dari Reference Center.
- Untuk PATCH IO Parameters, app akan mencoba GET child `invOrgParameters` untuk mendapatkan `OrganizationId2` jika kolom tersebut kosong.
