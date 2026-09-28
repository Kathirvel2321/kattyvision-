"""UI regression tests that a syntax check cannot catch.

Run with:  python tools/ui_tests.py

These cover the things that only show up when the thing is actually drawn:
readable type, cards that look different from each other, a page map that lays
itself out without stacking tiles, and a settings search that opens the tab
holding the matches instead of leaving an empty page on screen.
"""
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf                                              # noqa: E402
from court_ocr import ui_kit                               # noqa: E402
from court_ocr.ui_kit import PageMap                       # noqa: E402
from court_ocr.app import App, AdvancedDialog              # noqa: E402
from court_ocr.config import Config                        # noqa: E402
import court_ocr.app as module                              # noqa: E402

WORK = Path("work")
WORK.mkdir(exist_ok=True)
FAILED: list[str] = []

#: At most one live App at a time.
#:
#: ttkbootstrap refuses a second application root while the first is still
#: alive, and the teardown is not instant either: a destroyed window only
#: really gives up its Tcl name once the event queue has been pumped.  Leaving
#: the old App around and building a new one handed later tests widgets that had
#: already been destroyed underneath them ("invalid command name"), which is a
#: miserable bug to chase because the test that triggers it is innocent.
_APPS: list[App] = []


def build_app(root: tk.Tk, config_name: str = "_ui_app_cfg.json") -> App:
    """Make an App, having properly torn down the previous one."""
    destroy_app()
    app = App(WORK / config_name)
    _APPS.append(app)
    for _ in range(3):
        root.update()               # let Tk finish the old window's teardown
    return app


def destroy_app() -> None:
    """Tear down every live App, ignoring anything already gone."""
    while _APPS:
        app = _APPS.pop()
        try:
            app.destroy()
        except Exception:
            pass


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        FAILED.append(name)


def sample_pdf(name: str, pages: int, label: str) -> Path:
    """A small generated PDF, so the tests never depend on a real case file."""
    path = WORK / name
    if not path.is_file():
        doc = pymupdf.open()
        for n in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 140), f"{label} {n + 1}", fontsize=30)
        doc.save(path)
        doc.close()
    return path


def pump(root: tk.Misc, queue: list, limit: float = 60.0) -> None:
    """Spin the event loop until the lazy render queue empties."""
    deadline = time.time() + limit
    while queue and time.time() < deadline:
        root.update()
        time.sleep(0.01)


def preview_wait(preview, limit: float = 5.0) -> bool:
    """Wait for the debounced seal picture to be drawn.

    The preview repaints 90 ms after the last change, so a test that only calls
    ``update()`` once would look at a blank panel and wrongly fail.
    """
    if preview is None:
        return False
    top = preview.winfo_toplevel()
    deadline = time.time() + limit
    while preview._photo is None and time.time() < deadline:
        try:
            top.update()
        except Exception:
            break
        time.sleep(0.02)
    return preview._photo is not None


def channel_gap(hex_a: str, hex_b: str) -> float:
    """Average per-channel distance between two hex colours, 0-255."""
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return sum(abs(x - y) for x, y in zip(a, b)) / 3.0


def test_type_scale() -> None:
    """Type must be big enough to read, and the palette must have real steps.

    The previous pass used 8/9/10 pt almost everywhere and put unselected cards
    on #f7f9fc against a #ffffff panel - a 1.5% difference.  Both are exactly
    the "tiny grey words, everything looks the same" complaint.
    """
    print("\nType scale and contrast")
    check("body text is at least 11pt", ui_kit.BODY >= 11, f"BODY={ui_kit.BODY}")
    check("hints are at least 10pt", ui_kit.CAPTION >= 10,
          f"CAPTION={ui_kit.CAPTION}")
    check("the scale has real steps between every level",
          ui_kit.DISPLAY > ui_kit.TITLE > ui_kit.HEADING > ui_kit.BODY
          > ui_kit.CAPTION,
          f"{ui_kit.DISPLAY}/{ui_kit.TITLE}/{ui_kit.HEADING}/{ui_kit.BODY}"
          f"/{ui_kit.CAPTION}")
    check("cards are visibly distinct from the panel behind them",
          channel_gap(ui_kit.SURFACE, ui_kit.CARD) >= 4.0,
          f"SURFACE={ui_kit.SURFACE} CARD={ui_kit.CARD}")
    check("the page background is distinct from cards",
          channel_gap(ui_kit.BG, ui_kit.CARD) >= 6.0, f"BG={ui_kit.BG}")
    check("a chosen card is distinct from an unchosen one",
          channel_gap(ui_kit.ACCENT_SOFT, ui_kit.SURFACE) >= 4.0,
          f"ACCENT_SOFT={ui_kit.ACCENT_SOFT} SURFACE={ui_kit.SURFACE}")


