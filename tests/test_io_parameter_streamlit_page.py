"""Smoke-test the actual Streamlit IO Parameter Update Builder page.

Earlier regression tests only checked service helpers; they did not import or
execute the Streamlit page itself, allowing deployment-time import errors to
escape detection.
"""
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_io_parameter_page_loads_without_import_error():
    page = ROOT / "pages" / "3_IO_Parameter_Update_Builder.py"
    app = AppTest.from_file(str(page), default_timeout=30).run()

    assert not app.exception, [str(item.message) for item in app.exception]
    assert any("IO Parameter Update Builder" in title.value for title in app.title)
