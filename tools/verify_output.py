"""Verify a finished filing: page numbers, seal, OCR text, rotation, sizes.

    python work/_verify.py "output\\SA 245 of 2026\\SA 245 of 2026_Final.pdf"

Also renders a few pages to work/preview_*.png for a visual check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

path = Path(sys.argv[1] if len(sys.argv) > 1 else
            r"output\SA 245 of 2026\SA 245 of 2026_Final.pdf")
doc = pymupdf.open(path)
print(f"file      : {path}")
print(f"pages     : {doc.page_count}")
print(f"size      : {path.stat().st_size / 1024 / 1024:.2f} MB")
print(f"metadata  : {doc.metadata.get('producer')!r} / title={doc.metadata.get('title')!r}")
print(f"bookmarks : {len(doc.get_toc() or [])}")
for item in (doc.get_toc() or [])[:12]:
    print(f"            L{item[0]} p{item[2]:>3}  {item[1]}")
print(f"encrypted : {doc.is_encrypted}")
print("-" * 100)

problems: list[str] = []
for index, page in enumerate(doc):
    text = page.get_text().strip()
    numbers = [w for w in text.split() if w.isdigit()]
    has_number = "Page" in text and "of" in text
    images = page.get_images(full=True)
    rotated = page.rotation % 360
    print(f"page {index + 1:>3} | {page.rect.width:6.1f}x{page.rect.height:6.1f} "
          f"| rot {rotated:>3} | chars {len(text):>5} | images {len(images):>2} "
          f"| numbered {'yes' if has_number else 'NO '} | tail={numbers[-2:] if numbers else []}")
    if not text:
        problems.append(f"page {index + 1}: no text at all")
    if not has_number and index > 0:
        problems.append(f"page {index + 1}: 'Page X of Y' not found")

print("-" * 100)
print("OCR PROOF - text recovered from the scanned pages (searchable):")
for wanted in (4, 5, 6):
    if wanted <= doc.page_count:
        snippet = " ".join(doc[wanted - 1].get_text().split())[:150]
        print(f"  page {wanted}: {snippet}")

print("-" * 100)
needle = "SARFAESI"
hits = sum(len(doc[i].search_for(needle)) for i in range(doc.page_count))
print(f"search_for('{needle}') across document: {hits} hit(s) -> "
      f"{'searchable OK' if hits else 'NOT SEARCHABLE'}")

found_page_label = False
for i in range(doc.page_count):
    if doc[i].search_for("Page 1 of"):
        found_page_label = True
print(f"pagination label found: {found_page_label}")

print("-" * 100)
for index in (0, 3, doc.page_count - 1):
    pix = doc[index].get_pixmap(dpi=110)
    out = Path("work") / f"preview_p{index + 1}.png"
    pix.save(out)
    print(f"preview written: {out}")

print("-" * 100)
print("PROBLEMS:", problems or "none")
doc.close()