def test_page_map(root: tk.Tk) -> None:
    """The page map: every page visible, cuts where the user clicks."""
    print("\nPage map")
    frame = tk.Frame(root, bg=ui_kit.CARD)
    frame.pack(fill="both", expand=True)
    big = sample_pdf("_ui_t1.pdf", 23, "Exhibit")
    small = sample_pdf("_ui_t2.pdf", 5, "Annexure")
    pmap = PageMap(frame)
    pmap.pack(fill="both", expand=True)
    root.update()

    pmap.set_sources([(str(big), 23), (str(small), 5)])
    root.update()
    check("every page gets a tile", len(pmap.tiles) == 28,
          f"{len(pmap.tiles)} tiles")
    started = time.time()
    pump(root, pmap._queue)
    elapsed = time.time() - started
    shown = sum(1 for t in pmap.tiles if t._photo is not None)
    check("every thumbnail rendered", shown == len(pmap.tiles),
          f"{shown}/{len(pmap.tiles)}")
    check("28 thumbnails render in well under 10s", elapsed < 10,
          f"{elapsed:.1f}s")
    check("each extra source file gets a heading", len(pmap.headers) == 1,
          f"{len(pmap.headers)}")

    # The first version put every page of a file into a single grid row, so a
    # 20 page file drew all 20 tiles on top of each other.
    cells = {(t.grid_info().get("row"), t.grid_info().get("column"))
             for t in pmap.tiles}
    check("no two tiles share a grid cell", len(cells) == len(pmap.tiles),
          f"{len(cells)} cells for {len(pmap.tiles)} tiles")
    check("the first page of each file starts in column 0",
          all(t.grid_info().get("column") == 0
              for t in pmap.tiles if t.page_ref[1] == 0))

    pmap._toggle(pmap.tiles[3].page_ref)      # cut after 3 pages
    pmap._toggle(pmap.tiles[8].page_ref)      # then after 5 more
    sizes = [g[3] for g in pmap.groups()]
    check("a variable split gives 3 / 5 / 20", sizes == [3, 5, 20], f"{sizes}")
    check("the summary names every part",
          all(f"Part {n}" in pmap.summary.cget("text") for n in (1, 2, 3)))
    check("it says how many files will be made",
          "3 files" in pmap.var_count.get(), pmap.var_count.get())
    check("a cut page shows a tick, not just a colour",
          all("\u2713" in t.caption.cget("text") for t in pmap.tiles
              if t.page_ref in set(pmap.cuts())))

    pmap._toggle(pmap.tiles[3].page_ref)      # click page 4 again
    check("clicking a marked page again removes the cut",
          pmap.groups()[0][3] == 8, str(pmap.groups()[0][3]))
    pmap.clear_marks()
    pmap._toggle((0, 0))
    check("page 1 can never be cut off", len(pmap.groups()) == 1)

    pmap.set_part_size(7)
    pmap.split_evenly()
    even = [g[3] for g in pmap.groups()]
    check("split evenly never exceeds the chosen maximum",
          max(even) <= 7 and sum(even) == 28, f"{even}")
    pmap.destroy()


