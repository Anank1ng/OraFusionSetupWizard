"""Minimal IO Builder shows only the relevant preset button."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_minimal_builder_hides_standard_and_all_presets():
    page = ROOT / "pages" / "1_Minimal_IO_Builder.py"
    app = AppTest.from_file(str(page), default_timeout=30).run()

    assert not app.exception, [str(e.message) for e in app.exception]
    labels = [button.label for button in app.button]
    assert "Use Minimal" in labels
    assert "Use Standard" not in labels
    assert "Use All" not in labels
    assert any(metric.label == "Selected fields" and str(metric.value) == "12" for metric in app.metric)
