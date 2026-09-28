"""Self test - proves the app works on this PC, without opening a window.

It drives the real desktop-app code path (presets, step tracking, pipeline) with
the dialogs muted, then checks the finished PDF. Use it for support:

    python tools/selftest.py

Exit code 0 = everything passed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf                                    # noqa: E402

from court_ocr import app as app_module           # noqa: E402
from court_ocr import pdf_ops                     # noqa: E402
from court_ocr.app import App                     # noqa: E402
from court_ocr.toolcheck import diagnose          # noqa: E402
from court_ocr.util import MB                     # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, message: str) -> bool:
    RESULTS.append((bool(ok), message))
    print(f"  [{'PASS' if ok else 'FAIL'}] {message}")
    return bool(ok)


def main() -> int:
    print("=" * 74)
    print(" Court Filing Automation - self test")
    print("=" * 74)

    print("\n1. Tools")
    tools = diagnose()
    tesseract = tools["tesseract"]
    check(tesseract.available, f"OCR engine (Tesseract): {tesseract.status}")
    print(f"  [info] compressed strongly? Ghostscript: {tools['ghostscript'].status}")
    print(f"  [info] two Python packages: "
          f"pymupdf {pymupdf.__version__}, pillow "
          f"{__import__('PIL').__version__}")

    print("\n2. Practice documents")
    sample_dir = ROOT / "input" / "SAMPLE CASE"
    if not sample_dir.is_dir() or not list(sample_dir.glob("*.pdf")):
        import subprocess

        subprocess.run([sys.executable, str(ROOT / "tools" / "make_sample_pdf.py")],
                       cwd=str(ROOT), check=False)
    files = pdf_ops.collect_inputs(sample_dir, [".pdf", ".png", ".jpg"])
    check(len(files) >= 3, f"found {len(files)} sample document(s) in input/SAMPLE CASE")

    print("\n3. Running the app code path (window hidden, dialogs muted)")
    # mute the pop-ups so the test can run unattended
    class _Silent:
        @staticmethod
        def showinfo(*_a, **_k):
            return "ok"

        @staticmethod
        def showwarning(*_a, **_k):
            return "ok"

        @staticmethod
        def showerror(*_a, **_k):
            return "ok"

        @staticmethod
        def askyesno(*_a, **_k):
            return False

    app_module.messagebox = _Silent
    app_module.open_in_explorer = lambda *_a, **_k: None

    started = time.time()
    # use a throw-away config so the real config.json is never modified
    temp_config = ROOT / "work" / "_selftest_config.json"
    if temp_config.exists():
        temp_config.unlink()
    application = App(temp_config)
    application.withdraw()                        # keep it off screen
    application.var_input.set(str(sample_dir))
    application.var_output.set(str(ROOT / "output"))
    application.var_case.set("SELFTEST")
    application.var_preset.set("standard")
    application._on_preset(apply=True, persist=False)
    application._start()

    deadline = time.time() + 120
    while application.report is None and time.time() < deadline:
        application.update()
        time.sleep(0.15)
    application.update()
    elapsed = time.time() - started

    report = application.report
    if not check(report is not None, "the run finished"):
        print(f"      worker thread alive : "
              f"{application.worker.is_alive() if application.worker else None}")
        print(f"      step states         : {application.step_state}")
        print("      --- the app's log box ---")
        try:
            lines = application.log_box.get("1.0", "end").strip().splitlines()
        except Exception as exc:
            lines = [f"(could not read the log box: {exc})"]
        for line in lines[-25:]:
            print(f"      {line}")
        application.destroy()
        return 1

    check(report.ok, f"all steps reported OK ({len(report.steps)} steps)")
    check(elapsed < 240, f"finished in {elapsed:.1f} seconds")
    for step in report.steps:
        state = application.step_state.get(step.step, "?")
        print(f"      {step.step:<11} {state:<8} "
              f"{step.before_mb:.2f} -> {step.after_mb:.2f} MB  {step.detail[:52]}")

    print("\n4. Checking the produced PDF")
    outputs = report.outputs
    ok = check(bool(outputs), f"{len(outputs)} output file(s) produced")
    if ok:
        target = outputs[0]
        doc = pymupdf.open(target)
        pages = doc.page_count
        text_all = "".join(doc[i].get_text() for i in range(pages))
        numbered = sum(1 for i in range(pages) if "Page" in doc[i].get_text()
                       and " of " in doc[i].get_text())
        stamped = sum(1 for i in range(pages) if doc[i].get_images(full=True))
        scanned_hits = sum(len(doc[i].search_for("POSSESSION")) for i in range(pages))
        check(pages >= 7, f"page count is {pages}")
        check(numbered >= pages, f"'Page X of Y' present on {numbered}/{pages} pages")
        check(stamped == pages, f"bank seal present on {stamped}/{pages} pages")
        check("SARFAESI" in text_all, "OCR recovered text from the scans")
        check(scanned_hits > 0,
              f"search finds 'POSSESSION' on a scanned page ({scanned_hits} hit(s))")
        check(target.stat().st_size / MB < 4.5,
              f"file size {target.stat().st_size / MB:.2f} MB is under the 4.5 MB default")
        doc.close()

    application.destroy()

    failed = [message for ok_item, message in RESULTS if not ok_item]
    print("\n" + "=" * 74)
    if failed:
        print(f" RESULT: {len(failed)} problem(s)")
        for message in failed:
            print(f"   - {message}")
        print("=" * 74)
        return 1
    print(f" RESULT: all {len(RESULTS)} checks passed - the app works on this PC.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