def test_split_respects_limit(root: tk.Tk) -> None:
    """"Split evenly" must use the page limit the user typed, before RUN.

    The page limit used to reach the map only when RUN was pressed, so
    "Split evenly" worked from a part size of zero - which the map read as
    "the whole document".  It then produced a 370-page part and a 1-page
    part, and those marks were sent as explicit cuts, so the finished files
    ignored the limit completely.
    """
    print("\nSplit and the page limit")
    app = build_app(root)
    app.withdraw()
    root.update()
    page = app.tool_pages["split"]

    # A long case: 120 pages across three documents.
    files = [sample_pdf("_ui_split_a.pdf", 50, "DocA"),
             sample_pdf("_ui_split_b.pdf", 50, "DocB"),
             sample_pdf("_ui_split_c.pdf", 20, "DocC")]
    page.paths = list(files)
    page._refresh_list()
    root.update()
    pump(root, getattr(page.page_map, "_queue", []) or [])
    check("the page map shows all three documents",
          page.page_map.sources and len(page.page_map.tiles) == 120,
          f"{len(page.page_map.tiles)} tiles")

    # The user types 40.  No RUN has happened yet.
    page.option_vars["max_pages"].set(40)
    root.update()
    check("the map learns the limit without waiting for RUN",
          page.page_map.part_size == 40, str(page.page_map.part_size))

    page.page_map.split_evenly()
    root.update()
    sizes = [g[3] for g in page.page_map.groups()]
    check("'Split evenly' really cuts at the typed limit",
          sizes and max(sizes) <= 40, f"{sizes}")
    check("'Split evenly' keeps every page",
          sum(sizes) == 120, f"{sum(sizes)} of 120")
    check("'Split evenly' makes the number of files you expect",
          len(sizes) == 3, f"{len(sizes)} files from {sizes}")

    # Changing the limit must move the cuts, not just be remembered.
    page.option_vars["max_pages"].set(20)
    root.update()
    page.page_map.split_evenly()
    root.update()
    sizes20 = [g[3] for g in page.page_map.groups()]
    check("lowering the limit really re-cuts",
          sizes20 and max(sizes20) <= 20 and len(sizes20) == 6,
          f"{sizes20}")

    # The cuts are what the runner uses, so they must be the ones on screen.
    opts = {"max_pages": 20, "cuts": [tuple(c) for c in page.page_map.cuts()],
            "sources": list(page.sources), "keep_bookmarks": True,
            "mode": "both", "max_mb": 0.0}
    out = WORK / "ui_split_out"
    out.mkdir(parents=True, exist_ok=True)
    from court_ocr import pdf_ops

    written = pdf_ops.split_pdf_at_cuts(
        [(str(p), int(n)) for p, n in opts["sources"]], opts["cuts"], out,
        keep_bookmark_boundaries=True)
    check("the run writes one file per part on screen",
          len(written) == len(sizes20), f"{len(written)} vs {len(sizes20)}")

    import pymupdf as _pdf

    counts: list[int] = []
    origins: list[str] = []
    for path in written:
        with _pdf.open(path) as doc:
            counts.append(doc.page_count)
            # A part must come wholly from one source document.
            origins.append(path.name.split("_Part")[0])

    # Every page must survive.  A part is never allowed to straddle two
    # documents, so the pages between that boundary and the next cut used to be
    # dropped on the floor - ten of them here, silently, with no error anywhere.
    check("the written parts cover every page", sum(counts) == 120,
          f"sum {sum(counts)} of 120 - pages were lost")
    check("no page is empty", all(c > 0 for c in counts), f"{counts}")
    check("each part is named after the document it came from",
          len(set(origins)) >= 3, f"{sorted(set(origins))}")

    # Where a document runs out mid-part, the next part has to start at that
    # document's first page, so it can legitimately end up over the limit.  That
    # is the one acceptable exception - quietly losing pages never is.
    over = [c for c in counts if c > 20]
    check("at most one part per document boundary can exceed the limit",
          len(over) <= 2, f"over-limit parts: {over}")

    # A part that DOES break the limit must be said out loud.
    from court_ocr.app import _oversize_warnings

    flagged = _oversize_warnings(written, {"max_pages": 20, "max_mb": 0.0})
    check("an over-limit part is reported to the user",
          bool(flagged) == bool(over),
          f"parts over: {over}, warnings: {flagged}")

    quiet = _oversize_warnings(written, {"max_pages": 0, "max_mb": 0.0})
    check("no warning when no limit was set", not quiet, " | ".join(quiet))
    destroy_app()


def test_search(root: tk.Tk) -> None:
    """Searching must open the tab holding the matches, not an empty tab."""
    print("\nAdvanced settings search")
    app = build_app(root)
    app.withdraw()
    root.update()
    dialog = AdvancedDialog(app, Config.load(WORK / "_ui_cfg.json"))
    root.update()

    check("all settings were built", len(dialog.rows) >= 30,
          f"{len(dialog.rows)} rows")
    start_tab = dialog.book.index("current")
    dialog.var_search.set("seal")
    root.update()
    found = [r for r in dialog.rows if r["control"].grid_info()]
    check("the search finds settings", len(found) > 0, f"{len(found)} visible")
    check("the count is shown", "found" in dialog.search_count.cget("text"),
          dialog.search_count.cget("text"))
    check("non-matching rows are hidden", len(found) < len(dialog.rows),
          f"{len(found)}/{len(dialog.rows)}")
    shown_tab = dialog.book.tab(dialog.book.index("current"), "text")
    check("the tab holding the first match is opened automatically",
          shown_tab in {r["tab"] for r in found},
          f"showing {shown_tab!r}, matches in { {r['tab'] for r in found} }")
    check("the count says where the matches are",
          "found" in dialog.search_count.cget("text")
          and ("tab" in dialog.search_count.cget("text")
               or "several" in dialog.search_count.cget("text")),
          dialog.search_count.cget("text"))

    dialog.var_search.set("zzzz-no-such-setting")
    root.update()
    check("a hopeless search says so clearly",
          "nothing matches" in dialog.search_count.cget("text"),
          dialog.search_count.cget("text"))

    dialog._clear_search()
    root.update()
    check("clearing the search restores every row",
          all(r["control"].grid_info() for r in dialog.rows))
    check("clearing the search returns to the original tab",
          dialog.book.index("current") == start_tab,
          f"{dialog.book.index('current')} vs {start_tab}")
    dialog.destroy()
    destroy_app()


