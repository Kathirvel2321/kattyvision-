"""Core PDF operations on top of PyMuPDF.

Everything in this module is 100% local: no network, no subscription, no
upload.  Each public function works on files on disk and returns a small
result object so the pipeline can build a report.

Step map (matches the old manual workflow):
    collect -> merge -> [cover] -> ocr -> compress -> paginate -> stamp -> split
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pymupdf

from . import __version__
from .util import MB, apply_template, mm2pt, safe_filename, sorted_naturally

LOGGER = logging.getLogger("court_ocr")

A4 = (595.276, 841.89)          # A4 in PDF points (portrait)
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

POSITIONS = {
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
}
ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT = 0, 1, 2

ProgressFn = Callable[[str], None]


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Outcome of a single pipeline step."""

    step: str
    ok: bool = True
    path: Path | None = None
    before_mb: float = 0.0
    after_mb: float = 0.0
    pages: int = 0
    seconds: float = 0.0
    detail: str = ""

    @property
    def saving_pct(self) -> float:
        if self.before_mb <= 0:
            return 0.0
        return (self.before_mb - self.after_mb) / self.before_mb * 100.0


@dataclass
class DocumentInfo:
    """Quick facts about a PDF, used for validation and the report."""

    path: Path
    pages: int = 0
    size_mb: float = 0.0
    encrypted: bool = False
    text_pages: int = 0
    scanned_pages: int = 0
    rotated_pages: int = 0
    size_groups: dict[str, int] = field(default_factory=dict)

    @property
    def needs_ocr(self) -> bool:
        return self.scanned_pages > 0


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def anchor_rect(
    page: pymupdf.Page,
    width: float,
    height: float,
    position: str,
    margin_x: float = 0.0,
    margin_y: float = 0.0,
) -> pymupdf.Rect:
    """Rectangle of size ``width`` x ``height`` placed at ``position``.

    Coordinates are taken from ``page.rect`` (the page as the viewer shows
    it), so a page carrying a /Rotate flag is handled transparently.
    """
    pos = (position or "bottom-center").lower().replace("_", "-").strip()
    if pos not in POSITIONS:
        pos = "bottom-center"
    box = page.rect
    left = box.x0 + margin_x
    center_x = (box.x0 + box.x1 - width) / 2.0
    right = box.x1 - margin_x - width
    top = box.y0 + margin_y
    center_y = (box.y0 + box.y1 - height) / 2.0
    bottom = box.y1 - margin_y - height

    x = {"left": left, "center": center_x, "right": right}[pos.split("-")[-1] if pos != "center" else "center"]
    vertical = pos.split("-")[0] if pos != "center" else "center"
    y = {"top": top, "middle": center_y, "center": center_y, "bottom": bottom}[vertical]
    return pymupdf.Rect(x, y, x + width, y + height)


def _page_size_label(page: pymupdf.Page) -> str:
    w, h = round(page.rect.width), round(page.rect.height)
    for name, (aw, ah) in (("A4", A4), ("Letter", (612.0, 792.0)), ("Legal", (612.0, 1008.0))):
        if abs(w - aw) <= 6 and abs(h - ah) <= 6:
            return name
        if abs(w - ah) <= 6 and abs(h - aw) <= 6:
            return f"{name} landscape"
    return f"{w}x{h}pt"


def rect_inside(parent: pymupdf.Rect, child: pymupdf.Rect) -> pymupdf.Rect:
    """Scale ``child`` down (keeping aspect) until it fits inside ``parent``."""
    if child.width <= parent.width and child.height <= parent.height:
        return child
    scale = min(parent.width / child.width, parent.height / child.height)
    return pymupdf.Rect(
        child.x0, child.y0, child.x0 + child.width * scale, child.y0 + child.height * scale
    )


def font_ascender(font: str, font_size: float) -> float:
    """Distance from the text baseline up to the top of the line (in points)."""
    try:
        ascender = abs(float(pymupdf.Font(font).ascender))
        if 0.1 < ascender < 2.0:
            return ascender * font_size
    except Exception:
        pass
    return 0.8 * font_size


def resolve_font(font: str) -> str:
    """Map a configured font name onto something ``page.insert_text`` accepts.

    Two kinds of value arrive here:

    * a built-in PDF base-14 name (``helv``, ``tiro``, ...) - returned as-is;
    * a path to a ``.ttf``/``.otf`` file, which is registered with PyMuPDF once
      and then referred to by the alias it was registered under.

    Registration is idempotent: ``insert_font`` is cheap when the file is
    already known, so calling this per page costs nothing extra.  If the file
    cannot be read, the original name is returned and the caller falls back to
    whatever PyMuPDF does with it (a warning is logged so the run report shows
    why the numbers came out in the default face).
    """
    name = str(font or "helv").strip()
    if not name:
        return "helv"
    # Built-in names and previously-registered aliases pass straight through.
    if "\\" not in name and "/" not in name and not name.lower().endswith(
            (".ttf", ".otf", ".ttc")):
        return name
    path = Path(name)
    if not path.is_file():
        LOGGER.warning("[paginate] font file not found: %s - using Helvetica", name)
        return "helv"
    alias = "pagin" + hashlib.sha1(str(path.resolve()).lower().encode()).hexdigest()[:8]
    try:
        # A Document-scoped font must be inserted into every document that uses
        # it, which is why add_page_numbers calls this helper with its doc.
        pymupdf.Font(fontfile=str(path))            # validate it can be loaded
    except Exception as exc:
        LOGGER.warning("[paginate] cannot read font %s (%s) - using Helvetica",
                       name, exc)
        return "helv"
    return alias


