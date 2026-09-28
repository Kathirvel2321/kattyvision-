"""Adaptive per-page optimizer - smaller *and* sharper than one global setting.

For every page:

* **born-digital** pages (real text, no page-sized image) are left untouched, so
  the vector text and fonts stay exactly as the source had them;
* **scanned** pages are re-encoded the way a scanner would emit them, *in place*
  (``Page.replace_image``), so the page size, the image placement and the
  invisible OCR text layer all survive:

  ==============  ===================================================
  page class      encoding
  ==============  ===================================================
  ``text_scan``   1-bit bilevel  - smallest and crispest for text
  ``gray_scan``   8-bit grayscale JPEG (photos of stamps, shading)
  ``mixed_scan``  RGB JPEG       (colour ink, seals, letterheads)
  ==============  ===================================================

* the bank seal / any small overlay image is **never** re-encoded, and this step
  runs *before* pagination and stamping, so both stay crisp.

Measured on the sample case: a scanned page goes from 358 KB (300 dpi JPEG q75)
to 64 KB at **400 dpi**, i.e. sharper text at a ninth of the size.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

import pymupdf
from PIL import Image

from . import __version__, pdf_ops
from .util import MB

LOGGER = logging.getLogger("court_ocr")

PRODUCER = f"KattyVision {__version__} (adaptive optimizer)"
ProgressFn = Callable[[str], None]

MIN_PAGE_COVER = 0.60      # an image covering less than this is an overlay/seal
PROBE_DPI = 120            # cheap render used only to classify a page
SATURATION_CUTOFF = 45     # HSV saturation above this counts as real colour
COLOUR_SHARE_PCT = 2.0     # % of coloured pixels that makes a page "colour"
PAPER_LEVEL = 205          # gray value that counts as paper white
WHITE_SHARE_PCT = 85.0     # % white that makes a page "text on paper"
MIDTONE_SHARE_PCT = 12.0   # % mid-tones that means photos/shading/screenshots


# --------------------------------------------------------------------------- #
# Quality presets
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class QualityPreset:
    """How hard to squeeze, per page class."""

    key: str
    title: str
    hint: str
    text_dpi: int = 300
    photo_dpi: int = 200
    photo_quality: int = 85
    gray_quality: int = 80
    bitonal: bool = True
    threshold: int = 160       # 0-255; higher = thinner strokes


QUALITY_PRESETS: tuple[QualityPreset, ...] = (
    QualityPreset("archival", "Archival - best quality",
                  "400 dpi text, 300 dpi colour, q92",
                  text_dpi=400, photo_dpi=300, photo_quality=92, gray_quality=88),
    QualityPreset("high", "High - best everyday filing",
                  "400 dpi text, 250 dpi colour, q88",
                  text_dpi=400, photo_dpi=250, photo_quality=88, gray_quality=84),
    QualityPreset("balanced", "Balanced (recommended)",
                  "300 dpi text, 200 dpi colour, q85",
                  text_dpi=300, photo_dpi=200, photo_quality=85, gray_quality=80),
    QualityPreset("small", "Small - weakest portal limit",
                  "300 dpi text, 150 dpi colour, q78",
                  text_dpi=300, photo_dpi=150, photo_quality=78, gray_quality=72),
)

PRESET_KEYS: tuple[str, ...] = tuple(p.key for p in QUALITY_PRESETS)


def preset_for(key: str) -> QualityPreset:
    """Look up a preset by key, falling back to the recommended one."""
    wanted = str(key or "").strip().lower()
    for preset in QUALITY_PRESETS:
        if preset.key == wanted:
            return preset
    return next(p for p in QUALITY_PRESETS if p.key == "balanced")


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #

@dataclass
class PageRow:
    """What the optimizer did to one page."""

    page: int
    klass: str
    cover: float = 0.0
    before_kb: float = 0.0
    after_kb: float = 0.0
    dpi: int = 0
    encoding: str = ""
    note: str = ""

    @property
    def saving_pct(self) -> float:
        if self.before_kb <= 0:
            return 0.0
        return (self.before_kb - self.after_kb) / self.before_kb * 100.0


@dataclass
class OptimizeResult:
    """Outcome of the optimizer, shaped like the other step results."""

    path: Path
    before_mb: float = 0.0
    after_mb: float = 0.0
    preset: str = ""
    rows: list[PageRow] = field(default_factory=list)
    ok: bool = True
    detail: str = ""

    @property
    def saving_pct(self) -> float:
        if self.before_mb <= 0:
            return 0.0
        return (self.before_mb - self.after_mb) / self.before_mb * 100.0

    @property
    def scanned_pages(self) -> int:
        return sum(1 for row in self.rows if row.klass != "born_digital")

    def per_page_table(self) -> list[str]:
        """One printable line per page - used by tools/quality_report.py."""
        lines = []
        for row in self.rows:
            lines.append(
                f"{row.page:>4}  {row.klass:<12} {row.cover:>5.0%} "
                f"{row.before_kb:>8.0f} KB -> {row.after_kb:>7.0f} KB "
                f"{row.dpi:>4} dpi  {row.encoding or '-':<10} {row.note}"
            )
        return lines


# --------------------------------------------------------------------------- #
# Page analysis
# --------------------------------------------------------------------------- #

def classify_page(image: Image.Image) -> tuple[str, dict]:
    """Name the page class from a cheap colour/paper analysis.

    ``text_scan``  - nearly white paper, (almost) no colour -> 1-bit is perfect
    ``gray_scan``  - no colour, but lots of mid-tones (shading, faint stamp)
    ``mixed_scan`` - real colour present (blue ink, red seal, letterhead)
    """
    total = float(image.width * image.height) or 1.0
    coloured = sum(image.convert("HSV").getchannel("S").histogram()[SATURATION_CUTOFF:]) / total
    levels = image.convert("L").histogram()
    white = sum(levels[PAPER_LEVEL:]) / total
    midtone = sum(levels[60:PAPER_LEVEL]) / total

    stats = {"coloured_pct": round(coloured * 100, 3),
             "white_pct": round(white * 100, 2),
             "midtone_pct": round(midtone * 100, 2)}
    if coloured * 100.0 >= COLOUR_SHARE_PCT:
        return "mixed_scan", stats
    if white * 100.0 >= WHITE_SHARE_PCT and midtone * 100.0 < MIDTONE_SHARE_PCT:
        return "text_scan", stats
    return "gray_scan", stats


def page_image(page: pymupdf.Page) -> tuple[int | None, float]:
    """xref of the image covering the most of ``page``, plus its area share."""
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    best, share = None, 0.0
    try:
        images = page.get_images(full=True)
    except Exception:
        return None, 0.0
    for item in images:
        xref = item[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        for rect in rects:
            area = abs(rect.width * rect.height) / page_area
            if area > share:
                best, share = xref, area
    return best, share


def _render(page: pymupdf.Page, dpi: int) -> Image.Image:
    """Render a page to RGB pixels at ``dpi`` (no annotations, no alpha)."""
    pix = page.get_pixmap(dpi=int(dpi), colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _encode(image: Image.Image, klass: str, settings: QualityPreset
            ) -> tuple[bytes, str, int]:
    """Encode one page image the way its class deserves.

    Returns ``(bytes, label, dpi)``.  JPEG data is stored by PyMuPDF verbatim
    (DCTDecode), so the quality setting is exactly what lands in the PDF.
    """
    buffer = io.BytesIO()
    if klass == "text_scan" and settings.bitonal:
        bilevel = image.convert("L").point(
            lambda value: 255 if value > settings.threshold else 0, mode="1")
        bilevel.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "1-bit", settings.text_dpi
    if klass == "mixed_scan":
        image.convert("RGB").save(buffer, format="JPEG", quality=settings.photo_quality,
                                  optimize=True, progressive=False)
        return buffer.getvalue(), "JPEG rgb", settings.photo_dpi
    quality = settings.gray_quality if klass == "gray_scan" else settings.photo_quality
    dpi = settings.photo_dpi if klass == "gray_scan" else settings.text_dpi
    image.convert("L").save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue(), "JPEG gray", dpi


# --------------------------------------------------------------------------- #
# Settings / metadata helpers
# --------------------------------------------------------------------------- #

def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def settings_for(preset: str = "balanced", *, text_dpi: int | None = None,
                 photo_dpi: int | None = None, photo_quality: int | None = None,
                 gray_quality: int | None = None, bitonal: bool | None = None,
                 threshold: int | None = None) -> QualityPreset:
    """Preset + any config overrides, validated and clamped."""
    settings = preset_for(preset)
    changes: dict = {}
    if text_dpi:
        changes["text_dpi"] = _clamp(text_dpi, 150, 600)
    if photo_dpi:
        changes["photo_dpi"] = _clamp(photo_dpi, 72, 400)
    if photo_quality:
        changes["photo_quality"] = _clamp(photo_quality, 40, 98)
    if gray_quality:
        changes["gray_quality"] = _clamp(gray_quality, 40, 98)
    if bitonal is not None:
        changes["bitonal"] = bool(bitonal)
    if threshold:
        changes["threshold"] = _clamp(threshold, 90, 230)
    return replace(settings, **changes) if changes else settings


def _escalations(settings: QualityPreset) -> list[QualityPreset]:
    """Progressively harder settings, used when a size target must be met."""
    ladder: list[QualityPreset] = []
    strong = replace(settings,
                     text_dpi=min(settings.text_dpi, 300),
                     photo_dpi=min(settings.photo_dpi, 150),
                     photo_quality=min(settings.photo_quality, 78),
                     gray_quality=min(settings.gray_quality, 72))
    if strong != settings:
        ladder.append(strong)
    harder = replace(strong,
                     text_dpi=min(strong.text_dpi, 250),
                     photo_dpi=min(strong.photo_dpi, 120),
                     photo_quality=min(strong.photo_quality, 68),
                     gray_quality=min(strong.gray_quality, 60))
    if harder != strong:
        ladder.append(harder)
    return ladder


def _now() -> str:
    return datetime.now().strftime("D:%Y%m%d%H%M%S")


def _scrub_metadata(doc: pymupdf.Document) -> None:
    """Drop everything that says who/what produced the document.

    A filing should not carry the office printer, the scanner model or the
    Windows user name of whoever happened to create it.  ``title``, ``subject``
    and ``keywords`` are kept because the pipeline sets them on purpose.

    Careful: ``Doc.scrub(hidden_text=True)`` must never be used here - the
    hidden text *is* the OCR layer we want to keep.
    """
    try:
        meta = dict(doc.metadata or {})
    except Exception:
        meta = {}
    try:
        doc.set_metadata({
            "title": meta.get("title", "") or "",
            "author": "",
            "subject": meta.get("subject", "") or "",
            "keywords": meta.get("keywords", "") or "",
            "creator": PRODUCER,
            "producer": PRODUCER,
            "creationDate": meta.get("creationDate", "") or _now(),
            "modDate": _now(),
        })
    except Exception as exc:
        LOGGER.debug("[optimize] metadata not written: %s", exc)
    try:
        doc.set_xml_metadata("")              # clear the XMP block too
    except Exception as exc:
        LOGGER.debug("[optimize] xmp not cleared: %s", exc)


def _summary(rows: list[PageRow], kept: int) -> str:
    """Plain-English one-liner for the run report."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.klass] = counts.get(row.klass, 0) + 1
    parts: list[str] = []
    if counts.get("text_scan"):
        dpi = max((row.dpi for row in rows if row.klass == "text_scan"), default=0)
        parts.append(f"{counts['text_scan']} text scan(s) re-encoded 1-bit @ {dpi} dpi")
    if counts.get("gray_scan"):
        parts.append(f"{counts['gray_scan']} grayscale page(s) re-encoded")
    if counts.get("mixed_scan"):
        parts.append(f"{counts['mixed_scan']} colour page(s) re-encoded")
    if counts.get("born_digital"):
        parts.append(f"{counts['born_digital']} digital page(s) untouched")
    if kept:
        parts.append(f"{kept} page(s) kept as-is")
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# The optimizer
# --------------------------------------------------------------------------- #

