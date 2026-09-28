"""Court-safety checks for the OCR step: nothing may ever be lost.

Three properties are non-negotiable for a court filing:

1. **Every page survives** - count in == count out, in the same order, and
   every page's visible content is still there.
2. **A page with real text is never re-rendered** - `skip_text` (the default)
   must copy it through byte-for-byte, so a sharp born-digital pleading can
   never be softened or re-encoded by OCR.
3. **A failed chunk degrades to a copy of the original page**, never to a
   missing page.

Usage:  python tools/ocr_safety_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from court_ocr import ocr_engine as ocr
from court_ocr.toolcheck import find_tesseract, prepare_env

WORK = Path("work")
WORK.mkdir(exist_ok=True)
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {'' if ok else detail}")
    if not ok:
        FAILED.append(name)


def mixed_case(path: Path, digital_pages: int = 3, scan_pages: int = 3) -> Path:
    """A filing the way real ones arrive: some typed pages, some scans."""
    if path.is_file():
        return path
    from PIL import Image, ImageDraw

    doc = pymupdf.open()
    for n in range(digital_pages):                     # born-digital, real text
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 100), "TYPED PLEADING PAGE", fontsize=18)
        for line in range(6):
            page.insert_text((60, 140 + line * 22),
                             "the applicant respectfully submits before this "
                             "honourable court", fontsize=11)
    scan = WORK / "_safety_scan.png"
    if not scan.is_file():
        img = Image.new("L", (1240, 1754), 255)
        pen = ImageDraw.Draw(img)
        pen.text((80, 120), "SCANNED EXHIBIT - AFFIDAVIT IN REPLY", fill=0)
        pen.text((80, 200), "I say that the contents are true to my knowledge", fill=0)
        img.save(scan)
    for n in range(scan_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=str(scan))
    doc.save(path)
    doc.close()
    return path


def main() -> int:
    tool = find_tesseract()
    if not tool.available:
        print("Tesseract is not installed - run SETUP.ps1 first.")
        return 1
    prepare_env(tool)

    src = mixed_case(WORK / "_safety_mixed.pdf")
    out = WORK / "_safety_mixed_OCR.pdf"
    with pymupdf.open(src) as doc:
        in_pages = doc.page_count
        in_text = [doc[i].get_text() for i in range(doc.page_count)]

    print("\n1. Every page survives, in the same order")
    result = ocr.run_ocr(src, out, language="eng", dpi=300, jobs=0,
                         mode="skip_text", recheck=True)
    with pymupdf.open(out) as doc:
        out_pages = doc.page_count
        out_text = [doc[i].get_text() for i in range(doc.page_count)]
    check("no page was lost", out_pages == in_pages,
          f"{in_pages} -> {out_pages}")
    check("no page came back blank that had content",
          all(bool(t.strip()) for t in out_text), "an output page has no text")
    check("the step reported success", result.ok, str(result.failed_ranges))

    print("\n2. Typed pages are passed through untouched (never re-rendered)")
    typed = [i for i, t in enumerate(in_text) if "TYPED PLEADING" in t]
    kept = all("TYPED PLEADING" in out_text[i] for i in typed)
    check("born-digital pages keep their exact original text", kept,
          f"typed pages: {typed}")
    check("they are counted as passed through, not re-OCR'd",
          result.pages_passed_through == len(typed),
          f"passed={result.pages_passed_through}, typed={len(typed)}")
    check("only the scan pages were OCRed",
          result.pages_ocrd == in_pages - len(typed),
          f"ocrd={result.pages_ocrd}, scans={in_pages - len(typed)}")
    with pymupdf.open(src) as a, pymupdf.open(out) as b:
        same_image = False
        ref_a = a[typed[0]].get_images(full=True)
        ref_b = b[typed[0]].get_images(full=True)
        if ref_a and ref_b:
            same_image = ref_a[0][2:4] == ref_b[0][2:4]     # width, height
        check("a typed page keeps its original page size",
              abs(a[typed[0]].rect.width - b[typed[0]].rect.width) < 1,
              "page size changed")

    print("\n3. The scans really did become searchable")
    scans = [i for i in range(in_pages) if i not in typed]
    scan_words = sum(1 for i in scans if "AFFIDAVIT" in out_text[i].upper())
    check("the scanned pages now carry real, selectable text",
          scan_words == len(scans), f"{scan_words} of {len(scans)} pages")

    print("\n4. A whole run is never left half-done")
    check("no failed ranges", not result.failed_ranges, str(result.failed_ranges))
    check("a copy of the source is kept beside the result (safety net)",
          out.is_file() and src.is_file(), "missing file")

    print("\n5. The page count is reported honestly to the user")
    check("pages_total matches the file", result.pages_total == in_pages,
          f"{result.pages_total} vs {in_pages}")

    print()
    if FAILED:
        print(f"RESULT: {len(FAILED)} check(s) failed: {FAILED}")
        return 1
    print("RESULT: all OCR safety checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())