def heavy_pdf(name: str, pages: int = 3) -> Path:
    """A PDF with real text *and* big colour photos - something worth shrinking."""
    path = WORK / name
    if not path.is_file():
        from PIL import Image

        photo = WORK / "_ui_heavy_photo.png"
        if not photo.is_file():
            img = Image.new("RGB", (1200, 850))
            px = img.load()
            for y in range(850):
                for x in range(0, 1200, 3):
                    r, g, b = (x * 7) % 256, (y * 3) % 256, ((x + y) * 5) % 256
                    for k in range(3):
                        px[x + k, y] = (r, g, b)
            img.save(photo)
        doc = pymupdf.open()
        for n in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((70, 100), f"MARKER PAGE {n + 1} MARKER", fontsize=20)
            for line in range(10):
                page.insert_text((70, 140 + line * 20),
                                 "the respondent shall pay the outstanding sum",
                                 fontsize=11)
            page.insert_image(pymupdf.Rect(70, 400, 525, 780), filename=str(photo))
        doc.save(path)
        doc.close()
    return path


def test_compress(root: tk.Tk) -> None:
    """Compress really compresses, keeps every page, and admits a missed target."""
    print("\nCompress")
    from court_ocr import optimize, pdf_ops

    MB = 1024 * 1024
    src = heavy_pdf("_ui_compress.pdf", 3)
    before = src.stat().st_size / MB
    check("the test file is big enough to be worth compressing", before > 1.0,
          f"{before:.2f} MB")

    # ---- the engine behind the Compress tool page
    out = WORK / "ui_compress_l2.pdf"
    result = pdf_ops.compress_pdf(src, out, level=2, target_mb=0, dpi=150)
    check("the compress step reported success", result.ok, result.detail)
    check("the file really did get smaller", result.after_mb < before,
          f"{before:.2f} -> {result.after_mb:.2f} MB")
    with pymupdf.open(src) as a, pymupdf.open(out) as b:
        check("no page was lost", b.page_count == a.page_count,
              f"{a.page_count} -> {b.page_count}")
        text = "".join(b[i].get_text() for i in range(b.page_count))
        check("the text survived compression", "MARKER PAGE 3 MARKER" in text,
              "marker missing")
        # A page that shrank is only useful if it still looks the same.
        check("the page is still the same physical size",
              abs(a[0].rect.width - b[0].rect.width) < 1
              and abs(a[0].rect.height - b[0].rect.height) < 1,
              f"{a[0].rect} -> {b[0].rect}")

    # Stronger settings must actually be stronger, or the control is a lie.
    sizes = []
    for level in (1, 3):
        dest = WORK / f"ui_compress_l{level}.pdf"
        sizes.append(pdf_ops.compress_pdf(src, dest, level=level, dpi=150).after_mb)
    check("a stronger setting really makes a smaller file", sizes[1] < sizes[0],
          f"L1 {sizes[0]:.2f} MB vs L3 {sizes[1]:.2f} MB")

    # ---- the engine the whole-case flow uses by default
    opt_out = WORK / "ui_optimize.pdf"
    opt = optimize.optimize_pdf(src, opt_out, preset="balanced", scrub_metadata=True)
    check("the default pipeline engine reported success", opt.ok, opt.detail)
    check("the default pipeline engine shrank the file", opt.after_mb < before,
          f"{before:.2f} -> {opt.after_mb:.2f} MB")
    with pymupdf.open(opt_out) as doc:
        check("the default pipeline engine kept every page", doc.page_count == 3,
              str(doc.page_count))

    # ---- the bug: a target that cannot be met must be reported as a failure.
    # Born-digital pages are deliberately left untouched, so asking for an
    # impossible size has to come back as "did not meet the target" rather than
    # a cheerful success.  It used to come back ok=True, which meant the
    # pipeline's "still above the portal limit" warning could never fire.
    tight = WORK / "ui_optimize_tight.pdf"
    missed = optimize.optimize_pdf(src, tight, preset="archival", target_mb=0.05)
    check("a size that cannot be reached is reported as not ok", not missed.ok,
          f"ok=True at {missed.after_mb:.2f} MB vs a 0.05 MB target")
    check("the failure says why", "above" in missed.detail and
          "target" in missed.detail, missed.detail)
    check("the file was still written", tight.is_file() and tight.stat().st_size > 0,
          "no output")
    with pymupdf.open(tight) as doc:
        check("even a failed compression keeps every page", doc.page_count == 3,
              str(doc.page_count))

    # A target that IS reachable must still come back as a success.
    reachable = WORK / "ui_optimize_ok.pdf"
    good = optimize.optimize_pdf(src, reachable, preset="archival",
                                  target_mb=before)
    check("a reachable target reports success", good.ok, good.detail)
    check("a reachable target is actually met", good.after_mb <= before,
          f"{good.after_mb:.2f} MB vs {before:.2f} MB")