def draw_text(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    text: str,
    *,
    font: str = "helv",
    font_size: float = 9.0,
    color: tuple = (0, 0, 0),
    align: int = ALIGN_CENTER,
    line_spacing: float = 0.0,
    overlay: bool = True,
) -> bool:
    """Draw text inside ``rect`` and *guarantee* it appears.

    ``Page.insert_textbox`` silently writes nothing when the text is even a
    fraction of a point too tall for its box (this really happens on OCR'd
    pages, where the needed height came out 0.057pt over).  So: try the box
    first, and fall back to drawing each line at an explicit baseline, which
    never has a fitting check.
    """
    text = str(text)
    if not text.strip():
        return False
    try:
        remaining = page.insert_textbox(
            rect, text, fontname=font, fontsize=font_size, color=color,
            align=align, overlay=overlay,
        )
        if remaining >= 0:
            return True
        LOGGER.debug("insert_textbox overflow (%.3fpt) - using baseline fallback", remaining)
    except Exception as exc:
        LOGGER.debug("insert_textbox failed (%s) - using baseline fallback", exc)

    try:
        lines = text.split("\n")
        step = line_spacing or font_size * 1.35
        baseline = rect.y0 + font_ascender(font, font_size)
        for line in lines:
            if not line.strip():
                baseline += step
                continue
            try:
                width = pymupdf.get_text_length(line, fontname=font, fontsize=font_size)
            except Exception:
                width = len(line) * font_size * 0.55
            if align == ALIGN_CENTER:
                x = rect.x0 + (rect.width - width) / 2.0
            elif align == ALIGN_RIGHT:
                x = rect.x1 - width
            else:
                x = rect.x0
            page.insert_text((x, baseline), line, fontname=font, fontsize=font_size,
                             color=color, overlay=overlay)
            baseline += step
        return True
    except Exception as exc:
        LOGGER.error("draw_text failed: %s", exc)
        return False



# ---------------------------------------------------------------------------
# Import files from a folder
# ---------------------------------------------------------------------------

SKIP_PREFIXES = ("~$", "._")


def collect_inputs(
    folder: str | Path,
    extensions: Sequence[str],
    *,
    natural: bool = True,
    recursive: bool = True,
) -> list[Path]:
    """Return the files to merge, in the order the advocate expects them.

    Sorting is natural, so ``Exhibit 2`` comes before ``Exhibit 10``.  Hidden
    and temporary Office lock files are ignored.
    """
    root = Path(folder)
    if not root.is_dir():
        return []
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    found: list[Path] = []

    def _scan(directory: Path) -> None:
        try:
            children = list(directory.iterdir())
        except OSError:
            return
        files = [
            c for c in children
            if c.is_file()
            and c.suffix.lower() in exts
            and not c.name.startswith(SKIP_PREFIXES)
        ]
        found.extend(sorted_naturally(files, natural))
        if recursive:
            for sub in sorted_naturally([c for c in children if c.is_dir()], natural):
                _scan(sub)

    _scan(root)
    return found


def is_readable_pdf(path: str | Path) -> tuple[bool, str]:
    """Cheap validation: can PyMuPDF open this file and does it have pages?"""
    try:
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                return False, "password protected"
            if doc.page_count == 0:
                return False, "no pages"
        return True, ""
    except Exception as exc:  # pragma: no cover
        return False, f"{type(exc).__name__}: {exc}"


def inspect(path: str | Path, sample_limit: int = 400) -> DocumentInfo:
    """Collect page count, size, text/scan mix and page-size groups."""
    path = Path(path)
    info = DocumentInfo(path=path, size_mb=os.path.getsize(path) / MB)
    with pymupdf.open(path) as doc:
        info.encrypted = bool(doc.needs_pass)
        info.pages = doc.page_count
        for index in range(doc.page_count):
            page = doc[index]
            if page.rotation % 360:
                info.rotated_pages += 1
            label = _page_size_label(page)
            info.size_groups[label] = info.size_groups.get(label, 0) + 1
            if index < sample_limit or doc.page_count <= sample_limit:
                text = page.get_text().strip()
                has_image = bool(page.get_images(full=True))
                if len(text) >= 30 or (text and not has_image):
                    info.text_pages += 1
                else:
                    info.scanned_pages += 1
    return info


def page_needs_ocr(page: pymupdf.Page, min_chars: int = 30) -> bool:
    """True when a page carries little/no real text (so it needs OCR)."""
    text = page.get_text().strip()
    if not text and page.get_images(full=True):
        return True
    return len(text) < min_chars


# ---------------------------------------------------------------------------
# Step 1 - merge everything into one PDF
# ---------------------------------------------------------------------------

@dataclass
class MergeResult:
    """Where the merged PDF went and what ended up inside it."""

    path: Path
    pages: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    entries: list[tuple[str, int, int]] = field(default_factory=list)  # (title, first, last)

    @property
    def included(self) -> int:
        return len(self.entries)


def _image_frame_count(path: Path) -> int:
    """Number of frames/pages inside an image file (multi-page TIFF aware)."""
    if path.suffix.lower() in (".tif", ".tiff"):
        try:
            from PIL import Image

            with Image.open(path) as img:
                return int(getattr(img, "n_frames", 1))
        except Exception:
            return 1
    return 1


