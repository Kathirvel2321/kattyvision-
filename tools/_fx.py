import sys, os
from pathlib import Path
sys.path.insert(0, ".")
import pymupdf
from PIL import Image
from court_ocr import app as A
work = Path("work"); work.mkdir(exist_ok=True)
log = open("work/_fixlog.txt", "w", encoding="utf-8", buffering=1)
def p(*a):
    s = " ".join(str(x) for x in a); print(s); log.write(s + "\n")

# the exact _tool_compress path that crashed in the screenshot
src = work / "_fx.pdf"
if not src.is_file():
    img = Image.new("RGB", (1400, 990))
    px = img.load()
    for y in range(990):
        for x in range(0, 1400, 3):
            r, g, b = (x*9) % 256, (y*13) % 256, ((x+y)//3) % 256
            px[x, y] = r; px[x+1, y] = g; px[x+2, y] = b
    img.save(work / "_fx.png")
    d = pymupdf.open()
    for n in range(6):
        pg = d.new_page(width=595, height=842)
        pg.insert_text((70, 90), f"PAGE {n+1}", fontsize=16)
        pg.insert_image(pymupdf.Rect(40, 120, 555, 800), filename=str(work / "_fx.png"))
    d.save(src); d.close()
out = work / "fxout"; out.mkdir(exist_ok=True)
ctx = {"progress": lambda m: None, "gs_path": ""}

for target in (0.0, 1.5):
    for level in (1, 2, 3):
        opts = {"level": level, "target_mb": target, "dpi": 150}
        try:
            outs, warns = A._tool_compress([src], out, opts, ctx)
            p(f"target={target} level={level}: OK  files={len(outs)} warnings={warns}")
        except Exception as e:
            p(f"target={target} level={level}: CRASH {type(e).__name__}: {e}")
log.close()