def test_no_activity_panel(root: tk.Tk) -> None:
    """The Activity panel is gone, and the log that is left still works."""
    print("\nNo Activity panel")
    app = build_app(root)
    app.withdraw()
    root.update()

    check("the Activity panel is gone", not hasattr(app, "tool_log"),
          "tool_log still exists")
    # Nothing in the tool pages should be a log either.
    check("no tool page carries a log box",
          all(not hasattr(page, "tool_log") for page in app.tool_pages.values()),
          "found one")

    # With the panel gone, log_line still has to be safe, and the log behind
    # "Show technical details" must actually appear - it used to raise
    # "cannot use geometry manager" and silently do nothing.
    app.log_line("a line for the test")
    root.update()
    check("writing to the log still works",
          "a line for the test" in app.log_box.get("1.0", "end"),
          "not in the log")

    check("the technical log starts hidden", not app.log_visible, "already up")
    app.var_details.set(True)
    app._toggle_details()
    root.update()
    check("the details switch really shows the log", app.log_visible, "stayed off")
    check("the log is on screen when asked for",
          bool(app.log_box.grid_info()), "not gridded in")

    app.var_details.set(False)
    app._toggle_details()
    root.update()
    check("the details switch really hides the log again",
          not app.log_visible and not app.log_box.grid_info(), "stayed on")
    destroy_app()


