"""Navigation regression test: demo only shows minimal IO workflow pages."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_homepage_and_visible_navigation_are_loadable():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception, [str(exc.message) for exc in app.exception]
    assert any("Oracle Fusion IO Setup Builder" in title.value for title in app.title)

    for path in [
        "pages/1_Minimal_IO_Builder.py",
        "pages/4_Reference_Center.py",
        "pages/5_Setup_Runner.py",
    ]:
        app.switch_page(path).run()
        assert not app.exception, [str(exc.message) for exc in app.exception]


def test_sidebar_excludes_three_inactive_pages_without_deleting_code():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'st.Page("pages/1_Minimal_IO_Builder.py"' in source
    assert 'st.Page("pages/4_Reference_Center.py"' in source
    assert 'st.Page("pages/5_Setup_Runner.py"' in source

    for filename in [
        "2_Subinventory_Builder.py",
        "3_IO_Parameter_Update_Builder.py",
        "6_Full_IO_GET_Reference.py",
    ]:
        assert (ROOT / "pages" / filename).exists()
        assert f'st.Page("pages/{filename}"' not in source
