"""Bank seal / signature stamping (replaces the manual "add stamp" step).

Two things can be placed on a page:
  * an image - the Government Bank's scanned seal / signature (transparent PNG)
  * a text block - e.g. "State Bank of India / Authorised Signatory / 26-09-2026"

Everything is drawn locally with PyMuPDF + Pillow; nothing is uploaded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymupdf

from .util import mm2pt

LOGGER = logging.getLogger("court_ocr")
ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT = 0, 1, 2


@dataclass
class StampResult:
    """Outcome of the stamping step."""

    path: Path
    pages_stamped: int = 0
    image_used: str = ""
    text_lines: int = 0
    ok: bool = True
    detail: str = ""


# ---------------------------------------------------------------------------
# Which pages get stamped?
# ---------------------------------------------------------------------------

def parse_page_spec(spec: str | int | list, page_count: int) -> list[int]:
    """Turn a human page selection into sorted 0-based page numbers.

    Accepts ``all``, ``first``, ``last``, ``odd``, ``even``, ``1,3,5-9`` and
    plain integers / lists of integers.
    """
    if isinstance(spec, int):
        spec = str(spec)
    if isinstance(spec, (list, tuple, set)):
        spec = ",".join(str(x) for x in spec)
    text = str(spec or "all").strip().lower()
    if not text or text in ("all", "*", "every"):
        return list(range(page_count))
    if text in ("first", "1"): 
        return [0] if page_count else []
    if text == "last":
        return [page_count - 1] if page_count else []
    if text == "even":
        return [i for i in range(page_count) if (i + 1) % 2 == 0]
    if text == "odd":
        return [i for i in range(page_count) if (i + 1) % 2 == 1]

    pages: set[int] = set()
    for chunk in re.split(r"[,\s;]+", text):
        if not chunk:
            continue
        if "-" in chunk:
            low, _, high = chunk.partition("-")
            try:
                start, end = int(low), int(high)
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            pages.update(range(start - 1, min(end, page_count)))
        else:
            try:
                number = int(chunk)
            except ValueError:
                continue
            if 1 <= number <= page_count:
                pages.add(number - 1)
    return sorted(p for p in pages if 0 <= p < page_count)


# ---------------------------------------------------------------------------
# Preparing the seal image
# ---------------------------------------------------------------------------

_CACHE: dict[tuple, Path] = {}


def prepare_stamp_image(
    src: str | Path,
    *,
    cache_dir: str | Path,
    opacity: float = 1.0,
    remove_white: bool = False,
    white_threshold: int = 235,
) -> Path:
    """Return a ready-to-place PNG: opacity applied, optional white removed.

    The processed file is cached in ``cache_dir`` so repeated runs are instant.
    """
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"stamp image not found: {src}")
    opacity = max(0.02, min(1.0, float(opacity)))
    key = (str(src), round(opacity, 3), bool(remove_white), int(white_threshold),
           int(src.stat().st_mtime))
    cached = _CACHE.get(key)
    if cached and cached.is_file():
        return cached

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"stamp_{abs(hash(key)) % 10**10}.png"

    from PIL import Image

    with Image.open(src) as img:
        img = img.convert("RGBA")
        if remove_white:
            # scanned seals usually sit on a white sheet - key that out
            pixels = img.getdata()
            cut = int(white_threshold)
            rebuilt = [
                (r, g, b, 0) if (r >= cut and g >= cut and b >= cut) else (r, g, b, a)
                for r, g, b, a in pixels
            ]
            img.putdata(rebuilt)
        if opacity < 0.999:
            alpha = img.getchannel("A").point(lambda value: int(value * opacity))
            img.putalpha(alpha)
        img.save(target, "PNG", optimize=True)

    _CACHE[key] = target
    LOGGER.debug("stamp image prepared -> %s", target.name)
    return target


def generate_seal_png(
    out_path: str | Path,
    *,
    top_text: str = "",
    center_text: str = "AUTHORISED SIGNATORY",
    bottom_text: str = "",
    color: tuple[int, int, int] = (17, 17, 153),
    size: int = 900,
) -> Path:
    """Draw a bank-style round seal PNG (placeholder until the real one exists).

    Gives staff a working stand-in so the whole pipeline can be rehearsed
    before the bank hands over its official seal/signature image.
    """
    from PIL import Image, ImageDraw, ImageFont

    def _font(px: int):
        for name in ("arialbd.ttf", "arial.ttf", "calibrib.ttf", "calibri.ttf", "segoeui.ttf"):
            try:
                return ImageFont.truetype(name, px)
            except Exception:
                continue
        return ImageFont.load_default()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size // 22
    for inset, width in ((pad, max(4, size // 90)), (pad + size // 26, max(2, size // 150))):
        draw.ellipse((inset, inset, size - inset, size - inset),
                     outline=color + (230,), width=width)

    def _centered(text: str, y: int, px: int, alpha: int = 235) -> None:
        """Draw centred text, shrinking the font until it fits inside the seal."""
        if not text:
            return
        inner_width = size * 0.62
        font = _font(px)
        while px > 6:
            box = draw.textbbox((0, 0), text, font=font)
            if (box[2] - box[0]) <= inner_width:
                break
            px -= 2
            font = _font(px)
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((size - (box[2] - box[0])) / 2 - box[0], y), text,
                  font=font, fill=color + (alpha,))

    _centered(top_text, int(size * 0.20), size // 12)
    _centered(center_text, int(size * 0.44), size // 10)
    _centered(bottom_text, int(size * 0.62), size // 12)
    img.save(out_path, "PNG", optimize=True)
    LOGGER.info("placeholder seal created: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Applying the stamp
# ---------------------------------------------------------------------------

def apply_stamp(
    doc_path: str | Path,
    out_path: str | Path,
    *,
    image_path: str | Path | None = None,
    pages: str = "all",
    position: str = "bottom-right",
    width_percent: float = 20.0,
    offset_x_mm: float = 12.0,
    offset_y_mm: float = 12.0,
    nudge_x_mm: float = 0.0,
    nudge_y_mm: float = 0.0,
    opacity: float = 0.9,
    rotation: int = 0,
    remove_white: bool = False,
    white_threshold: int = 235,
    cache_dir: str | Path = "work",
    text: dict | None = None,
    progress=None,
) -> StampResult:
    """Place the seal image and/or a text block on the selected pages.

    ``nudge_x_mm`` / ``nudge_y_mm`` shift the finished seal in *screen* terms
    (positive x = right, positive y = down) after the anchor has been applied.
    That is deliberately different from ``offset_*``, which is an inward margin
    and so moves the seal in opposite directions depending on which corner is
    anchored.  The arrow buttons in the preview use the nudge, which means
    right always means right.
    """
    from .pdf_ops import _save_compact   # local import avoids a cycle

    doc_path, out_path = Path(doc_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = StampResult(path=out_path)

    prepared: Path | None = None
    if image_path:
        try:
            prepared = prepare_stamp_image(
                image_path, cache_dir=cache_dir, opacity=opacity,
                remove_white=remove_white, white_threshold=white_threshold,
            )
            result.image_used = str(prepared)
        except Exception as exc:
            LOGGER.error("[stamp] image unavailable (%s) - continuing without seal", exc)
            prepared = None

    text = text or {}
    text_on = bool(text.get("enabled")) and bool(text.get("lines"))
    if prepared is None and not text_on:
        result.ok = False
        result.detail = "no stamp image and no text configured"
        LOGGER.warning("[stamp] nothing to stamp - step skipped")
        return result

    with pymupdf.open(doc_path) as doc:
        targets = parse_page_spec(pages, doc.page_count)
        for index in targets:
            page = doc[index]
            if prepared is not None:
                rect = seal_rect(page, width_percent, _aspect(prepared),
                                 position, offset_x_mm, offset_y_mm,
                                 nudge_x_mm, nudge_y_mm)
                try:
                    page.insert_image(rect, filename=str(prepared),
                                      keep_proportion=True,
                                      rotate=int(rotation) % 360, overlay=True)
                    result.pages_stamped += 1
                except Exception as exc:
                    LOGGER.warning("[stamp] page %d image failed: %s", index + 1, exc)
            if text_on and _stamp_text(page, text):
                result.text_lines += 1
            if progress and (index + 1) % 25 == 0:
                progress(f"[stamp] {index + 1}/{doc.page_count} pages")

        _save_compact(doc, out_path)

    return result


def seal_rect(page, width_percent: float, aspect: float, position: str,
              offset_x_mm: float = 12.0, offset_y_mm: float = 12.0,
              nudge_x_mm: float = 0.0, nudge_y_mm: float = 0.0) -> "pymupdf.Rect":
    """Where the seal lands on a page, in PDF points.

    One function, used by both :func:`apply_stamp` and the on-screen preview,
    so what the user arranges is exactly what gets written.  Sharing the
    calculation is the whole point: two copies of this arithmetic would drift,
    and the preview would quietly start lying to the user.
    """
    from .pdf_ops import anchor_rect

    box = page.rect
    width = max(12.0, box.width * max(1.0, min(90.0, width_percent)) / 100.0)
    height = width * (aspect or 1.0)
    rect = anchor_rect(page, width, height, position,
                       mm2pt(offset_x_mm), mm2pt(offset_y_mm))
    if nudge_x_mm or nudge_y_mm:
        rect = pymupdf.Rect(rect.x0 + mm2pt(nudge_x_mm),
                            rect.y0 + mm2pt(nudge_y_mm),
                            rect.x1 + mm2pt(nudge_x_mm),
                            rect.y1 + mm2pt(nudge_y_mm))
    return _clamp(rect, box)


def _aspect(path: Path) -> float:
    """Height / width ratio of an image (1.0 when unknown)."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            return (img.height / img.width) if img.width else 1.0
    except Exception:
        return 1.0


