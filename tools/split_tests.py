"""Split tests - the page limit must be obeyed exactly, and the file-size rule
must never quietly re-cut a part the user already sized.

    python tools/split_tests.py

Exit code 0 = every check passed.  Run this after touching pdf_ops.split_pdf,
the split step in pipeline.py, or the Split tool page in app.py.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf                                                    # noqa: E402

from court_ocr import pdf_ops                                     # noqa: E402
from court_ocr.config import Config                               # noqa: E402

WORK = Path("work") / "split_tests"
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok, detail: str = "") -> bool:
    ok = bool(ok)
    RESULTS.append((ok, name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ::  {detail}" if detail else ""),
          flush=True)
    return ok


def build(pages: int, bookmark_every: int = 0) -> Path:
    """A small, fast placeholder PDF with real (vector) text."""
    path = WORK / f"src_{pages}{'_bm' if bookmark_every else ''}.pdf"
    if path.is_file():
        return path
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=595.276, height=841.89)
        page.insert_text((60, 80), f"ANNEXURE {n + 1} of {pages}", fontsize=13,
                         fontname="hebo")
        for row in range(6):
            page.insert_text((60, 130 + row * 26),
                             "SARFAESI Act 2002 - Section 13(4) possession notice.",
                             fontsize=10)
    if bookmark_every:
        doc.set_toc([[1, f"Document {i // bookmark_every + 1}", i + 1]
                     for i in range(0, pages, bookmark_every)])
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    return path


def sizes_of(files) -> list[int]:
    return [pdf_ops.inspect(p).pages for p in files]


def total_pages_of(files) -> int:
    return sum(sizes_of(files))



def main() -> int:
    shutil.rmtree(ROOT / WORK, ignore_errors=True)
    (ROOT / WORK).mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print(" Split tests")
    print("=" * 78)

    # ---------------------------------------------------------- the main case
    print("\n1. 300 pages, 40 per upload  (the case that used to go wrong)")
    src = build(300)
    started = time.time()
    parts = pdf_ops.split_pdf(src, ROOT / WORK / "out_40", mode="pages",
                              max_pages=40, max_size_mb=0, case="CASE")
    took = time.time() - started
    sizes = sizes_of(parts.files)
    check("exactly 8 parts come out", len(parts.files) == 8, f"{len(parts.files)}: {sizes}")
    check("every part is filled to the 40-page limit", sizes == [40] * 7 + [20], f"{sizes}")
    check("no page is lost or duplicated", total_pages_of(parts.files) == 300,
          f"{total_pages_of(parts.files)} of 300")
    check("no part breaks the limit", max(sizes) <= 40, f"largest {max(sizes)}")
    check("nothing is reported as a problem", not parts.skipped, str(parts.skipped))
    check("it is quick", took < 30, f"{took:.1f}s for 300 pages")

    # ------------------------------------------- the MB rule must stay out of it
    print("\n2. the same case with an absurdly small MB limit - still page count only")
    # 0.0005 MB = 0.5 KB, so every part is "over" it: the size rule is switched
    # on but the mode says the page count is the rule.
    parts = pdf_ops.split_pdf(src, ROOT / WORK / "out_40_tiny", mode="pages",
                              max_pages=40, max_size_mb=0.0005, case="CASE")
    sizes = sizes_of(parts.files)
    check("the MB limit does not change the number of parts", len(parts.files) == 8,
          f"{len(parts.files)}: {sizes}")
    check("the parts are still 40 pages each", sizes == [40] * 7 + [20], f"{sizes}")
    check("over-size parts are only reported, never re-cut",
          not parts.skipped and len(parts.over_size) == 8,
          f"warnings={parts.skipped} reported={len(parts.over_size)}")

    # ----------------------------------------- the MB rule still works on request
    print("\n3. opt-in size splitting still works (mode='both')")
    parts = pdf_ops.split_pdf(build(30), ROOT / WORK / "out_both", mode="both",
                              max_pages=30, max_size_mb=0.0005, case="SMALL")
    sizes = sizes_of(parts.files)
    check("size splitting produces more, smaller parts", len(parts.files) > 1,
          f"{len(parts.files)}: {sizes}")
    check("it never gets stuck (no empty part)", all(n >= 1 for n in sizes), f"{sizes}")
    check("no page is lost while size splitting", total_pages_of(parts.files) == 30,
          f"{total_pages_of(parts.files)} of 30")

    # ------------------------------------------------------- document boundaries
    print("\n4. 'never cut a document in half' still holds")
    marked = build(300, bookmark_every=50)
    parts = pdf_ops.split_pdf(marked, ROOT / WORK / "out_bm", mode="pages",
                              max_pages=40, keep_bookmark_boundaries=True, case="CASE")
    sizes = sizes_of(parts.files)
    check("no part breaks the 40-page limit", max(sizes) <= 40, f"largest {max(sizes)}")
    check("no page is lost at a document boundary", total_pages_of(parts.files) == 300,
          f"{total_pages_of(parts.files)} of 300")
    print(f"       part sizes: {sizes}")
    check("parts are still full (only shortened at a document start)",
          sizes[0] == 40 and sizes[-1] <= 40, f"{sizes}")
    parts = pdf_ops.split_pdf(marked, ROOT / WORK / "out_nobm", mode="pages",
                              max_pages=40, keep_bookmark_boundaries=False, case="CASE")
    check("with that option off the split is purely 40 + 40 ...",
          sizes_of(parts.files) == [40] * 7 + [20], f"{sizes_of(parts.files)}")

    # ------------------------------------------------------------- edge numbers
    print("\n5. edges")
    one = pdf_ops.split_pdf(build(40), ROOT / WORK / "out_exact", mode="pages",
                            max_pages=40, case="EXACT")
    check("exactly the limit -> one part", len(one.files) == 1,
          f"{len(one.files)}: {sizes_of(one.files)}")
    two = pdf_ops.split_pdf(build(41), ROOT / WORK / "out_plus1", mode="pages",
                            max_pages=40, case="PLUS1")
    check("one page over -> 40 + 1", sizes_of(two.files) == [40, 1],
          f"{sizes_of(two.files)}")

    # ---------------------------------------------------- the whole pipeline
    print("\n7. the one-click pipeline (merge + split only)")
    case_in = ROOT / WORK / "case_in"
    case_in.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, case_in / "300 pages.pdf")
    cfg = Config.load(ROOT / "config.json")
    cfg.set("paths.input", str(case_in))
    cfg.set("paths.output", str(ROOT / WORK / "pipe_out"))
    cfg.set("paths.work", str(ROOT / WORK / "pipe_work"))
    cfg.set("paths.logs", str(ROOT / WORK / "pipe_logs"))
    cfg.set("case.name", "PIPE")
    cfg.set("split.max_pages_per_file", 40)
    cfg.set("split.mode", "pages")
    cfg.set("split.max_size_mb", 0)
    from court_ocr.pipeline import Pipeline                          # noqa: E402

    report = Pipeline(cfg).run(steps=["merge", "split"])
    step = next((s for s in report.steps if s.step == "split"), None)
    check("the pipeline splits the merged file into 40-page parts",
          bool(report.outputs) and all(pdf_ops.inspect(p).pages <= 40
                                       for p in report.outputs),
          f"{len(report.outputs)} part(s): "
          f"{[pdf_ops.inspect(p).pages for p in report.outputs]}")
    check("the log line states the page-count rule",
          step is not None and "by page limit (40/file)" in step.detail,
          step.detail if step else "-")

    # ------------------------------------------------ the GUI Split tool runner
    print("\n8. the PDF Tools -> Split page (its own defaults)")
    from court_ocr.app import TOOL_SPECS, _tool_split                 # noqa: E402

    spec = next(s for s in TOOL_SPECS if s.key == "split")
    defaults = {key: default for _kind, _label, key, default, *_rest in spec.options}
    check("the tool defaults to page-count-only splitting with no MB limit",
          defaults.get("mode") == "pages" and float(defaults.get("max_mb", 1)) == 0.0,
          str(defaults))
    outputs, warnings = _tool_split([src], ROOT / WORK / "tool_out",
                                    dict(defaults, max_pages=40),
                                    {"progress": lambda message: None, "gs_path": ""})
    check("the tool produces the same 8 parts", len(outputs) == 8,
          f"{len(outputs)}: {sizes_of(outputs)} warnings={warnings}")

    # ------------------------------------------------------------------ summary
    failed = [(n, d) for ok, n, d in RESULTS if not ok]
    print("\n" + "=" * 78)
    if failed:
        print(f" RESULT: {len(failed)} of {len(RESULTS)} checks failed")
        for name, detail in failed:
            print(f"   FAIL  {name}  ::  {detail}")
    else:
        print(f" RESULT: all {len(RESULTS)} checks passed - the page limit is exact.")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

    # ------------------------------------------------- the page map (exact cuts)
    print("\n6. page-map cuts (the user clicks where each part starts)")
    written = pdf_ops.split_pdf_at_cuts([(str(src), 300)], [(0, 40), (40, 80)],
                                        ROOT / WORK / "out_cuts")
    check("two exact parts", len(written) == 2 and sizes_of(written) == [40, 40],
          f"{sizes_of(written)}")