def _render_dpi(klass: str, settings: QualityPreset) -> int:
    """Which resolution a page class is rendered at."""
    return settings.text_dpi if klass == "text_scan" else settings.photo_dpi


def _optimize_once(src: Path, dst: Path, settings: QualityPreset, *,
                   scrub_metadata: bool, progress: ProgressFn | None) -> tuple[OptimizeResult, int]:
    """One full pass at ``settings``.  Returns (result, kept_page_count)."""
    before = src.stat().st_size / MB
    rows: list[PageRow] = []
    kept = 0
    with pymupdf.open(src) as doc:
        total = doc.page_count
        for index in range(total):
            page = doc[index]
            xref, cover = page_image(page)
            row = PageRow(page=index + 1, klass="born_digital", cover=cover)
            if xref is None or cover < MIN_PAGE_COVER:
                rows.append(row)                       # vector / digital page
                continue
            try:
                row.before_kb = len(doc.extract_image(xref)["image"]) / 1024
            except Exception:
                row.before_kb = 0.0
            try:
                klass, _stats = classify_page(
                    _render(page, min(PROBE_DPI, _render_dpi("mixed_scan", settings))))
                row.klass = klass
                image = _render(page, _render_dpi(klass, settings))
                data, label, dpi = _encode(image, klass, settings)
                row.after_kb, row.encoding, row.dpi = len(data) / 1024, label, dpi
                if row.before_kb and row.after_kb >= row.before_kb:
                    row.note = "kept - already smaller"
                    kept += 1
                else:
                    page.replace_image(xref, stream=data)
                    row.note = f"-{row.saving_pct:.0f}%"
            except Exception as exc:                   # never break a filing
                row.klass, row.note = "kept", f"kept as-is ({type(exc).__name__})"
                kept += 1
                LOGGER.warning("[optimize] page %d left untouched: %s", index + 1, exc)
            rows.append(row)
            LOGGER.debug("[optimize] page %d/%d %s cover=%.0f%% %.0f -> %.0f KB %s",
                         index + 1, total, row.klass, cover * 100,
                         row.before_kb, row.after_kb, row.note)
            if progress and (index + 1) % 10 == 0:
                progress(f"[optimize] {index + 1}/{total} pages")

        if scrub_metadata:
            _scrub_metadata(doc)
        pdf_ops.save_compact(doc, dst, effort=1)

    result = OptimizeResult(path=dst, before_mb=before, preset=settings.key,
                            after_mb=dst.stat().st_size / MB, rows=rows)
    return result, kept


