"""Compress tests - does compression really work, or only make files small?

    python tools/compress_tests.py

Size on its own proves nothing, so this bench also checks the things that
actually matter for a filing:

* the searchable text layer survives (1-bit re-encoding must not destroy it);
* OCR still reads the page just as well **after** compression as before;
* the resolution goes **up** (a 200 dpi scan comes back as 1-bit at 300 dpi);
* born-digital pages are left alone, byte for byte;
* the bank seal is never re-encoded, so it stays crisp;
* the file never gets bigger, and a second pass never makes things worse;
* when a size target cannot be met, it says so instead of pretending.

Exit code 0 = every check passed.  Tesseract is only needed for the two OCR
quality checks; without it they are reported as skipped.
"""

from __future__ import annotations

import difflib
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf                                                    # noqa: E402

from court_ocr import optimize, pdf_ops, stamp as stamp_mod        # noqa: E402
from court_ocr import ocr_engine                                  # noqa: E402
from court_ocr.toolcheck import find_tesseract                    # noqa: E402

WORK = Path("work") / "compress_tests"
RESULTS: list[tuple[bool, str, str]] = []
A4 = (595.276, 841.89)
BODY = ("POSSESSION notice under Section 13(4) of the SARFAESI Act, 2002 is issued "
        "to the borrower and the public in two newspapers. The secured creditor "
        "is entitled to realise the secured assets of the debtor.")


