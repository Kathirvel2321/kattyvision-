"""Checks that the app never *looks* broken while it starts.

The problem being guarded against is not a crash - it is silence.  Building the
window takes about two seconds (measured), and for all of it the process is
busy, so the user watches a blank unresponsive rectangle that Windows may label
"(Not Responding)".  These checks are therefore about perception, and about the
plumbing that keeps the main thread free:

1. **A card appears quickly**, before the expensive part of the build, and it is
   a real visible window rather than an object that was built and never shown.
2. **It animates** - the bar fills as steps are reported and the highlight
   keeps sweeping even when the progress does not move, which is what stops a
   stalled bar from reading as "frozen".
3. **It closes cleanly**, leaves no window behind, and tolerates a second call.
4. **It can always fail safely** - `make_splash` returns an object with the same
   interface even when drawing raises, so a cosmetic screen can never stop the
   app from opening.
5. **The workspace build never blocks on the OCR check**: `diagnose()` runs on a
   worker thread and its result arrives through the event queue.
6. **A remembered window geometry is clamped** and a maximised one is refused,
   so the window cannot reopen off the edge of the screen.

Usage:  python tools/splash_tests.py
"""
from __future__ import annotations

import pathlib
import sys
import time
import tkinter as tk

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import court_ocr.app as app_module                          # noqa: E402
import court_ocr.splash as splash_module                    # noqa: E402
import court_ocr.ui_kit as ui_kit                           # noqa: E402
from court_ocr.splash import _NullSplash, make_splash       # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail if not ok else ''}")
    if not ok:
        FAILED.append(name)


