"""Generate the app logo + Windows icon used by the desktop shortcut and window.

Design: a navy rounded tile holding a white legal document with a folded corner
and a gold embossed seal - readable even at 16 x 16 pixels.

Produces:
    assets/logo.png      512 px master
    assets/logo_64.png   small raster for the window header
    assets/app.ico       multi-resolution Windows icon

Usage:
    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

NAVY = (11, 42, 91)
NAVY_LIGHT = (23, 68, 138)
PAPER = (255, 255, 255)
INK = (176, 190, 212)
GOLD = (212, 160, 23)
GOLD_DARK = (166, 122, 12)
SS = 4                      # supersampling factor for smooth edges


def _rounded(draw: ImageDraw.ImageDraw, box, radius, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def render(size: int = 1024) -> Image.Image:
    """Draw the logo at ``size`` pixels (RGBA)."""
    canvas = size * SS
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ---- tile
    pad = int(canvas * 0.055)
    _rounded(draw, (pad, pad, canvas - pad, canvas - pad), int(canvas * 0.185), NAVY)
    # diagonal sheen for depth
    sheen = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).polygon(
        [(pad, canvas - pad), (canvas - pad, pad), (canvas - pad, canvas - pad)], fill=NAVY_LIGHT + (110,)
    )
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (pad, pad, canvas - pad, canvas - pad), radius=int(canvas * 0.185), fill=255
    )
    img.paste(sheen, (0, 0), Image.composite(sheen.split()[3], Image.new("L", (canvas, canvas), 0), mask))

    # ---- document sheet
    left, top = int(canvas * 0.215), int(canvas * 0.150)
    right, bottom = int(canvas * 0.720), int(canvas * 0.865)
    fold = int(canvas * 0.150)
    draw.polygon(
        [(left, top), (right - fold, top), (right, top + fold), (right, bottom), (left, bottom)],
        fill=PAPER,
    )
    draw.rounded_rectangle((left, top, right, bottom), radius=int(canvas * 0.018), fill=PAPER)
    # folded corner
    draw.polygon([(right - fold, top), (right, top + fold), (right - fold, top + fold)],
                 fill=(206, 216, 232))
    # text lines
    line_left = left + int(canvas * 0.055)
    line_right = right - int(canvas * 0.055)
    y = top + int(canvas * 0.215)
    for index, width_ratio in enumerate((1.0, 0.86, 0.95, 0.62)):
        height = int(canvas * 0.030)
        draw.rounded_rectangle(
            (line_left, y, line_left + int((line_right - line_left) * width_ratio), y + height),
            radius=height // 2, fill=INK,
        )
        y += int(canvas * 0.082)
    # signature squiggle
    y += int(canvas * 0.010)
    draw.line(
        [(line_left, y + int(canvas * 0.030)), (line_left + int(canvas * 0.10), y),
         (line_left + int(canvas * 0.18), y + int(canvas * 0.034)),
         (line_left + int(canvas * 0.30), y + int(canvas * 0.004))],
        fill=NAVY, width=max(2, int(canvas * 0.014)), joint="curve",
    )

    # ---- gold seal
    cx, cy = int(canvas * 0.720), int(canvas * 0.725)
    radius = int(canvas * 0.225)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 fill=GOLD, outline=GOLD_DARK, width=max(2, int(canvas * 0.016)))
    inner = int(radius * 0.74)
    draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner),
                 outline=GOLD_DARK + (255,), width=max(1, int(canvas * 0.009)))
    # check mark
    check = [
        (cx - int(radius * 0.44), cy + int(radius * 0.03)),
        (cx - int(radius * 0.10), cy + int(radius * 0.34)),
        (cx + int(radius * 0.46), cy - int(radius * 0.34)),
    ]
    draw.line(check, fill=PAPER, width=max(3, int(canvas * 0.030)), joint="curve")

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = render(1024)

    logo = ASSETS / "logo.png"
    master.resize((512, 512), Image.LANCZOS).save(logo, "PNG", optimize=True)
    master.resize((64, 64), Image.LANCZOS).save(ASSETS / "logo_64.png", "PNG", optimize=True)

    # transparent margin version feels tighter in the window header
    ico = ASSETS / "app.ico"
    master.resize((256, 256), Image.LANCZOS).save(
        ico, format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    for path in (logo, ASSETS / "logo_64.png", ico):
        print(f"wrote {path.relative_to(ROOT)}  ({path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