def test_seal_bench(root: tk.Tk) -> None:
    """The seal workbench: a live picture, arrows, and page selection."""
    print("\nSeal workbench")
    import pymupdf
    from court_ocr.ui_kit import SealPreview
    from court_ocr.stamp import seal_rect

    pdf = sample_pdf("_ui_seal.pdf", 4, "Exhibit")
    seal = WORK / "_ui_seal.png"
    if not seal.is_file():
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((12, 12, 288, 288), outline=(180, 30, 30, 255), width=14)
        draw.line((70, 150, 230, 150), fill=(180, 30, 30, 255), width=12)
        img.save(seal)

    app = build_app(root)
    app.withdraw()
    root.update()
    page = app.tool_pages["stamp"]
    check("the seal page has a live preview", page.seal_preview is not None)
    check("the seal page has a page chooser", page.seal_pages is not None)

    page.paths = [pdf]
    page.option_vars["seal_image"].set(str(seal))
    page._refresh_list()
    root.update()
    # The picture is debounced, so give the event loop the moment it needs.
    pump(root, [1] if preview_wait(page.seal_preview) else [])

    preview = page.seal_preview
    check("the preview counted the pages", preview.page_count == 4,
          f"{preview.page_count}")
    check("the picture was drawn", preview._photo is not None)
    check("the read-out says where it is",
          "bottom right" in preview.hint.cget("text"),
          preview.hint.cget("text"))

    # --- the picture must follow the controls
    for position in ("top-left", "bottom-left", "top-right", "bottom-right"):
        page.option_vars["position"].set(position)
        root.update()
        check(f"the picture follows '{position}'",
              preview.position == position, preview.position)
    page.option_vars["width_percent"].set(45)
    root.update()
    check("the picture follows the size", preview.width_percent == 45.0,
          str(preview.width_percent))
    page.option_vars["opacity"].set(0.4)
    root.update()
    check("the picture follows the strength",
          abs(preview.opacity - 0.4) < 1e-6, str(preview.opacity))

    # --- your own seal must be pickable from the bench, not buried away
    check("the bench shows a box for your own seal",
          getattr(page, "seal_image_entry", None) is not None, "no entry")
    check("the bench entry drives the run options",
          page.seal_image_var is page.option_vars["seal_image"], "different var")
    check("the bench box starts with something in it",
          bool(page.seal_image_var.get()), "empty")
    my_seal = WORK / "_ui_my_seal.png"
    if not my_seal.is_file():
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((8, 8, 192, 192), fill=(0, 90, 200, 255))
        img.save(my_seal)
    # Type it in the way a person types a path, and the picture must follow.
    page.seal_image_var.set(str(my_seal))
    root.update()
    check("typing a seal path loads it into the picture",
          preview.seal_path == my_seal, str(preview.seal_path))
    check("the run would use the seal I typed",
          str(my_seal) in str(page.option_vars["seal_image"].get()),
          page.option_vars["seal_image"].get())

    # --- every arrow must move in the direction its glyph points.  The pad
    #     used to send all four to the right, so "up" looked alive but slid
    #     sideways.  These press the real buttons, not the maths behind them.
    for name, want_x, want_y in (("up", 0.0, -1.0), ("down", 0.0, 1.0),
                                 ("left", -1.0, 0.0), ("right", 1.0, 0.0)):
        preview.reset_nudge()
        root.update()
        preview.arrow_buttons[name].event_generate("<Button-1>")
        root.update()
        got = (preview.nudge_x, preview.nudge_y)
        check(f"the {name} arrow really moves {name}",
              got == (want_x, want_y), f"got {got}, wanted {want_x},{want_y}")
    check("all four arrow buttons exist",
          set(preview.arrow_buttons) == {"up", "down", "left", "right"},
          ", ".join(sorted(preview.arrow_buttons)))
    preview.reset_nudge()
    root.update()

    # --- sliders, used the way a person would: drag the handle
    page.option_vars["width_percent"].set(20)
    page.option_vars["opacity"].set(0.9)
    root.update()
    size_var = preview._vars["width_percent"]
    check("the size slider starts where the option did", size_var.get() == 20.0,
          str(size_var.get()))
    size_var.set(35)
    root.update()
    check("dragging the size slider resizes the picture",
          preview.width_percent == 35.0, str(preview.width_percent))
    check("the size slider reaches the run options",
          float(page.option_vars["width_percent"].get()) == 35.0,
          page.option_vars["width_percent"].get())
    check("the size is printed next to its slider",
          "35" in preview._readouts["width_percent"].cget("text"),
          preview._readouts["width_percent"].cget("text"))

    strong_var = preview._vars["opacity"]
    strong_var.set(45)
    root.update()
    check("dragging the strength slider changes the strength",
          abs(preview.opacity - 0.45) < 1e-6, str(preview.opacity))
    check("the strength slider reaches the run options",
          abs(float(page.option_vars["opacity"].get()) - 0.45) < 1e-6,
          page.option_vars["opacity"].get())
    # Sliders are horizontal - a vertical one would read as a volume fader
    # turned the wrong way and be easy to misread.
    check("both sliders are horizontal",
          all(str(preview._sliders[k].cget("orient")) == "horizontal"
              for k in ("width_percent", "opacity")),
          ", ".join(str(preview._sliders[k].cget("orient"))
                    for k in ("width_percent", "opacity")))
    # An option edited by hand must drag the slider back to match it.
    page.option_vars["width_percent"].set(28)
    root.update()
    check("editing the number by hand moves the slider too",
          preview._vars["width_percent"].get() == 28.0,
          str(preview._vars["width_percent"].get()))
    page.option_vars["width_percent"].set(20)
    page.option_vars["opacity"].set(0.9)
    root.update()

    # --- the arrows move it, and in a predictable direction
    page.option_vars["position"].set("bottom-right")
    page.option_vars["width_percent"].set(20)
    page.option_vars["opacity"].set(0.9)
    root.update()
    preview.nudge(0.0, 0.0)
    root.update()
    with pymupdf.open(pdf) as doc:
        before = seal_rect(doc[0], 20.0, 1.0, "bottom-right", 12, 12, 0, 0)
    preview.nudge(5.0, 0.0)
    root.update()
    with pymupdf.open(pdf) as doc:
        after = seal_rect(doc[0], 20.0, 1.0, "bottom-right", 12, 12, 5.0, 0.0)
    check("right really moves right", after.x0 > before.x0,
          f"{before.x0:.1f} -> {after.x0:.1f}")
    check("the arrow moved the seal the expected 5 mm",
          abs((after.x0 - before.x0) - 5.0 * 72.0 / 25.4) < 0.5,
          f"{after.x0 - before.x0:.2f}pt")
    check("the page remembers the nudge", page.nudge_x == 5.0,
          str(page.nudge_x))

    preview.nudge(0.0, 3.0)
    root.update()
    with pymupdf.open(pdf) as doc:
        down = seal_rect(doc[0], 20.0, 1.0, "bottom-right", 12, 12, 5.0, 3.0)
    check("down really moves down", down.y0 > after.y0,
          f"{after.y0:.1f} -> {down.y0:.1f}")

    preview.reset_nudge()
    root.update()
    check("reset puts it back", preview.nudge_x == 0.0 and preview.nudge_y == 0.0)

    # --- nudging can never push the seal off the paper
    with pymupdf.open(pdf) as doc:
        page_obj = doc[0]
        wild = seal_rect(page_obj, 20.0, 1.0, "bottom-right", 12, 12,
                         900.0, 900.0)
        inside = page_obj.rect.contains(wild)
    check("a huge nudge is still clamped onto the paper", inside, str(wild))

    # --- page selection
    page.page_stepper.set(3, notify=True)
    root.update()
    check("the picture follows the page stepper", preview.page == 2,
          str(preview.page))
    page.var_seal_pages.set("last")
    root.update()
    check("choosing 'last only' jumps to the last page",
          page.page_stepper.get() == 4, str(page.page_stepper.get()))
    page.var_seal_pages.set("all")
    root.update()

    # --- the run options carry the picture's decisions
    opts = {"nudge_x": page.nudge_x, "nudge_y": page.nudge_y,
            "pages": page.var_seal_pages.get()}
    check("the run receives the nudge", opts["nudge_x"] == 0.0)
    check("the run receives the page choice", opts["pages"] == "all")
    destroy_app()