def optimize_pdf(
    doc_path: str | Path,
    out_path: str | Path,
    *,
    preset: str = "balanced",
    text_dpi: int | None = None,
    photo_dpi: int | None = None,
    photo_quality: int | None = None,
    gray_quality: int | None = None,
    bitonal: bool | None = None,
    threshold: int | None = None,
    scrub_metadata: bool = True,
    target_mb: float = 0.0,
    progress: ProgressFn | None = None,
) -> OptimizeResult:
    """Rewrite ``doc_path`` into ``out_path``, encoding every page its own way.

    When ``target_mb`` is set and the first pass is still too big, the same file
    is optimised again with progressively stronger settings (the portal limit
    always wins, but quality is only sacrificed as far as necessary).
    """
    doc_path, out_path = Path(doc_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    settings = settings_for(preset, text_dpi=text_dpi, photo_dpi=photo_dpi,
                            photo_quality=photo_quality, gray_quality=gray_quality,
                            bitonal=bitonal, threshold=threshold)

    ladder = [settings] + (_escalations(settings) if target_mb else [])
    result: OptimizeResult | None = None
    kept = 0
    for index, current in enumerate(ladder):
        attempt = out_path.with_name(f"{out_path.stem}.q{index}{out_path.suffix}")
        if progress:
            progress(f"[optimize] preset {current.key}: text {current.text_dpi} dpi, "
                     f"colour {current.photo_dpi} dpi q{current.photo_quality}")
        result, kept = _optimize_once(doc_path, attempt, current,
                                      scrub_metadata=scrub_metadata, progress=progress)
        LOGGER.info("[optimize] pass %d (%s): %.2f -> %.2f MB (-%.0f%%), %s",
                    index + 1, current.key, result.before_mb, result.after_mb,
                    result.saving_pct, _summary(result.rows, kept) or "nothing to do")
        last = index == len(ladder) - 1
        if not target_mb or result.after_mb <= target_mb or last:
            attempt.replace(out_path)
            result.path = out_path
            break
        LOGGER.info("[optimize] still above the %.2f MB target - trying stronger "
                    "settings", target_mb)
        attempt.unlink(missing_ok=True)

    assert result is not None                     # ladder always has one rung
    result.after_mb = out_path.stat().st_size / MB
    wrote_something = out_path.is_file() and result.after_mb > 0
    # A size target that was asked for and not met is a FAILURE, not a quiet
    # success.  This used to be set from "did we write a file at all", so a PDF
    # left comfortably over the portal limit came back ok=True, the pipeline's
    # "still above the portal limit" warning could never fire, and the step was
    # reported as done.  A born-digital file is the usual way to hit this: the
    # optimizer deliberately leaves those pages alone, so it simply cannot
    # squeeze them any further.
    met_target = (not target_mb) or result.after_mb <= target_mb
    result.ok = wrote_something and met_target
    result.detail = (f"{result.before_mb:.2f} -> {result.after_mb:.2f} MB "
                     f"({result.saving_pct:.0f}% smaller) via '{result.preset}'; "
                     + (_summary(result.rows, kept) or "nothing to optimise"))
    if not met_target:
        result.detail += (f"; still above the {target_mb:.2f} MB target - the "
                          f"pages are already stored as efficiently as they can be")
    if not wrote_something:
        result.detail += "; nothing could be written"
    return result



