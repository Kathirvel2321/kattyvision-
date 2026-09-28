"""Run with python tools/brand_controls_check.py (requires a desktop)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from court_ocr.app import App
from court_ocr.ui_kit import Stepper


app = App(Path("work/_brand_controls_config.json"))
try:
    app.update()
    values = []
    number = Stepper(app, minimum=1, maximum=5000, value=50, command=values.append)
    number.variable.set("124")
    assert number.get() == 124 and values == [124]
    for invalid in ("", "hello", "nan", "inf"):
        number.variable.set(invalid)
        assert number.get() == 124
    number.variable.set("9000")
    assert number.get() == 5000
    number.set_range(1, 10)
    assert number.get() == 10
    app.step_pages.variable.set("42")
    app.step_mb.variable.set("4.5")
    app._sync_limits_to_config()
    assert app.cfg.get("split.max_pages_per_file") == 42
    assert app.cfg.get("compress.target_size_mb") == 4.5
    app.step_pages.variable.set("77")
    app.step_pages.value_label.focus_force()
    app.update()
    app.btn_run.event_generate("<Button-1>")
    app.update()
    assert app.cfg.get("split.max_pages_per_file") == 77
    assert app.logo_image.width() >= 250
    assert app.logo_image.height() <= 50
    for index in range(4):
        app.book.select(index)
        app.update()
        assert app.hero_badge.winfo_ismapped()
    print("PASS: typed values, invalid input, bounds, click commit, config sync, logo and shared header")
finally:
    app.destroy()