def test_seal_run(root: tk.Tk) -> None:
    """Run the seal tool for real and confirm the file came out as previewed."""
    print("\nSeal run")
    import pymupdf
    from court_ocr import stamp as stamp_mod

    pdf = sample_pdf("_ui_run.pdf", 5, "Exhibit")
    seal = WORK / "_ui_run_seal.png"
    if not seal.is_file():
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((10, 10, 230, 230), outline=(200, 30, 30, 255), width=12)
        img.save(seal)

    out = WORK / "ui_seal_out"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "sealed.pdf"
    result = stamp_mod.apply_stamp(
        pdf, dest, image_path=seal, pages="first", position="bottom-right",
        width_percent=20, opacity=0.9, nudge_x_mm=0, nudge_y_mm=0,
        cache_dir=str(WORK))
    check("the seal was applied", result.ok, result.detail)
    check("only the chosen page was stamped", result.pages_stamped == 1,
          str(result.pages_stamped))
    with pymupdf.open(dest) as doc:
        check("the output kept every page", doc.page_count == 5,
              str(doc.page_count))
        # Read the real placement back out of the saved file and compare it
        # with the preview's own calculation.  If these disagree, the picture
        # is lying - which is the one thing the preview must never do.
        images = doc[0].get_images(full=True)
        check("the seal image is really on page 1", len(images) == 1,
              f"{len(images)} images")
        if images:
            found = doc[0].get_image_rects(images[0][0])
            with pymupdf.open(pdf) as src:
                expected = stamp_mod.seal_rect(src[0], 20, 1.0,
                                               "bottom-right", 12, 12, 0, 0)
            got = found[0]
            check("the saved seal sits exactly where the preview drew it",
                  abs(got.x0 - expected.x0) < 1.5
                  and abs(got.y0 - expected.y0) < 1.5,
                  f"saved {got} vs previewed {expected}")

    # And with a nudge, the saved file must move by the same amount.
    dest2 = out / "sealed_nudged.pdf"
    result2 = stamp_mod.apply_stamp(
        pdf, dest2, image_path=seal, pages="first", position="bottom-right",
        width_percent=20, opacity=0.9, nudge_x_mm=-8, nudge_y_mm=0,
        cache_dir=str(WORK))
    check("the nudged run succeeded", result2.ok, result2.detail)
    with pymupdf.open(dest2) as doc:
        got = doc[0].get_image_rects(doc[0].get_images(full=True)[0][0])[0]
    with pymupdf.open(pdf) as src:
        base = stamp_mod.seal_rect(src[0], 20, 1.0, "bottom-right", 12, 12, 0, 0)
    moved = base.x0 - got.x0
    check("an 8 mm left nudge really moved the saved seal 8 mm",
          abs(moved - 8 * 72.0 / 25.4) < 1.0, f"{moved:.2f}pt")


