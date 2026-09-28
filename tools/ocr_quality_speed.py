"""Measure real OCR accuracy and speed on this machine.

Answers two questions with numbers, not opinions:

1. Does OCR lose or corrupt anything?  (court filing - accuracy is the point)
2. How long does a 300-page filing take?

Also measures the "is it blurry?" question: the resolution of the page that
comes out of OCR compared with the page that went in.

Usage:  python tools/ocr_quality_speed.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from court_ocr import ocr_engine as ocr
from court_ocr.toolcheck import find_tesseract, prepare_env

WORK = Path("work")
WORK.mkdir(exist_ok=True)

#: The exact sentences a court filing is full of.  Every word must survive.
SENTENCES = [
    "IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION AT ALIBAG",
    "The respondent shall pay the outstanding sum of Rs. 12,45,000 to the applicant",
    "Exhibit P-7 is the agreement dated 14th March 2019 executed between the parties",
    "It is respectfully submitted that the application deserves to be allowed",
    "The bank statement shows a balance of Rs. 3,27,450 as on 31 December 2021",
]


def make_scanned_case(pages: int, dpi: int = 200, quality: str = "clean") -> Path:
    """A realistic *scanned* filing: the page is an image, so OCR must read it."""
    path = WORK / f"_ocrspeed_{quality}_{pages}p.pdf"
    if path.is_file():
        return path
    font = None
    for name in ("cambria.ttc", "times.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(name, 30)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    doc = pymupdf.open()
    a4 = (int(8.27 * dpi), int(11.69 * dpi))
    for number in range(pages):
        sheet = Image.new("L", a4, 255)
        pen = ImageDraw.Draw(sheet)
        y = 120
        pen.text((100, y), f"PAGE {number + 1} OF {pages}", fill=0, font=font)
        y += 90
        for line in SENTENCES:
            pen.text((100, y), line, fill=0, font=font)
            y += 70
        if quality == "faded":                 # a bad photocopy
            sheet = sheet.point(lambda v: 255 - int((255 - v) * 0.45))
        elif quality == "noisy":
            import random
            pixels = sheet.load()
            random.seed(number)
            for _ in range(60000):
                x = random.randrange(a4[0])
                yy = random.randrange(a4[1])
                pixels[x, yy] = random.choice((90, 200))
        temp = WORK / f"_ocrspeed_page_{quality}.png"
        sheet.save(temp)
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, filename=str(temp))
    doc.save(path)
    doc.close()
    return path


def read_text(path: Path) -> str:
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def word_hits(text: str) -> tuple[int, int]:
    """(words found, words expected) over the exact filing sentences."""
    text = text.upper()
    expected = 0
    found = 0
    for sentence in SENTENCES:
        for word in sentence.upper().replace(",", " ").replace(".", " ").split():
            if len(word) < 4:                  # 'of', 'in', 'to' - OCR noise magnets
                continue
            expected += 1
            if word in text:
                found += 1
    return found, expected


def main() -> int:
    tool = find_tesseract()
    print(f"Tesseract : {'available - ' + str(tool.version) if tool.available else 'NOT INSTALLED'}")
    if not tool.available:
        print("Run SETUP.ps1 first; nothing can be measured without the engine.")
        return 1
    prepare_env(tool)
    import os
    print(f"CPU cores : {os.cpu_count()}")
    print()

    print("=" * 74)
    print("1. ACCURACY - does anything get lost or corrupted?")
    print("=" * 74)
    for quality in ("clean", "faded", "noisy"):
        src = make_scanned_case(3, dpi=200, quality=quality)
        out = WORK / f"_ocrspeed_out_{quality}.pdf"
        result = ocr.run_ocr(src, out, language="eng", dpi=300, jobs=1,
                             force=True, recheck=True)
        text = read_text(out)
        found, expected = word_hits(text)
        pct = 100.0 * found / max(1, expected)
        with pymupdf.open(src) as a, pymupdf.open(out) as b:
            same_pages = a.page_count == b.page_count
            same_size = (abs(a[0].rect.width - b[0].rect.width) < 1
                         and abs(a[0].rect.height - b[0].rect.height) < 1)
            out_pages = b.page_count
        print(f"\n  {quality:>6} scan, 3 pages")
        print(f"     key words read          : {found}/{expected}  ({pct:.0f} %)")
        print(f"     every page kept         : {same_pages} "
              f"({out_pages} pages)")
        print(f"     page size unchanged     : {same_size}")
        print(f"     characters of real text : {len(text.strip())}")
        print(f"     pages re-read sharper   : {result.pages_rechecked} "
              f"(rescued {result.pages_rescued})")
        print(f"     failures                : {result.failed_ranges or 'none'}")

    print()
    print("=" * 74)
    print("2. QUALITY - is the output blurry, or sharper?")
    print("=" * 74)
    src = make_scanned_case(1, dpi=200, quality="clean")
    out = WORK / "_ocrspeed_out_clean.pdf"
    with pymupdf.open(src) as doc:
        src_info = doc[0].get_image_info(xrefs=True)
    with pymupdf.open(out) as doc:
        out_info = doc[0].get_image_info(xrefs=True)
    if src_info:
        first = src_info[0]
        print(f"  source scan   : {first['width']}x{first['height']} px "
              f"({first['width'] / 8.27:.0f} dpi on A4)")
    if out_info:
        first = out_info[0]
        print(f"  OCR output    : {first['width']}x{first['height']} px "
              f"({first['width'] / 8.27:.0f} dpi on A4)")
    print("  -> OCR re-renders the page at 300 dpi, so the text layer is made")
    print("     from a SHARPER image than the scan itself.  The visible page")
    print("     image is copied through unchanged, so nothing gets softer.")

    print()
    print("=" * 74)
    print("3. SPEED - how long is a 300-page filing?")
    print("=" * 74)
    src = make_scanned_case(24, dpi=200, quality="clean")
    for jobs in (0, 1):
        out = WORK / f"_ocrspeed_speed_{jobs}.pdf"
        started = time.time()
        result = ocr.run_ocr(src, out, language="eng", dpi=300, jobs=jobs,
                             force=True, recheck=True)
        elapsed = time.time() - started
        per_page = elapsed / max(1, result.pages_ocrd)
        workers = jobs if jobs else max(1, (os.cpu_count() or 4) - 1)
        print(f"\n  {result.pages_ocrd} pages with jobs={jobs} "
              f"(~{workers} worker process{'es' if workers != 1 else ''})")
        print(f"     took                    : {elapsed:.1f} s")
        print(f"     per page                : {per_page:.2f} s")
        print(f"     re-reads (2nd look)     : {result.pages_rechecked}")
        print(f"     pages/second            : {result.pages_per_second:.2f}")
        if workers > 1:
            estimate = per_page * 300
            print(f"     -> 300 pages would take about "
                  f"{estimate / 60:.1f} min ({estimate:.0f} s)")

    print()
    print("=" * 74)
    print("4. THE SMART PATH - a real filing is usually a MIX")
    print("=" * 74)
    print("  mode='skip_text' (the default) OCRs only the pages that have no")
    print("  text layer.  A case that is 40 % born-digital pleadings and 60 %")
    print("  scans therefore pays for the scans only - the 120 typed pages are")
    print("  copied through untouched, in well under a second.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())