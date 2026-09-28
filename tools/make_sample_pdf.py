"""Create a realistic SARFAESI-style test folder so the pipeline can be tried.

Builds a mixture of document types that mirrors the real workflow:
  * born-digital PDFs (real text layer, must NOT be re-OCR'd)
  * "scanned" pages (rasterised text + speckle noise, MUST be OCR'd)
  * a rotated scan (exercises rotation baking)
  * a landscape PNG exhibit

Usage:
    python tools/make_sample_pdf.py
    python tools/make_sample_pdf.py --out "input/SAMPLE CASE" --pages 40
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
A4 = (595.276, 841.89)

PARAS = [
    "That the borrower has failed to repay the secured debt despite repeated demands.",
    "The Authorised Officer has therefore taken possession of the secured assets under "
    "Section 13(4) of the SARFAESI Act, 2002.",
    "The secured creditor is entitled to realise the secured assets under Section 13(7).",
    "Notice under Section 13(2) of the SARFAESI Act, 2002 was duly served upon the borrower.",
    "The account has been classified as a Non-Performing Asset in accordance with the RBI "
    "guidelines issued from time to time.",
    "The mortgaged immovable property bearing Survey No. 245/2 situated at Village Raigad "
    "is charged in favour of the secured creditor.",
    "It is respectfully submitted that the present application is within limitation.",
    "The borrower has not availed of any alternative remedy as contemplated under the Act.",
]


def _digital_page(doc: pymupdf.Document, heading: str, index: int, pages: int) -> None:
    """A normal born-digital page carrying a genuine text layer."""
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((60, 80), "BEFORE THE DISTRICT JUDGE, RAIGAD",
                     fontsize=13, fontname="hebo")
    page.insert_text((60, 104), heading.upper(), fontsize=11, fontname="hebo")
    page.insert_text((60, 122), "Misc. Application No. 245 of 2026", fontsize=10)
    y = 160
    random.seed(index)
    for _ in range(12):
        page.insert_text((60, y), random.choice(PARAS)[:88], fontsize=10)
        y += 22
    page.insert_text((60, 800), f"Page {index} of {pages} - digital copy", fontsize=9)


def _scanned_page(doc: pymupdf.Document, heading: str, index: int, pages: int,
                  rotate: int = 0, noise: int = 900) -> None:
    """A page that exists only as pixels - exactly what OCR must rescue."""
    temp = pymupdf.open()
    page = temp.new_page(width=A4[0], height=A4[1])
    page.insert_text((60, 90), heading.upper(), fontsize=12, fontname="hebo")
    page.insert_text((60, 112), "Misc. Application No. 245 of 2026", fontsize=10)
    y = 150
    random.seed(1000 + index)
    for _ in range(14):
        page.insert_text((60, y), random.choice(PARAS)[:86], fontsize=10)
        y += 21
    page.insert_text((60, 810), f"Scanned page {index} of {pages}", fontsize=9)
    pix = page.get_pixmap(dpi=200)
    temp.close()

    if noise:                              # real scans are never clean
        raw = bytearray(pix.samples)
        step = max(1, len(raw) // noise)
        for offset in range(0, len(raw) - 3, step):
            raw[offset] = raw[offset + 1] = raw[offset + 2] = 40
        pix = pymupdf.Pixmap(pix.colorspace, pix.width, pix.height, bytes(raw), pix.alpha)
    pix.set_dpi(140, 140)                  # "scan resolution" metadata

    fresh = doc.new_page(width=A4[0], height=A4[1])
    fresh.insert_image(fresh.rect, stream=pix.tobytes("jpeg", jpg_quality=80),
                       keep_proportion=True)
    if rotate:
        fresh.set_rotation(rotate)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create sample SARFAESI documents")
    parser.add_argument("--out", default=str(ROOT / "input" / "SAMPLE CASE"),
                        help="folder to write the sample case into")
    parser.add_argument("--pages", type=int, default=0,
                        help="number of extra scanned pages to add")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # 1. born-digital index note
    doc = pymupdf.open()
    _digital_page(doc, "Index of Documents Filed", 1, 1)
    doc.save(out / "01 Index of Documents.pdf", garbage=4, deflate=True)
    doc.close()

    # 2. digital notice, second page rotated (exercises rotation baking)
    doc = pymupdf.open()
    _digital_page(doc, "Notice under Section 13(2)", 1, 2)
    _digital_page(doc, "Notice under Section 13(2) continued", 2, 2)
    doc[1].set_rotation(90)
    doc.save(out / "02 SARFAESI Notice 13(2).pdf", garbage=4, deflate=True)
    doc.close()

    # 3. scanned material - the part that used to cost half an hour on iLovePDF
    extra = max(0, int(args.pages))
    doc = pymupdf.open()
    total = 3 + extra
    _scanned_page(doc, "Possession Notice under Section 13(4)", 1, total)
    _scanned_page(doc, "Panchnama and Inventory of Secured Assets", 2, total)
    _scanned_page(doc, "Publication of Notice in Newspapers", 3, total)
    for offset in range(extra):
        _scanned_page(doc, f"Annexure - Loan Ledger Extract {offset + 1}", 4 + offset, total)
    doc.save(out / "03 Scanned Notices and Annexures.pdf", garbage=4, deflate=True)
    doc.close()

    # 4. landscape image exhibit
    photo = pymupdf.open()
    page = photo.new_page(width=1123, height=794)
    page.draw_rect(page.rect, color=(0, 0, 0), fill=(0.85, 0.88, 0.92), width=2)
    page.insert_text((60, 120), "PHOTOGRAPH OF SECURED ASSET", fontsize=30, fontname="hebo")
    page.insert_text((60, 190),
                     "Survey No. 245/2, Village Raigad - north boundary wall", fontsize=16)
    page.get_pixmap(dpi=110).save(out / "04 Photograph of Secured Asset.png")
    photo.close()

    files = sorted(out.iterdir())
    print(f"created {len(files)} sample file(s) in: {out}")
    for item in files:
        print(f"   {item.name:52s} {item.stat().st_size / 1024:9.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