def _append_image(out: pymupdf.Document, src: Path) -> None:
    """Append an image file as one (or more) A4 portrait page(s)."""
    frames = _image_frame_count(src)
    if frames <= 1:
        page = out.new_page(width=A4[0], height=A4[1])
        try:
            page.insert_image(page.rect, filename=str(src), keep_proportion=True)
            return
        except Exception as exc:
            LOGGER.debug("direct image insert failed for %s (%s), re-encoding", src.name, exc)
            out.delete_page(out.page_count - 1)
    with pymupdf.open(src) as idoc:              # MuPDF opens images as a document
        for index in range(max(frames, idoc.page_count)):
            try:
                pix = idoc[index].get_pixmap(dpi=200)
                data = pix.tobytes("jpeg", jpg_quality=85)
                page = out.new_page(width=A4[0], height=A4[1])
                page.insert_image(page.rect, stream=data, keep_proportion=True)
            except Exception as exc:
                LOGGER.warning("could not read frame %d of %s: %s", index, src.name, exc)
                break


def _append_pdf(
    out: pymupdf.Document,
    src: Path,
    *,
    flatten_rotation: bool = True,
    keep_links: bool = True,
) -> int:
    """Append a PDF and return how many pages were added.

    Pages carrying a /Rotate flag are re-drawn onto a fresh page via
    ``show_pdf_page`` ("baked"), so stamping and page numbering always land
    where the reader actually sees them.  Text stays selectable.
    """
    added = 0
    with pymupdf.open(src) as sdoc:
        if sdoc.needs_pass:
            raise RuntimeError("PDF is password protected - remove the password first")
        rotations = {page.rotation % 360 for page in sdoc}
        if not flatten_rotation or rotations <= {0}:
            start = out.page_count
            out.insert_pdf(sdoc, links=keep_links, annots=keep_links)
            added = out.page_count - start
        else:
            for pno in range(sdoc.page_count):
                page = sdoc[pno]
                if page.rotation % 360 == 0:
                    out.insert_pdf(sdoc, from_page=pno, to_page=pno,
                                   links=keep_links, annots=keep_links)
                else:
                    rect = page.rect                  # already the displayed size
                    fresh = out.new_page(width=rect.width, height=rect.height)
                    fresh.show_pdf_page(fresh.rect, sdoc, pno)
                added += 1
    return added


def merge_files(
    files: Sequence[Path],
    out_path: str | Path,
    *,
    create_bookmarks: bool = True,
    blank_separator: bool = False,
    flatten_rotation: bool = True,
    progress: ProgressFn | None = None,
    on_error: str = "skip",
) -> MergeResult:
    """Merge ``files`` (PDFs and images) into a single PDF, in given order."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = MergeResult(path=out_path)
    out = pymupdf.open()
    toc: list[list] = []
    total = len(files)

    try:
        for position, src in enumerate(files, start=1):
            title = safe_filename(src.stem, fallback=f"Document {position}")
            if progress:
                progress(f"[merge] {position}/{total}  {src.name}")
            try:
                first = out.page_count + 1
                if src.suffix.lower() == ".pdf":
                    added = _append_pdf(out, src, flatten_rotation=flatten_rotation)
                else:
                    before = out.page_count
                    _append_image(out, src)
                    added = out.page_count - before
                if added == 0:
                    result.skipped.append((src.name, "no pages produced"))
                    continue
                result.entries.append((title, first, out.page_count))
                if create_bookmarks:
                    toc.append([1, title, first])
                if blank_separator and position < total:
                    out.new_page(width=A4[0], height=A4[1])
            except Exception as exc:
                LOGGER.error("[merge] FAILED %s: %s", src.name, exc)
                result.skipped.append((src.name, str(exc)))
                if on_error == "abort":
                    raise

        if toc:
            try:
                out.set_toc(toc)
            except Exception as exc:  # bookmarks must never break a merge
                LOGGER.warning("[merge] bookmarks skipped: %s", exc)

        _save_compact(out, out_path)
        result.pages = out.page_count
    finally:
        out.close()
    return result


# ---------------------------------------------------------------------------
# Saving helpers
# ---------------------------------------------------------------------------

def _save_compact(doc: pymupdf.Document, path: str | Path, *, effort: int = 0) -> Path:
    """Save with structure cleanup: garbage collection + object streams.

    Note on `compression_effort`: in PyMuPDF (1.28.2), `compression_effort` is
    scaled 0..100 (where values 1..11 map to deflate level 0 / uncompressed!).
    We only pass `compression_effort` if effort >= 12, or omit it completely so
    MuPDF uses its default deflate level (level 6/7) which achieves optimal
    compression.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        garbage=4, deflate=True, deflate_images=True, deflate_fonts=True,
        clean=True, use_objstms=1,
    )
    if effort:
        # Scale 1-9 (legacy zlib scale) to 10-100 or keep raw if already 10-100
        val = int(effort)
        if 1 <= val <= 9:
            val = val * 10  # e.g. 1 -> 10? No, 10 is still level 0! So min 15
            val = max(val, 20)
        kwargs["compression_effort"] = min(val, 100)
    try:
        doc.save(str(path), **kwargs)
    except Exception as exc:
        LOGGER.debug("clean save failed (%s), retrying with basic settings", exc)
        doc.save(str(path), garbage=3, deflate=True)
    return path


# Public name - other steps (e.g. optimize.py) save with the same cleanup, so
# the save flags live in exactly one place.
save_compact = _save_compact


# ---------------------------------------------------------------------------
# Step 5 - pagination ("Page X of Y")
# ---------------------------------------------------------------------------