def _clamp(rect: pymupdf.Rect, box: pymupdf.Rect) -> pymupdf.Rect:
    """Nudge a rectangle back inside the printable area."""
    dx = min(0.0, box.x1 - rect.x1) + max(0.0, box.x0 - rect.x0)
    dy = min(0.0, box.y1 - rect.y1) + max(0.0, box.y0 - rect.y0)
    return pymupdf.Rect(rect.x0 + dx, rect.y0 + dy, rect.x1 + dx, rect.y1 + dy)


def _stamp_text(page: pymupdf.Page, cfg: dict) -> bool:
    """Draw the configured multi-line text stamp; True on success."""
    from .pdf_ops import anchor_rect

    lines = [str(line) for line in cfg.get("lines") or []]
    if cfg.get("include_date"):
        try:
            lines.append(datetime.now().strftime(str(cfg.get("date_format") or "%d-%m-%Y")))
        except Exception:
            lines.append(datetime.now().strftime("%d-%m-%Y"))
    if not lines:
        return False

    box = page.rect
    font_size = float(cfg.get("font_size", 10))
    width = max(60.0, box.width * max(5.0, min(95.0, float(cfg.get("width_percent", 38)))) / 100.0)
    spacing = float(cfg.get("line_spacing", 13))
    rect = anchor_rect(
        page, width, spacing * len(lines) + 6,
        str(cfg.get("position", "top-right")),
        mm2pt(float(cfg.get("offset_x_mm", 8))),
        mm2pt(float(cfg.get("offset_y_mm", 14))),
    )
    rect = _clamp(rect, box)
    try:
        from .pdf_ops import draw_text

        rendered = draw_text(
            page, rect, "\n".join(lines),
            font=str(cfg.get("font", "helv")),
            font_size=font_size,
            color=tuple(cfg.get("color") or (0, 0, 0)),
            align=ALIGN_CENTER,
            line_spacing=spacing,
        )
    except Exception as exc:
        LOGGER.warning("[stamp] text stamp failed: %s", exc)
        return False
    if not rendered:
        LOGGER.warning("[stamp] text stamp did not render on page %d", page.number + 1)
    return rendered