def test_ocr_recheck(root: tk.Tk) -> None:
    """The OCR second look: a doubtful page must be read again, not accepted."""
    print("\nOCR second look")
    import pymupdf
    from court_ocr import ocr_engine as ocr
    from court_ocr.toolcheck import find_tesseract

    tool = find_tesseract()
    if not tool.available:
        print("  [SKIP] Tesseract not installed - nothing to check here")
        return
    tessdata = tool.extra_dir

    # A full page of real text: the first pass should be plenty, so the page
    # must NOT be re-read.  Re-reading every page would double the runtime.
    good = WORK / "_ui_ocr_good.pdf"
    if not good.is_file():
        doc = pymupdf.open()
        sheet = doc.new_page(width=595, height=842)
        for line in range(28):
            sheet.insert_text((70, 90 + line * 24),
                              "the quick brown fox jumps over the lazy dog", fontsize=13)
        doc.save(good)
        doc.close()
    with pymupdf.open(good) as doc:
        page = doc[0]
        data, ink = ocr._ocr_page(page, 300, "eng", tessdata, with_ink=True)
    letters = sum(1 for c in ocr._page_text(data) if c.isalnum())
    check("a normal page is measured as having ink", ink > ocr.INK_MIN,
          f"{ink:.4f}")
    check("a normal page is NOT flagged as incomplete",
          not ocr.looks_incomplete(ocr._page_text(data), ink),
          f"{letters} letters, ink {ink:.4f}")

    # A page that is almost all ink but yields no text at all: exactly the
    # "first render left some letters out" case the user described.
    noisy = WORK / "_ui_ocr_noisy.pdf"
    if not noisy.is_file():
        from PIL import Image, ImageDraw

        img = Image.new("L", (1240, 1754), 255)
        draw = ImageDraw.Draw(img)
        for y in range(60, 1700, 14):          # dense ruled noise, no glyphs
            draw.line((40, y, 1200, y), fill=40, width=3)
        img.save(noisy)
    with pymupdf.open(noisy) as doc:
        pix = doc[0].get_pixmap(dpi=150)
    noise_ink = ocr.ink_fraction(pix)
    check("a page full of marks measures as heavy ink", noise_ink > 0.15,
          f"{noise_ink:.3f}")
    check("a page full of marks but no words IS flagged",
          ocr.looks_incomplete("", noise_ink), "not flagged")

    # A blank page must never trigger the second look - there is nothing to lose.
    blank = WORK / "_ui_ocr_blank.pdf"
    if not blank.is_file():
        pymupdf.open().new_page(width=595, height=842)
        doc = pymupdf.open()
        doc.new_page(width=595, height=842)
        doc.save(blank)
        doc.close()
    with pymupdf.open(blank) as doc:
        blank_ink = ocr.ink_fraction(doc[0].get_pixmap(dpi=150))
    check("a blank page is not flagged", not ocr.looks_incomplete("", blank_ink),
          f"ink {blank_ink:.5f}")

    # A faded photocopy must still count as having writing.  With a fixed grey
    # cut-off its text measured as pure white, the page looked blank, and the
    # second look never fired on exactly the pale scans that need it.
    faded = WORK / "_ui_ocr_faded.png"
    if not faded.is_file():
        from PIL import Image as _Image, ImageDraw as _Draw, ImageFont

        try:
            _font = ImageFont.truetype("arial.ttf", 13)
        except Exception:
            _font = ImageFont.load_default()
        _img = _Image.new("L", (595, 842), 255)
        _pen = _Draw.Draw(_img)
        for line in range(20):
            _pen.text((50, 90 + line * 26), "faded court order text", fill=170,
                      font=_font)
        _img.save(faded)
    faded_pdf = WORK / "_ui_ocr_faded.pdf"
    if not faded_pdf.is_file():
        doc = pymupdf.open()
        sheet = doc.new_page(width=595, height=842)
        sheet.insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(faded))
        doc.save(faded_pdf)
        doc.close()
    with pymupdf.open(faded_pdf) as doc:
        faded_data, faded_ink = ocr._ocr_page(doc[0], 300, "eng", tessdata,
                                              with_ink=True)
    check("a faded page still counts as having writing", faded_ink > ocr.INK_MIN,
          f"ink {faded_ink:.4f}")
    # And it is read properly, so it must NOT be needlessly read again.
    check("a faded but readable page is not flagged",
          not ocr.looks_incomplete(ocr._page_text(faded_data), faded_ink),
          f"ink {faded_ink:.4f}")

    # End to end: the real engine, with the second look on, must not lose a
    # single page and must still produce real text.
    out = WORK / "ui_ocr_out.pdf"
    result = ocr.run_ocr(good, out, language="eng", dpi=300, jobs=1,
                         force=True, recheck=True)
    check("the rechecked run succeeded", result.ok, result.detail)
    check("no page was lost by the second look",
          result.pages_total == 1, str(result.pages_total))
    # Only pages that actually looked doubtful are read twice.  A clean page
    # must not be re-read, or the second look would double the runtime of
    # every ordinary run.
    check("a clean page is not read a second time",
          result.pages_rechecked == 0, str(result.pages_rechecked))
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
    check("the rechecked page still has its text", len(text.strip()) > 200,
          f"{len(text.strip())} chars")
    # The page must come out the same physical size it went in at.
    with pymupdf.open(good) as src:
        src_rect = src[0].rect
    with pymupdf.open(out) as doc:
        out_rect = doc[0].rect
    check("the rechecked page kept its size",
          abs(src_rect.width - out_rect.width) < 1
          and abs(src_rect.height - out_rect.height) < 1,
          f"{src_rect} -> {out_rect}")


def main() -> int:
    module.messagebox = type("Silent", (), {
        "showinfo": staticmethod(lambda *a, **k: None),
        "showwarning": staticmethod(lambda *a, **k: None),
        "showerror": staticmethod(lambda *a, **k: None),
        "askyesno": staticmethod(lambda *a, **k: False),
    })
    root = tk.Tk()
    root.withdraw()
    try:
        test_type_scale()
        test_page_map(root)
        test_split_respects_limit(root)
        test_compress(root)
        test_no_activity_panel(root)
        test_seal_bench(root)
        test_seal_run(root)
        test_ocr_recheck(root)
        test_search(root)
    finally:
        destroy_app()
        root.destroy()
    print()
    if FAILED:
        print(f"RESULT: {len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("RESULT: all UI checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
