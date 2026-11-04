PATCH DEMO — Full IO GET Reference (read-only)
=========================================
Repository: https://github.com/Anank1ng/OraFusionSetupWizard

Tambahkan (tidak perlu replace file lain):
  pages/6_Full_IO_GET_Reference.py
  services/full_io_get_service.py
  tests/test_full_io_get_service.py     (opsional untuk unit test)

Commit perubahan ke branch yang dipakai Streamlit; reboot bila cache navigasi belum refresh.

Akses page:
  Streamlit sidebar -> Full IO GET Reference

Alur:
  1. Masukkan URL instance/username/password.
  2. Fetch IO List (offset pagination, max 500).
  3. Select sampai 50 IO, klik GET Full Details.
  4. Tab Parent/Inventory Parameters/Plant Parameters, download Excel/JSON.
  5. Opsional: pilih source/reference IO dan target IO, pilih field PATCH yang ingin dicopy.
  6. Download PATCH Excel + Mapping JSON untuk mode Patch IO Parameters di Setup Runner.

Keamanan:
  - Halaman ini cuma melakukan GET, tidak melakukan POST/PATCH.
  - Field display-only parent tidak masuk ke template PATCH.
  - OrganizationId2 tidak ditebak. Jika tidak ditemukan dari GET, nilainya kosong;
    runner saat ini memiliki lookup key child saat live run.
  - Jangan menyebar hasil export secara publik jika mengandung data internal perusahaan.

Catatan:
  - Kode dibuat berdasarkan file service/schema pada repo publik saat review.
  - Testing unit lokal menggunakan dummy client, bukan live instance Oracle.
  - Excel/JSON full GET adalah referensi; jangan di-upload ke Setup Runner.
  - Patch Excel/Mapping yang khusus dibuat dari field terpilihlah yang diupload.