class _Exploding:
    """Stands in for a Splash that cannot be drawn (no display, bad canvas...)."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("no screen today")


print("=" * 74)
print(" KattyVision start-up / loading card checks")
print("=" * 74)

# The card sizes itself with ui_kit.px(), so the scale has to be set the same way
# main() sets it or these numbers would not mean anything.
app_module.UI_SCALE = app_module.enable_dpi_awareness()

# --------------------------------------------------------------- 1. appearing
print("\n1. The loading card appears, and appears early")
root = tk.Tk()
root.withdraw()

started = time.perf_counter()
splash = make_splash(root)
appeared = time.perf_counter() - started
root.update_idletasks()

check("a splash was created", splash.ok, type(splash).__name__)
check(f"it took {appeared * 1000:.0f} ms to build (the build itself is ~2000 ms)",
      appeared < 0.5, f"{appeared:.3f}s")
check("its window exists", bool(splash.window.winfo_exists()))
check("its window is mapped (actually on screen)",
      bool(splash.window.winfo_ismapped()))
check("it does not take a taskbar button of its own",
      bool(splash.window.overrideredirect()))
check("it is not left transparent", splash._alpha > 0.5, f"alpha={splash._alpha}")

# ------------------------------------------------------------------ 2. moving
print("\n2. It keeps moving while the caller is busy")


def bar_width(splash) -> float:
    """How wide the fill actually is.

    A rounded rectangle is one polygon, so its first point is always
    ``left + radius`` no matter how full the bar is; the rightmost x is the
    number that moves.
    """
    return max(splash.canvas.coords(splash.fill_id)[0::2])


splash.step(0.20, "Building the workspace...")
for _ in range(6):
    time.sleep(splash.MIN_FRAME)
    splash.pump()
start_bar = bar_width(splash)
splash.step(0.85, "Almost ready...")
for _ in range(8):
    time.sleep(splash.MIN_FRAME)
    splash.pump()
end_bar = bar_width(splash)
check("the bar filled as steps were reported", end_bar > start_bar + 10,
      f"{start_bar:.0f} -> {end_bar:.0f}")
check("the status text follows the message",
      splash.canvas.itemcget("message", "text") == "Almost ready...",
      splash.canvas.itemcget("message", "text"))

# The sweep is what keeps a stalled bar from looking dead, so it has to move even
# while the reported progress does not change at all.
positions = []
for _ in range(8):
    time.sleep(splash.MIN_FRAME)
    splash.pump()
    positions.append(round(splash.canvas.coords(splash.sheen_id)[0], 1))
check("the highlight keeps sweeping while progress is frozen",
      len(set(positions)) > 1, f"positions={sorted(set(positions))}")

# ------------------------------------------------------------------ 3. closing
print("\n3. It closes cleanly")
splash.close()
check("the card window is gone", not splash.window.winfo_exists())
check("it reports itself closed", splash._closed)
try:
    splash.close()          # the hand-over path can reach close() twice
    twice_ok = True
except Exception as exc:                                  # noqa: BLE001
    twice_ok = False
    print(f"        second close() raised {exc!r}")
check("closing twice is harmless", twice_ok)

# ------------------------------------------------------------ 4. safe failure
print("\n4. A splash failure never stops the app")
stub = _NullSplash()
try:
    stub.step(0.5, "x")
    stub.pump()
    stub.close()
    stub_usable = True
except Exception as exc:                                  # noqa: BLE001
    stub_usable = False
    print(f"        fallback raised {exc!r}")
check("the fallback answers step/pump/close without raising", stub_usable)
check("the fallback is not mistaken for a real card", not stub.ok)

working = splash_module.Splash
splash_module.Splash = _Exploding
try:
    rescued = make_splash(root)
    rescued_ok = isinstance(rescued, _NullSplash)
finally:
    splash_module.Splash = working
check("make_splash swallows a card that cannot be drawn", rescued_ok)

# --------------------------------------------------------------- 5. no freeze
print("\n5. The OCR check no longer runs on the UI thread")
source = (pathlib.Path(splash_module.__file__).with_name("app.py")
          .read_text(encoding="utf-8"))
check("start-up calls the background check",
      "_check_tools_async(" in source)
check("start-up does NOT call the blocking check",
      "self._report_tools()" not in source,
      "a blocking self._report_tools() is still called from __init__")
check("the tool check runs on a worker thread",
      "threading.Thread" in source and 'name="toolcheck"' in source)
check("the result is applied on the UI thread through the event queue",
      'kind == "tools"' in source and "def _process_events" in source)
check("the main window is hidden while it is being built",
      "def _hide_main_window" in source and "def _show_main_window" in source)
check("ui_kit is told the real display scale",
      "set_scale(UI_SCALE)" in source)

app = None
try:
    app = app_module.App("work/_splash_cfg.json")
    check("the app window is visible once start-up finishes",
          app.winfo_viewable(), f"state={app.state()!r}")
    check("the app window can hold a taskbar button",
          app.state() == "normal", app.state())
    check("the window title is the brand, not a working name",
          app.title() == "KattyVision", app.title())
    check("the tool check had already finished and reported",
          app.tool_worker is not None and not app.tool_worker.is_alive(),
          "the tool worker is still running after start-up")
    # This is the check that used to fail everywhere: ui_kit.SCALE was
    # documented as being set from main() and never was, so on a 150 % display
    # every padding stayed at 1x while the fonts and the window were 1.5x.
    check("ui_kit picked up the real display scale",
          ui_kit.SCALE == app_module.UI_SCALE,
          f"SCALE={ui_kit.SCALE} UI_SCALE={app_module.UI_SCALE}")
    check("so px() scales the padding with the monitor too",
          ui_kit.px(22) == max(1, int(round(22 * app_module.UI_SCALE))),
          f"px(22)={ui_kit.px(22)} at scale {app_module.UI_SCALE}")
except Exception as exc:                                          # noqa: BLE001
    check("the app starts with the card in place", False, repr(exc))

# ---------------------------------------------------------------- 6. geometry
print("\n6. A remembered window size cannot reopen off-screen")
if app is not None:
    screen_w, screen_h = app.winfo_screenwidth(), app.winfo_screenheight()

    # The exact value this machine had saved: a maximised window, offset to the
    # right.  It used to be restored verbatim on every launch.
    app.cfg.set("ui.geometry", "1920x974+322+35")
    app.geometry("1x1+0+0")
    refused = not app._restore_geometry()
    check("a maximised-looking size is refused, not restored", refused)
    check("so the window keeps its fitted size instead",
          app.winfo_width() <= screen_w and app.winfo_height() <= screen_h,
          f"{app.winfo_width()}x{app.winfo_height()} on {screen_w}x{screen_h}")

    app.cfg.set("ui.geometry", "900x600+100+80")
    applied = app._restore_geometry()
    check("an ordinary remembered size IS restored", applied)
    check("and it is clamped to the screen",
          app.winfo_x() >= 0 and app.winfo_y() >= 0
          and app.winfo_x() + app.winfo_width() <= screen_w
          and app.winfo_y() + app.winfo_height() <= screen_h,
          f"{app.winfo_width()}x{app.winfo_height()}"
          f"+{app.winfo_x()}+{app.winfo_y()} on {screen_w}x{screen_h}")

    # A size remembered on a monitor that is no longer attached.
    app.cfg.set("ui.geometry", "1600x1200+5000+4000")
    app._restore_geometry()
    check("a size remembered on a now-detached screen is pulled back on-screen",
          app.winfo_x() < screen_w and app.winfo_y() < screen_h,
          f"+{app.winfo_x()}+{app.winfo_y()} on {screen_w}x{screen_h}")

    app.cfg.set("ui.geometry", "not a geometry")
    check("junk in the config is ignored", not app._restore_geometry())

    app.destroy()
root.destroy()

print("\n" + "=" * 74)
if FAILED:
    print(f" RESULT: {len(FAILED)} check(s) FAILED: {', '.join(FAILED)}")
    print("=" * 74)
    raise SystemExit(1)
print(" RESULT: all loading-card checks passed - start-up never looks frozen.")
print("=" * 74)
raise SystemExit(0)

