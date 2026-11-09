"""The Setup Runner no longer duplicates template downloads at page top."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pages" / "5_Setup_Runner.py"


def test_setup_runner_hides_unused_builtin_template_section():
    source = PAGE.read_text(encoding="utf-8")
    for old_label in (
        "Download Full Setup Workbook",
        "Template Minimal IO",
        "Template Subinventories",
        "Template IO Parameters",
        '1. Download template bawaan',
    ):
        assert old_label not in source

    assert 'st.subheader("1. Pilih proses yang mau dijalankan")' in source
    assert 'st.subheader("2. Mapping & input file")' in source
    assert 'st.subheader("3. Connection & run options")' in source
    assert "Download mapping JSON aktif" in source
    assert "Download template dari mapping aktif" in source
    assert "run_create_io(" in source


def test_setup_runner_still_loads_in_streamlit():
    app = AppTest.from_file(str(PAGE), default_timeout=30).run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any(title.value == "🚀 Setup Runner" for title in app.title)