ROMAN = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
    (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def _roman(value: int) -> str:
    if value <= 0:
        return str(value)
    out = []
    for number, letters in ROMAN:
        while value >= number:
            out.append(letters)
            value -= number
    return "".join(out)


def _label(value: int, style: str) -> str:
    style = (style or "arabic").lower()
    if style == "roman":
        return _roman(value).lower()
    if style == "ROMAN":
        return _roman(value)
    if style == "alpha":
        text = ""
        value = max(1, value)
        while value:
            value, rem = divmod(value - 1, 26)
            text = chr(65 + rem) + text
        return text
    return str(value)


@dataclass
class PaginateResult:
    """Outcome of the pagination step."""

    path: Path
    pages_numbered: int = 0
    ok: bool = True
    detail: str = ""


def add_page_numbers(
    doc_path: str | Path,
    out_path: str | Path,
    *,
    fmt: str = "{page}",
    position: str = "bottom-center",
    font: str = "helv",
    font_file: str | None = None,
    font_size: float = 9.0,
    margin_mm: float = 8.0,
    start_number: int = 1,
    skip_first: int = 0,
    skip_last: int = 0,
    style: str = "arabic",
    background: bool = False,
    progress: ProgressFn | None = None,
) -> PaginateResult:
    """Burn page numbers into every page (except the skipped ones).

    Two ways to name the face, and both are in use:

    * ``font_file`` - a path to a ``.ttf``/``.otf``.  Set by the "Page numbers"
      tool page, where the user picks a file with a Choose... button.  It wins
      over ``font`` when present, and an empty string means "not chosen", not
      "use this literal name".
    * ``font`` - a built-in base-14 name, or a path.  The pipeline resolves a
      configured ``pagination.font_file`` into a path and passes it here, so
      this function must still cope with a path arriving through ``font``.
    """
    doc_path, out_path = Path(doc_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = PaginateResult(path=out_path)

    with pymupdf.open(doc_path) as doc:
        total_pages = doc.page_count
        last = max(skip_first, total_pages - max(0, skip_last))
        counted = max(1, last - skip_first)
        margin = mm2pt(margin_mm)
        font_obj = None
        # A custom .ttf/.otf is embedded per page, under an alias the drawing
        # code can then refer to by name.  Two callers reach here with the face
        # chosen in different ways - the tool page hands over a separate
        # ``font_file`` (empty string when the user picked nothing), and the
        # pipeline folds a configured font file into ``font`` - so take whichever
        # is a real path and fall back to the built-in name.
        chosen = str(font_file or "").strip() or str(font or "helv").strip()
        used_font = "helv"
        font_file_path: Path | None = None
        if "\\" in chosen or "/" in chosen or chosen.lower().endswith(
                (".ttf", ".otf", ".ttc")):
            font_path = Path(chosen)
            if font_path.is_file():
                # NOTE: str() has to wrap the path BEFORE .lower().  Written the
                # other way round it calls .lower() on a Path, which has no such
                # method, and the whole font feature dies the moment a user
                # actually picks a file.  The alias only has to be stable per
                # file, so the lowercase full path is a good key.
                used_font = "pagin" + hashlib.sha1(
                    str(font_path.resolve()).lower().encode()).hexdigest()[:8]
                try:
                    # get_text_length only knows the base-14 names, so widths
                    # for an embedded face come from a Font object instead.
                    # Loading it here also proves the file is readable before
                    # any page is touched.
                    font_obj = pymupdf.Font(fontfile=str(font_path))
                except Exception as exc:
                    LOGGER.warning("[paginate] cannot use font %s (%s) - "
                                   "falling back to Helvetica", chosen, exc)
                    used_font = "helv"
                    font_obj = None
                else:
                    font_file_path = font_path
            else:
                LOGGER.warning("[paginate] font file not found: %s - "
                               "using Helvetica", chosen)
                used_font = "helv"
        else:
            used_font = chosen
        for index in range(skip_first, last):
            if progress:
                progress(f"[pagination] {index + 1}/{last} pages")
            page = doc[index]
            if font_file_path is not None:
                # Registration is per PAGE, not per document: PyMuPDF has
                # Page.insert_font() and no Document.insert_font(), so calling
                # it on the document raises AttributeError and the font is
                # silently replaced by Helvetica.  Registered once per page here
                # so every page that gets a number carries the real face.
                try:
                    page.insert_font(fontname=used_font,
                                     fontfile=str(font_file_path))
                except Exception as exc:
                    LOGGER.warning("[paginate] could not embed %s on page %d "
                                   "(%s) - using Helvetica for this page",
                                   chosen, index + 1, exc)
                    used_font = "helv"
                    font_obj = None
                    font_file_path = None
            number = start_number + (index - skip_first)
            # {page}/{total} follow the chosen Number style, {roman} and
            # {roman_total} are always Roman, so "Page i of xx" is available
            # even when the style is 1, 2, 3.  A hand-typed layout can contain
            # anything, so an unknown placeholder falls back to the plain
            # number rather than raising and abandoning the whole job.
            try:
                text = str(fmt).format(
                    page=_label(number, style),
                    total=_label(counted, style),
                    roman=_roman(number),
                    roman_total=_roman(counted),
                )
            except Exception as exc:                        # noqa: BLE001
                LOGGER.warning("[paginate] layout %r could not be filled in "
                               "(%s) - falling back to 'Page X of Y'",
                               fmt, exc)
                text = "{page}".format(
                    page=_label(number, style),
                    total=_label(counted, style),
                )
            try:
                if font_obj is not None:
                    width = font_obj.text_length(text, fontsize=font_size) + 6
                else:
                    width = pymupdf.get_text_length(
                        text, fontname=used_font, fontsize=font_size) + 6
                height = max(font_size * 1.5, 14.0)
                rect = anchor_rect(page, width, height, position, margin, margin * 0.5)
                if background:
                    page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)
                if draw_text(page, rect, text, font=used_font, font_size=font_size,
                             color=(0, 0, 0), align=ALIGN_CENTER):
                    result.pages_numbered += 1
                else:
                    result.ok = False
                    LOGGER.warning("[paginate] could not stamp page %d", index + 1)
            except Exception as exc:
                LOGGER.warning("[paginate] page %d failed: %s", index + 1, exc)
                result.ok = False
        _save_compact(doc, out_path)
    return result


# ---------------------------------------------------------------------------
# Step 4 - compression (portal size limits)
# ---------------------------------------------------------------------------

@dataclass
class CompressResult:
    """Outcome of the compression step."""

    path: Path
    before_mb: float = 0.0
    after_mb: float = 0.0
    level: int = 0
    engine: str = ""
    ok: bool = True
    detail: str = ""

    @property
    def saving_pct(self) -> float:
        if self.before_mb <= 0:
            return 0.0
        return (self.before_mb - self.after_mb) / self.before_mb * 100.0


def _gs_compress(
    src: Path,
    out_path: Path,
    gs_exe: str,
    *,
    dpi: int = 150,
    jpeg_quality: int = 75,
    grayscale: bool = False,
) -> Path:
    """Optional Ghostscript pass - the strongest compression available."""
    args = [
        gs_exe, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7", "-dNOPAUSE",
        "-dBATCH", "-dQUIET", "-dSAFER", "-dDetectDuplicateImages=true",
        "-dCompressFonts=true", "-dSubsetFonts=true", "-dEmbedAllFonts=true",
        "-dAutoRotatePages=/None", "-dDownsampleColorImages=true",
        f"-dColorImageResolution={int(dpi)}", "-dColorImageDownsampleType=/Bicubic",
        "-dDownsampleGrayImages=true", f"-dGrayImageResolution={int(dpi)}",
        "-dGrayImageDownsampleType=/Bicubic", "-dDownsampleMonoImages=true",
        f"-dMonoImageResolution={max(300, int(dpi) * 2)}",
        "-dMonoImageDownsampleType=/Subsample",
        "-dAutoFilterColorImages=false", "-dColorImageFilter=/DCTEncode",
        "-dAutoFilterGrayImages=false", "-dGrayImageFilter=/DCTEncode",
        f"-dJPEGQ={max(10, min(95, int(jpeg_quality)))}",
        "-dEncodeColorImages=true", "-dEncodeGrayImages=true",
    ]
    if grayscale:
        args += ["-dColorConversionStrategy=/Gray", "-dProcessColorModel=/DeviceGray"]
    args += [f"-sOutputFile={out_path}", str(src)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=1800,
                          creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError(f"ghostscript failed ({proc.returncode}): {(proc.stderr or '')[:300]}")
    return out_path


def _rewrite_images(doc: pymupdf.Document, *, dpi: int, quality: int, grayscale: bool) -> None:
    """Downsample + re-encode embedded images (PyMuPDF native, no GS needed).

    ``rewrite_images`` demands ``dpi_target < dpi_threshold``: the threshold
    selects which images are "too dense", the target says what to shrink them
    to.  So a 300 dpi scan asked for 150 dpi becomes: threshold 187, target 150.
    """
    target = max(50, int(dpi))
    threshold = max(target + 10, int(round(target * 1.25)))
    try:
        doc.rewrite_images(
            dpi_threshold=threshold,
            dpi_target=target,
            quality=max(10, min(95, int(quality))),
            set_to_gray=bool(grayscale),
            lossy=True, lossless=True, bitonal=True, color=True, gray=True,
        )
        LOGGER.debug("[compress] images rewritten (threshold %d dpi -> %d dpi, q%d)",
                     threshold, target, quality)
    except Exception as exc:
        LOGGER.warning("[compress] image rewrite unavailable (%s)", exc)


def _subset_fonts(doc: pymupdf.Document) -> None:
    """Shrink embedded fonts; silently skip when unsupported."""
    try:
        doc.subset_fonts()
    except Exception as exc:
        LOGGER.debug("[compress] subset_fonts skipped: %s", exc)


def compress_pdf(
    doc_path: str | Path,
    out_path: str | Path,
    *,
    level: int = 0,
    levels_to_try: Sequence[int] = (1, 2, 3),
    target_mb: float = 0.0,
    dpi: int = 150,
    jpeg_quality: int = 75,
    grayscale: bool = False,
    gs_exe: str = "",
    progress: ProgressFn | None = None,
) -> CompressResult:
    """Shrink a PDF, optionally chasing a hard size limit.

    level 1: structural cleanup + font subsetting (lossless, gentlest)
    level 2: + downsample/re-encode images (visually lossless at 150 dpi)
    level 3: + Ghostscript pass (or harder level-2 settings when GS is absent)
    """
    doc_path, out_path = Path(doc_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    before = os.path.getsize(doc_path) / MB
    start = int(level) if int(level) > 0 else min(int(x) for x in levels_to_try)
    # start at the configured level, then escalate to any stronger level
    plan = sorted({start} | {int(x) for x in levels_to_try if int(x) >= start})
    tried: list[str] = []

    def _attempt(chosen: int) -> tuple[float, str]:
        """Run one compression level; returns (size_mb, engine_label)."""
        tmp = out_path.with_name(f"{out_path.stem}.lvl{chosen}{out_path.suffix}")
        with pymupdf.open(doc_path) as doc:
            if chosen >= 1:
                _subset_fonts(doc)
            if chosen >= 2:
                _rewrite_images(doc, dpi=dpi, quality=jpeg_quality, grayscale=grayscale)
            _save_compact(doc, tmp, effort=1 if chosen >= 1 else 0)
        size, engine = os.path.getsize(tmp) / MB, f"pymupdf L{chosen}"

        if chosen >= 3:
            if gs_exe:
                gs_out = tmp.with_name(f"{tmp.stem}.gs{tmp.suffix}")
                try:
                    _gs_compress(tmp, gs_out, gs_exe, dpi=dpi,
                                 jpeg_quality=jpeg_quality, grayscale=grayscale)
                    if gs_out.stat().st_size < tmp.stat().st_size:
                        tmp.unlink(missing_ok=True)
                        tmp = gs_out
                        size, engine = tmp.stat().st_size / MB, "ghostscript"
                    else:
                        gs_out.unlink(missing_ok=True)
                except Exception as exc:
                    LOGGER.debug("[compress] ghostscript pass skipped: %s", exc)
            else:
                with pymupdf.open(doc_path) as doc:
                    _subset_fonts(doc)
                    _rewrite_images(doc, dpi=max(72, int(dpi * 0.7)),
                                    quality=max(35, jpeg_quality - 20), grayscale=True)
                    _save_compact(doc, tmp, effort=2)
                size, engine = os.path.getsize(tmp) / MB, "pymupdf L3 (no ghostscript)"
        tmp.replace(out_path)
        return size, engine

    final_size, final_engine, final_level = before, "none", 0
    for chosen in plan:
        if progress:
            progress(f"[compress] level {chosen} ...")
        try:
            size, engine = _attempt(chosen)
        except Exception as exc:
            LOGGER.error("[compress] level %d failed: %s", chosen, exc)
            tried.append(f"L{chosen}: error {exc}")
            continue
        tried.append(f"L{chosen}: {size:.2f} MB via {engine}")
        LOGGER.info("[compress] level %d -> %.2f MB (%+.1f%%) via %s", chosen, size,
                    -((before - size) / before * 100 if before else 0), engine)
        final_size, final_engine, final_level = size, engine, chosen
        if not target_mb or size <= target_mb:
            break
        LOGGER.info("[compress] above the %.2f MB target - trying a stronger level", target_mb)

    return CompressResult(
        path=out_path, before_mb=before, after_mb=final_size, level=final_level,
        engine=final_engine, ok=(not target_mb) or final_size <= target_mb,
        detail=" | ".join(tried),
    )


# ---------------------------------------------------------------------------
# Step 8 - split into portal-sized parts
# ---------------------------------------------------------------------------

@dataclass
class SplitResult:
    """Outcome of the splitting step."""

    files: list[Path] = field(default_factory=list)
    total_pages: int = 0
    skipped: list[str] = field(default_factory=list)
    #: Parts that are bigger than ``max_size_mb`` even though the user asked to
    #: split by page count only.  Reported, never obeyed - the page count was
    #: the rule they gave, so re-cutting would only fight them.
    over_size: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.files)


def _bookmark_starts(doc: pymupdf.Document) -> dict[int, str]:
    """``{page_number(1-based): title}`` for every top-level bookmark."""
    starts: dict[int, str] = {}
    try:
        for item in doc.get_toc(simple=True) or []:
            if not item or len(item) < 3:
                continue
            level, title, page = item[0], item[1], item[2]
            if int(level) == 1 and 1 <= int(page) <= doc.page_count:
                starts.setdefault(int(page), str(title))
    except Exception as exc:
        LOGGER.debug("[split] no usable bookmarks: %s", exc)
    return starts


def _best_cut(first: int, last: int, starts: dict[int, str]) -> int | None:
    """Choose a cut point near the middle, preferring a document boundary."""
    if last <= first:
        return None
    middle = (first + last + 1) // 2
    boundaries = [page for page in starts if first < page <= last]
    if boundaries:
        return min(boundaries, key=lambda page: abs(page - middle))
    return middle


def _part_end(first: int, last: int, max_pages: int, starts: dict[int, str]) -> int:
    """The **last page of this part** when the page limit is what decides.

    The part is filled right up to the limit, so "300 pages, 40 per part" gives
    exactly eight parts (40 x 7 + 20) instead of fifteen parts of twenty.  The
    old code cut at the middle of the allowed window, which quietly halved every
    part and doubled the number of uploads.

    The one exception is a document boundary: if an exhibit starts inside the
    window, the end is pulled back to it so "never cut a document in half" still
    holds.  That only shortens *this* part - the next one starts filling again
    immediately, so the pages are never wasted.
    """
    allowed = min(first + max(1, int(max_pages)) - 1, last)
    boundaries = [page for page in starts if first < page <= allowed]
    return max(boundaries) if boundaries else allowed


def _write_part(
    src_path: Path,
    first: int,
    last: int,
    dest: Path,
    starts: dict[int, str],
    title: str = "",
) -> Path:
    """Extract pages ``first..last`` (1-based, inclusive) into ``dest``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(src_path) as src:
        out = pymupdf.open()
        try:
            try:
                out.insert_pdf(src, from_page=first - 1, to_page=last - 1,
                               links=True, annots=True)
            except Exception:
                out.insert_pdf(src, from_page=first - 1, to_page=last - 1)
            toc = [[1, str(starts[page]), page - first + 1]
                   for page in sorted(starts) if first <= page <= last]
            if title:
                toc.insert(0, [1, title, 1])
            if toc:
                try:
                    out.set_toc(toc)
                except Exception:
                    pass
            if title:
                try:
                    producer = f"KattyVision {__version__}"
                    out.set_metadata({
                        "title": title,
                        "author": "",
                        "subject": "",
                        "keywords": "",
                        "creator": producer,
                        "producer": producer,
                    })
                except Exception:
                    pass
            _save_compact(out, dest)
        finally:
            out.close()
    return dest


def split_pdf(
    doc_path: str | Path,
    out_dir: str | Path,
    *,
    mode: str = "pages",
    max_pages: int = 50,
    max_size_mb: float = 0.0,
    keep_bookmark_boundaries: bool = True,
    name_template: str = "{case}_Part{index:02d}.pdf",
    case: str = "Case",
    court: str = "",
    bank: str = "",
    number: str = "",
    start_index: int = 1,
    part_of_total: bool = False,
    progress: ProgressFn | None = None,
) -> SplitResult:
    """Split a PDF into upload-sized parts.

    The **page limit is the rule**: a part is filled right up to ``max_pages``
    ("300 pages, 40 per upload" -> exactly 8 parts), pulled back only to a
    document boundary when "never cut a document in half" is switched on.

    The **file-size limit is opt-in**.  It only does anything when the caller
    asks for it (``mode="size"``/``"both"`` together with ``max_size_mb``).
    With the default ``mode="pages"`` a part that happens to be bigger than
    ``max_size_mb`` is reported in :attr:`SplitResult.over_size` and kept
    exactly as it is - re-cutting behind the user's back is what used to hand
    back a completely different number of files than they asked for.
    """
    doc_path, out_dir = Path(doc_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = SplitResult()
    max_pages = max(1, int(max_pages or 1))
    mode = (mode or "pages").lower()

    with pymupdf.open(doc_path) as doc:
        total = doc.page_count
        starts = _bookmark_starts(doc) if keep_bookmark_boundaries else {}
    result.total_pages = total
    if total == 0:
        result.detail = "source PDF has no pages"
        return result

    pending: list[tuple[int, int]] = [(1, total)]
    index = max(1, int(start_index))
    guard = 0
    while pending:
        guard += 1
        if guard > 100_000:                     # paranoia: never loop forever
            result.detail = "split aborted (safety limit)"
            break
        first, last = pending.pop(0)
        span = last - first + 1

        if span > max_pages and mode in ("pages", "both"):
            # ``end`` is the last page of this part; the next part starts after it.
            end = _part_end(first, last, max_pages, starts)
            end = max(first, min(end, last))
            pending.insert(0, (end + 1, last))
            pending.insert(0, (first, end))
            continue

        name = apply_template(
            name_template, case=case, court=court, bank=bank, number=number,
            index=index, pages=span, start=first, end=last, total=total,
            part=index, parts=(0 if part_of_total else ""),
        )
        dest = out_dir / name
        if progress:
            progress(f"[split] writing part {index}: pages {first}-{last}")
        _write_part(doc_path, first, last, dest, starts, title=f"{case} - part {index}")
        size = dest.stat().st_size / MB

        if max_size_mb and size > max_size_mb and span > 1 and mode in ("size", "both"):
            # Only ever reached when the user explicitly asked for size-based
            # splitting.  Here halving is the right move: there is no way to
            # know how many pages will fit under an MB limit, and it stops at
            # one page rather than looping.
            cut = _best_cut(first, last, starts) or ((first + last + 1) // 2)
            cut = max(first + 1, min(cut, last))
            dest.unlink(missing_ok=True)
            LOGGER.info("[split] part %d too big (%.2f MB) - re-cutting pages %d-%d",
                        index, size, first, last)
            pending.insert(0, (cut, last))
            pending.insert(0, (first, cut - 1))
            continue

        if max_size_mb and size > max_size_mb:
            note = (f"{name}: {size:.2f} MB is over the {max_size_mb:.2f} MB limit "
                    f"but was kept as {span} page(s)")
            if mode in ("size", "both"):
                # The user asked to obey the size limit and it cannot be obeyed:
                # this single page is already over it.
                note = f"{name}: {size:.2f} MB exceeds {max_size_mb:.2f} MB even as one page"
                LOGGER.warning("[split] %s", note)
                result.skipped.append(note)
            else:
                # Page count was the rule they gave, so the size limit is only
                # reported.  Re-cutting here would quietly hand back a different
                # number of files than the one they asked for.
                LOGGER.info("[split] %s", note)
                result.over_size.append(note)

        result.files.append(dest)
        if progress:
            progress(f"[split] part {index}: pages {first}-{last}, {size:.2f} MB -> {name}")
        index += 1

    if result.files and progress:
        progress(f"[split] {len(result.files)} file(s) written")
    return result


def split_pdf_at_cuts(
    sources: Sequence[tuple[str | Path, int]],
    cuts: Sequence[tuple[int, int]],
    out_dir: str | Path,
    *,
    name_template: str = "{case}_Part{index:02d}.pdf",
    keep_bookmark_boundaries: bool = True,
    progress: ProgressFn | None = None,
) -> list[Path]:
    """Split across several files at the exact pages the user picked.

    ``sources`` is ``[(path, page_count), ...]`` in the order the user added the
    files, and ``cuts`` lists ``(file_index, page_index)`` points where a new
    part starts - exactly what the page map shows.  Parts may be any size, so
    "5 pages, then 8, then the rest" works.

    Returns the files written, in order.  Never raises: a file that cannot be
    read is skipped and the remaining parts still come out.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Flatten (file, page) pairs into one running sequence so the cut maths
    # does not care how the pages are spread across the source files.
    flat: list[tuple[int, int]] = []
    for file_index, (_path, pages) in enumerate(sources):
        flat.extend((file_index, page) for page in range(int(pages or 0)))
    if not flat:
        return []

    wanted = set(cuts)
    positions = {index for index, ref in enumerate(flat) if ref in wanted}
    positions.discard(0)
    # Where each part *wants* to end, in the flattened page order.
    targets = sorted(positions) + [len(flat)]

    written: list[Path] = []
    index = 1
    # ``cursor`` is where the next part starts, and it does NOT simply jump to
    # the next cut: when a part has to be clipped at a document boundary the
    # pages between that boundary and the cut still belong to somebody.  It used
    # to start at the cut instead, which silently threw those pages away - ten
    # lost pages in a 3-document, 120-page case, with no error anywhere.
    cursor = 0
    for target in targets:
        begin = cursor
        end = target - 1
        if end < begin:
            continue
        first_file, first_page = flat[begin]
        last_file, last_page = flat[end]
        path = Path(sources[first_file][0])
        # A part never spans two source files: the user picked these files, and
        # silently stitching two of them together would be surprising.
        if last_file != first_file:
            last_page = int(sources[first_file][1] or first_page + 1) - 1
            end = begin + (last_page - first_page)
        count = end - begin + 1
        if progress:
            progress(f"[split] part {index}: {count} page(s) "
                     f"from {path.name}")
        try:
            starts: dict[int, str] = {}
            if keep_bookmark_boundaries:
                try:
                    with pymupdf.open(path) as doc:
                        starts = _bookmark_starts(doc)
                except Exception:
                    starts = {}
            name = apply_template(name_template, case=path.stem, index=index,
                                  pages=count, start=first_page + 1,
                                  end=last_page + 1, total=len(flat),
                                  part=index, parts="")
            dest = out_dir / name
            _write_part(path, first_page + 1, last_page + 1, dest, starts,
                        title=f"{path.stem} - part {index}")
            written.append(dest)
            index += 1
        except Exception as exc:                 # one bad file must not stop all
            LOGGER.warning("[split] part %d failed (%s)", index, exc)
        # Move past whatever this part covered, whether it was written or not,
        # so a failure can never make the loop re-write the same pages.
        cursor = end + 1
    return written


# ---------------------------------------------------------------------------
# Optional cover / index page
# ---------------------------------------------------------------------------

def add_cover_page(
    doc_path: str | Path,
    out_path: str | Path,
    *,
    entries: Sequence[tuple[str, int, int]] = (),
    title: str = "",
    subtitle: str = "Index of Documents",
    court: str = "",
    bank: str = "",
    number: str = "",
    show_index: bool = True,
    show_page_range: bool = True,
    page_offset: int = 1,
    footer: str = "",
) -> int:
    """Insert an index page at the front; returns the resulting page count.

    ``page_offset`` shifts the printed page references: the entries were
    measured before this (cover) page existed, so they need +1.  Bookmarks are
    left alone - PDF outlines point at page objects, so they follow their pages
    automatically when a page is inserted in front.
    """
    doc_path, out_path = Path(doc_path), Path(out_path)
    with pymupdf.open(doc_path) as doc:
        page = doc.new_page(pno=0, width=A4[0], height=A4[1])
        margin = mm2pt(18)
        left, right = margin, page.rect.x1 - margin
        width = right - left
        cursor = margin

        def _line(text: str, size: float = 11, font: str = "helv",
                  align: int = ALIGN_CENTER, gap: float = 4.0,
                  color: tuple = (0, 0, 0)) -> None:
            nonlocal cursor
            if not str(text).strip():
                return
            box = pymupdf.Rect(left, cursor, right, cursor + size * 1.8)
            draw_text(page, box, str(text), font=font, font_size=size,
                      color=color, align=align)
            cursor = box.y1 + gap

        _line(title or "IN THE COURT OF THE DISTRICT JUDGE", 14, "hebo", gap=2)
        _line(court, 11, gap=2)
        if number:
            _line(f"Case No.: {number}", 11, gap=2)
        _line(bank, 11, "hebo", gap=10)
        if subtitle:
            _line(subtitle, 12, "hebo", align=ALIGN_LEFT, gap=8)

        if show_index and entries:
            row_h = 14.0
            available = page.rect.y1 - margin - cursor - 30
            rows = max(1, int(available / row_h))
            size = 10.0 if len(entries) <= rows else max(6.5, 10.0 * rows / len(entries))
            row_h = min(row_h, size * 1.55)
            for index, (name, first, last) in enumerate(entries[: max(1, int(available // row_h))], 1):
                label = f"{index}."
                name_box = pymupdf.Rect(left, cursor, left + width * 0.12, cursor + row_h)
                draw_text(page, name_box, label, font_size=size, align=ALIGN_LEFT)
                text_right = right - (width * 0.24 if show_page_range else 0)
                text_box = pymupdf.Rect(left + width * 0.12, cursor, text_right, cursor + row_h)
                draw_text(page, text_box, str(name)[:110], font_size=size, align=ALIGN_LEFT)
                if show_page_range:
                    page_box = pymupdf.Rect(text_right, cursor, right, cursor + row_h)
                    first_shown = first + page_offset
                    last_shown = last + page_offset
                    span = (f"{first_shown}" if first_shown == last_shown
                            else f"{first_shown} - {last_shown}")
                    draw_text(page, page_box, span, font_size=size, align=ALIGN_RIGHT)
                cursor += row_h

        _line("", 6, gap=6)
        from datetime import datetime as _dt

        _line(footer or f"Prepared on {_dt.now().strftime('%d-%m-%Y')} - generated locally, "
                        f"no online service used", 8, gap=0)
        _save_compact(doc, out_path)
        return doc.page_count