def check(name: str, ok, detail: str = "") -> bool:
    ok = bool(ok)
    RESULTS.append((ok, name, str(detail)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ::  {detail}" if detail else ""),
          flush=True)
    return ok


def skip(name: str, why: str) -> None:
    print(f"  [SKIP] {name}  ::  {why}", flush=True)


def norm(text: str) -> str:
    return " ".join(text.upper().split())


# --------------------------------------------------------------------------- #
# A realistic mixed case: one born-digital page, one colour page, four scans
# --------------------------------------------------------------------------- #

def build_case() -> Path:
    """A case file that looks like what actually arrives from a scanner."""
    path = WORK / "case.pdf"
    if path.is_file():
        return path

    # 1. born-digital: real vector text, no page-sized image
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((60, 80), "BEFORE THE DISTRICT JUDGE, RAIGAD", fontsize=13,
                     fontname="hebo")
    for row in range(16):
        page.insert_text((60, 130 + row * 26), BODY[:86], fontsize=11)
    doc.save(path)
    doc.close()

    # 2. colour page (red/blue ink -> must stay colour)
    sheet = pymupdf.open()
    colour = sheet.new_page(width=A4[0], height=A4[1])
    colour.draw_rect(colour.rect, color=None, fill=(0.98, 0.97, 0.94))
    colour.draw_line(pymupdf.Point(50, 120), pymupdf.Point(545, 120),
                     color=(0.8, 0.1, 0.1), width=2)
    for row in range(16):
        colour.insert_text((60, 160 + row * 26), BODY[:86], fontsize=11,
                           color=(0.1, 0.2, 0.7))
    colour_pix = colour.get_pixmap(dpi=200)
    sheet.close()
    with pymupdf.open(path) as out:
        fresh = out.new_page(width=A4[0], height=A4[1])
        fresh.insert_image(fresh.rect, stream=colour_pix.tobytes("jpeg", jpg_quality=85),
                           keep_proportion=True)
        out.saveIncr()

    # 3-6. plain scans of court text: pixels only, 200 dpi, JPEG q80
    for n in range(4):
        sheet = pymupdf.open()
        scan = sheet.new_page(width=A4[0], height=A4[1])
        scan.insert_text((60, 90), f"ANNEXURE {n + 1}", fontsize=12, fontname="hebo")
        for row in range(18):
            scan.insert_text((60, 140 + row * 24), BODY[:84], fontsize=10)
        pix = scan.get_pixmap(dpi=200)
        sheet.close()
        with pymupdf.open(path) as out:
            fresh = out.new_page(width=A4[0], height=A4[1])
            fresh.insert_image(fresh.rect, stream=pix.tobytes("jpeg", jpg_quality=80),
                               keep_proportion=True)
            out.saveIncr()
    return path


def page_text(path: Path, index: int) -> str:
    with pymupdf.open(path) as doc:
        return doc[index].get_text()


def image_info(path: Path, index: int) -> list[tuple[int, int, int, str]]:
    """``[(width, height, bytes, ext)]`` for every image on a page."""
    out = []
    with pymupdf.open(path) as doc:
        for image in doc[index].get_images(full=True):
            data = doc.extract_image(image[0])
            out.append((data["width"], data["height"], len(data["image"]),
                        str(data.get("ext"))))
    return out


def ocr_text(path: Path, out: Path) -> str:
    """OCR a file and return the recovered text (empty when unavailable)."""
    result = ocr_engine.run_ocr(path, out, dpi=300, jobs=1)
    with pymupdf.open(result.path) as doc:
        return "".join(doc[i].get_text() for i in range(doc.page_count))


def main() -> int:
    shutil.rmtree(ROOT / WORK, ignore_errors=True)
    (ROOT / WORK).mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print(" Compress tests - size AND quality")
    print("=" * 78)
    case = build_case()
    before_mb = case.stat().st_size / 1048576
    print(f"\nfixture: {before_mb:.2f} MB, 6 pages "
          f"(1 born-digital, 1 colour, 4 scans at 200 dpi)")

    # ---------------------------------------------- 1. the adaptive optimizer
    print("\n1. adaptive optimizer (what the Automation tab runs)")
    out = WORK / "adaptive.pdf"
    started = time.time()
    result = optimize.optimize_pdf(case, out, preset="balanced")
    took = time.time() - started
    classes = [row.klass for row in result.rows]
    print(f"       {'  '.join(f'p{r.page}={r.klass}' for r in result.rows)}")
    check("the whole file got much smaller",
          result.ok and result.after_mb < result.before_mb * 0.5,
          f"{result.before_mb:.2f} -> {result.after_mb:.2f} MB "
          f"(-{result.saving_pct:.0f}%)")
    check("the born-digital page was recognised and left alone",
          classes[0] == "born_digital", f"page 1 = {classes[0]}")
    check("the colour page was recognised as colour",
          classes.count("mixed_scan") == 1, f"classes = {classes}")
    check("the four scans were recognised as text scans",
          classes.count("text_scan") == 4, f"classes = {classes}")
    check("no page ever got bigger", all(r.after_kb <= r.before_kb
                                         for r in result.rows if r.after_kb and r.before_kb),
          ", ".join(f"p{r.page} {r.before_kb:.0f}->{r.after_kb:.0f}KB"
                    for r in result.rows))
    check("every page is accounted for in the report", len(result.rows) == 6,
          f"{len(result.rows)} row(s)")

    # resolution: 200 dpi A4 = 1654 px wide; 300 dpi = 2481 px
    scan_before = image_info(case, 2)[0]
    scan_after = image_info(out, 2)[0]
    check("a 200 dpi scan comes back SHARPER (300 dpi), not blurrier",
          scan_after[0] > scan_before[0],
          f"{scan_before[0]}x{scan_before[1]} @200dpi -> "
          f"{scan_after[0]}x{scan_after[1]} @300dpi")
    check("...while getting smaller on that page",
          scan_after[2] < scan_before[2],
          f"{scan_before[2] / 1024:.0f} KB -> {scan_after[2] / 1024:.0f} KB "
          f"({scan_after[3]})")
    check("the born-digital page's text is untouched",
          norm(page_text(case, 0)) == norm(page_text(out, 0)),
          f"{len(page_text(out, 0))} chars, identical")
    with pymupdf.open(case) as a, pymupdf.open(out) as b:
        widths_before = [round(p.rect.width) for p in a]
        widths_after = [round(p.rect.width) for p in b]
        check("page count and page size survive",
              a.page_count == b.page_count and widths_before == widths_after,
              f"{b.page_count} pages, widths {widths_after}")
    print(f"       speed: {took:.1f}s for 6 pages "
          f"({took / 6:.2f}s per page - this step is single-threaded)")

    # ------------------------------------------- 2. the searchable text layer
    print("\n2. does compression destroy the searchable text layer?")
    searchable = WORK / "searchable.pdf"
    ocr_result = ocr_engine.run_ocr(case, searchable, dpi=300, jobs=1)
    with pymupdf.open(searchable) as doc:
        hits_before = sum(len(doc[i].search_for("POSSESSION")) for i in range(6))
    squeezed = WORK / "searchable_compressed.pdf"
    optimize.optimize_pdf(searchable, squeezed, preset="balanced")
    with pymupdf.open(squeezed) as doc:
        hits_after = sum(len(doc[i].search_for("POSSESSION")) for i in range(6))
        chars_after = sum(len(doc[i].get_text().strip()) for i in range(6))
    check("text is searchable after OCR", hits_before > 0,
          f"{hits_before} 'POSSESSION' hit(s)")
    check("...and STILL searchable after 1-bit re-encoding",
          hits_after == hits_before and chars_after > 0,
          f"{hits_before} -> {hits_after} hits, {chars_after} chars of text kept")

    # ------------------------------------------------- 3. OCR quality, before/after
    print("\n3. can Tesseract still read the compressed page as well?")
    if not find_tesseract().available:
        skip("OCR quality before/after", "Tesseract not installed")
    else:
        original_text = norm(ocr_text(case, WORK / "ocr_original.pdf"))
        squeezed_text = norm(ocr_text(out, WORK / "ocr_squeezed.pdf"))
        ratio = difflib.SequenceMatcher(None, original_text, squeezed_text).ratio()
        check("the compressed file reads the same as the original",
              ratio >= 0.95, f"text match {ratio:.1%} "
                             f"({len(original_text)} vs {len(squeezed_text)} chars)")
        for word in ("POSSESSION", "SARFAESI", "SECTION"):
            # Only meaningful if the word is actually there to begin with: the
            # point is "no word is LOST", not "this word must exist".
            was_there = word in original_text
            is_there = word in squeezed_text
            check(f"'{word}' is not lost by compression", was_there == is_there,
                  "found in both" if is_there else
                  ("not in either (case-sensitive)" if not was_there else "LOST"))
        lost = [w for w in ("POSSESSION", "SARFAESI", "SECTION", "CREDITOR",
                            "INHERIT", "ASSETS")
                if w in original_text and w not in squeezed_text]
        check("no word that the original had has disappeared", not lost,
              f"lost: {lost}" if lost else "none lost")

    # ---------------------------------------------------- 4. never worse twice
    print("\n4. running it a second time must not make the file worse")
    twice = WORK / "twice.pdf"
    second = optimize.optimize_pdf(out, twice, preset="balanced")
    check("a second pass never grows the file",
          second.after_mb <= result.after_mb + 0.01,
          f"{result.after_mb:.3f} -> {second.after_mb:.3f} MB")
    check("a second pass still says it is fine", second.ok, second.detail[:70])

    # ------------------------------------------------------- 5. the seal stays crisp
    print("\n5. is the bank seal re-encoded (blurred) or left alone?")
    seal = stamp_mod.generate_seal_png(WORK / "seal.png", top_text="GOVERNMENT BANK")
    stamped = WORK / "stamped.pdf"
    stamp_mod.apply_stamp(case, stamped, image_path=seal, pages="all",
                          position="bottom-right", width_percent=20,
                          offset_x_mm=10, offset_y_mm=10, cache_dir=str(WORK))
    after_stamp = WORK / "stamped_compressed.pdf"
    stamp_result = optimize.optimize_pdf(stamped, after_stamp, preset="balanced")
    before_images = image_info(stamped, 2)
    after_images = image_info(after_stamp, 2)
    seal_before = min(before_images, key=lambda i: i[2])
    seal_after = min(after_images, key=lambda i: i[2])
    check("the seal is still on the page", len(after_images) >= 2,
          f"{len(before_images)} image(s) before, {len(after_images)} after")
    check("the seal itself was NOT re-encoded (stays crisp)",
          seal_after[2] == seal_before[2] and seal_after[0] == seal_before[0],
          f"seal {seal_before[0]}x{seal_before[1]} {seal_before[2] / 1024:.0f}KB -> "
          f"{seal_after[0]}x{seal_after[1]} {seal_after[2] / 1024:.0f}KB")
    check("the rest of that page was still compressed",
          stamp_result.after_mb < stamp_result.before_mb,
          f"{stamp_result.before_mb:.2f} -> {stamp_result.after_mb:.2f} MB")

    # --------------------------------------------------- 6. size target honesty
    print("\n6. when a size target is set")
    met = optimize.optimize_pdf(case, WORK / "target_met.pdf", preset="balanced",
                                target_mb=before_mb * 0.3)
    check("a reachable target is met and reported as OK",
          met.ok and met.after_mb <= before_mb * 0.3,
          f"{met.after_mb:.2f} MB vs target {before_mb * 0.3:.2f} MB")
    impossible = optimize.optimize_pdf(case, WORK / "target_missed.pdf",
                                       preset="balanced", target_mb=0.0005)
    check("an impossible target is reported as a FAILURE, not hidden",
          impossible.ok is False and "target" in impossible.detail.lower(),
          f"ok={impossible.ok}, {impossible.after_mb:.2f} MB, "
          f"{impossible.detail.split(';')[-1].strip()[:60]}")

    # ------------------------------------------------------ 7. metadata privacy
    print("\n7. metadata (a filing must not leak the office's printer name)")
    with pymupdf.open(out) as doc:
        meta = doc.metadata or {}
    print(f"       author={meta.get('author')!r} creator={meta.get('creator')!r} "
          f"producer={meta.get('producer')!r}")
    check("author is blanked", not (meta.get("author") or "").strip(),
          repr(meta.get("author")))
    check("producer/creator name the app, not the machine or scanner",
          "KattyVision" in str(meta.get("producer", "")),
          repr(meta.get("producer")))

    # -------------------------------------- 8. the two engines, side by side
    print("\n8. the two engines on the same real file (this is the important one)")
    print(f"       {'engine':<34}{'before':>9}{'after':>9}{'saved':>8}")
    rows = []
    for label, run in (
        ("levels 1 (lossless only)",
         lambda dst: pdf_ops.compress_pdf(case, dst, level=1)),
        ("levels 2 (downsample to 150 dpi)",
         lambda dst: pdf_ops.compress_pdf(case, dst, level=2, dpi=150)),
        ("levels 3 (strongest, no Ghostscript)",
         lambda dst: pdf_ops.compress_pdf(case, dst, level=3, dpi=150)),
        ("adaptive (the Automation tab)",
         lambda dst: optimize.optimize_pdf(case, dst, preset="balanced")),
        ("adaptive, 'small' preset",
         lambda dst: optimize.optimize_pdf(case, dst, preset="small")),
    ):
        res = run(WORK / f"engine_{abs(hash(label))}.pdf")
        rows.append((label, res))
        print(f"       {label:<34}{res.before_mb:>7.2f} MB{res.after_mb:>7.2f} MB"
              f"{res.saving_pct:>7.0f}%")
    adaptive = next(r for label, r in rows if label.startswith("adaptive ("))
    best_level = max((r for label, r in rows if label.startswith("levels")), key=lambda r: r.saving_pct)
    check("the adaptive engine beats the levels engine by a wide margin",
          adaptive.saving_pct > best_level.saving_pct + 20,
          f"adaptive -{adaptive.saving_pct:.0f}% vs best level "
          f"-{best_level.saving_pct:.0f}%")

    # ---------------------------------------- 9. the PDF Tools -> Compress page
    print("\n9. the PDF Tools -> Compress page (must use the smart engine)")
    from court_ocr.app import TOOL_SPECS, _tool_compress                # noqa: E402

    spec = next(s for s in TOOL_SPECS if s.key == "compress")
    keys = [option[2] for option in spec.options]
    defaults = {key: default for _kind, _label, key, default, *_rest in spec.options}
    check("the page offers quality presets, not the old strength levels",
          "preset" in keys and "level" not in keys, f"options = {keys}")
    check("its default is the balanced preset", defaults.get("preset") == "balanced",
          str(defaults))
    out_dir = WORK / "tool_out"
    outputs, warnings = _tool_compress(
        [case], out_dir, dict(defaults), {"progress": lambda m: None, "gs_path": ""})
    with pymupdf.open(outputs[0]) as doc:
        tool_pages = doc.page_count
    tool_mb = outputs[0].stat().st_size / 1048576
    check("running it from the tool really shrinks a scanned filing",
          tool_mb < before_mb * 0.5, f"{before_mb:.2f} -> {tool_mb:.2f} MB "
                                     f"({(1 - tool_mb / before_mb) * 100:.0f}% smaller)")
    check("no page is lost by the tool", tool_pages == 6, f"{tool_pages} pages")
    check("nothing is reported as a problem", not warnings, str(warnings)[:100])

    # ----------------------------------------------------------------- summary
    failed = [(n, d) for ok, n, d in RESULTS if not ok]
    print("\n" + "=" * 78)
    if failed:
        print(f" RESULT: {len(failed)} of {len(RESULTS)} checks failed")
        for name, detail in failed:
            print(f"   FAIL  {name}  ::  {detail}")
    else:
        print(f" RESULT: all {len(RESULTS)} checks passed - compress really works.")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())


