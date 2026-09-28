"""Build the KattyVision brand assets from the user's own logos.

Source:  brand/kattyvision.png        (the small mark)
         brand/kattyvissionfull.png   (the full wordmark)

Produces:
    assets/app.ico      multi-resolution Windows icon (taskbar, window, shortcut)
    assets/logo.png     512 px square mark for the hero band
    assets/logo_64.png  64 px mark for small uses

The brand art is used as-is: it is padded onto a transparent square, never
recoloured, so the identity is exactly the one the owner drew.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
BRAND_DIR = ROOT / "brand"


def square_mark(source: Path, size: int, pad_ratio: float = 0.04) -> Image.Image:
    """Fit the logo onto a transparent square, centred, with a small margin."""
    with Image.open(source) as raw:
        art = raw.convert("RGBA")
    art.thumbnail((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - art.width) // 2
    y = (size - art.height) // 2
    canvas.paste(art, (x, y), art)
    return canvas


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    candidates = [BRAND_DIR / "kattyvision.png", BRAND_DIR / "kattyvissionfull.png"]
    source = next((p for p in candidates if p.is_file()), None)
    if source is None:
        print("no brand logo found in brand/ - nothing to do")
        return 1

    master = square_mark(source, 512)

    logo = ASSETS / "logo.png"
    master.save(logo, "PNG", optimize=True)
    master.resize((64, 64), Image.Resampling.LANCZOS).save(
        ASSETS / "logo_64.png", "PNG", optimize=True)

    ico = ASSETS / "app.ico"
    master.save(ico, format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                       (128, 128), (256, 256)])

    for path in (logo, ASSETS / "logo_64.png", ico):
        print(f"wrote {path.relative_to(ROOT)}  ({path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
