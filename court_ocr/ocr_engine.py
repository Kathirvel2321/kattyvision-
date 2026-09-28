"""Local OCR engine - this is what removes the iLovePDF dependency.

How it works: each page is rendered to an image at a chosen DPI and handed to
Tesseract through PyMuPDF's ``Pixmap.pdfocr_tobytes()``.  The result is a page
that looks identical but carries a hidden, searchable text layer - the same
thing iLovePDF sells, except it runs on this PC, in parallel, for free.

Speed notes
  * ``mode="skip_text"`` (default) only OCRs pages that have no real text, so
    born-digital pleadings are copied through untouched.
  * Pages are processed as contiguous ranges by a pool of worker *processes*
    (Tesseract releases the GIL only partially), which is why this is orders of
    magnitude faster than any single-threaded web service.
"""

from __future__ import annotations

import logging
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pymupdf

from .toolcheck import find_tesseract, prepare_env

LOGGER = logging.getLogger("court_ocr")
MIN_TEXT_CHARS = 30
SERIAL_THRESHOLD = 4          # fewer pages than this -> no point spawning workers


@dataclass
class OcrResult:
    """Outcome of the OCR step."""

    path: Path
    pages_total: int = 0
    pages_ocrd: int = 0
    pages_passed_through: int = 0
    #: Pages whose first OCR answer looked wrong and were read again.
    pages_rechecked: int = 0
    #: Of those, the ones where the second attempt really did read more.
    pages_rescued: int = 0
    seconds: float = 0.0
    ok: bool = True
    detail: str = ""
    failed_ranges: list[str] = field(default_factory=list)

    @property
    def pages_per_second(self) -> float:
        return (self.pages_ocrd / self.seconds) if self.seconds > 0 else 0.0


def needs_ocr(page: pymupdf.Page, min_chars: int = MIN_TEXT_CHARS) -> bool:
    """True when a page has (almost) no extractable text."""
    try:
        text = page.get_text().strip()
    except Exception:
        return True
    return len(text) < min_chars


#: How much of a rendered page must be dark before we call it "has writing".
INK_MIN = 0.004
#: A real page of text yields well over 10,000 readable characters per unit of
#: ink.  Sitting well under that means the read lost most of the page, which is
#: the "the first render left letters out" case worth repeating.  Calibrated on
#: clean, faint and blurred pages: good ones score 15,000-30,000, a page that
#: lost most of its text scores far below this.
CHARS_PER_INK = 7000.0
#: How much darker than the page's own paper a pixel must be to count as ink.
#: A fixed cut-off was wrong: a faded photocopy with grey ~180 text measured as
#: completely blank, so those pages were never re-read.
INK_CONTRAST = 55


def ink_fraction(pix: pymupdf.Pixmap, sample: int = 160) -> float:
    """Roughly how much of the page is dark, in ``0..1``.

    Sampled rather than counted pixel by pixel: a 300 dpi A4 page is 8.7
    megapixels, and all we want is "is there writing here and how much of it",
    not an exact figure.  A stride keeps this well under a millisecond.

    "Dark" is measured against the page's *own* paper tone rather than a fixed
    grey, so a faded or yellowed scan is still recognised as having text.
    """
    try:
        width, height, n = pix.width, pix.height, pix.n
        if width < 2 or height < 2:
            return 0.0
        x_stride = max(1, width // sample)
        y_stride = max(1, height // sample)
        samples = pix.samples
        values: list[int] = []
        for y in range(0, height, y_stride):
            base = y * pix.stride
            for x in range(0, width, x_stride):
                # n == 1 is greyscale; n >= 3 take the first channel, which is
                # enough to tell ink from paper.
                values.append(samples[base + x * n])
        if not values:
            return 0.0
        values.sort()
        paper = values[int(len(values) * 0.80)]      # 80th percentile = the page
        cut = max(0, paper - INK_CONTRAST)           # anything below is ink
        return sum(1 for v in values if v < cut) / len(values)
    except Exception as exc:
        # Never let a measurement problem break a real OCR run, but do not
        # swallow it silently either - a silent 0.0 here looks exactly like a
        # blank page and quietly disables the second look everywhere.
        LOGGER.debug("[ocr] ink measurement skipped: %s", exc)
        return 0.0


def looks_incomplete(text: str, ink: float) -> bool:
    """True when a page clearly has writing but almost no text came back.

    This is the "the first pass dropped some letters" case.  Tesseract can miss
    a whole block - a faint header, a stamp, a rotated line - and the result
    still looks plausible enough that nothing else notices.  The tell is that
    the page has ink but the character count is far too low for it.
    """
    if ink < INK_MIN:
        return False                     # a blank or near-blank page: nothing lost
    letters = sum(1 for ch in text if ch.isalnum())
    return letters < ink * CHARS_PER_INK


def _page_text(data: bytes) -> str:
    """The text Tesseract put into a one-page OCR result."""
    try:
        with pymupdf.open("pdf", data) as single:
            return single[0].get_text() if single.page_count else ""
    except Exception:
        return ""


def _ocr_page(page: pymupdf.Page, dpi: int, language: str, tessdata: str,
              with_ink: bool = False) -> tuple[bytes, float]:
    """Render one page and hand it to Tesseract, keeping the original size.

    Returns the searchable page and, when asked, how much of it is dark.  The
    ink is measured off the *same* render that was OCRed, so it costs nothing:
    re-rendering just to look for missing letters would double the work.
    """
    pix = page.get_pixmap(dpi=dpi)
    if pix.alpha or pix.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    # Measure the ink BEFORE handing the buffer to Tesseract.  Once
    # ``pdfocr_tobytes`` has run, the pixmap's sample buffer is no longer
    # dependable and every read came back as zero.
    ink = ink_fraction(pix) if with_ink else 0.0
    # Setting the DPI back to the page's own DPI is what keeps the searchable
    # copy the same physical size as the scan, whatever we rendered at.
    pix.set_dpi(dpi, dpi)
    data = pix.pdfocr_tobytes(compress=True, language=language,
                              tessdata=tessdata or None)
    return data, ink


def plan_pages(
    doc_path: str | Path,
    *,
    mode: str = "skip_text",
    min_chars: int = MIN_TEXT_CHARS,
    force: bool = False,
) -> list[int]:
    """Return the 0-based page numbers that actually need OCR."""
    mode = (mode or "skip_text").lower()
    with pymupdf.open(doc_path) as doc:
        if mode in ("force", "all") or force:
            return list(range(doc.page_count))
        if mode in ("off", "none"):
            return []
        return [i for i in range(doc.page_count) if needs_ocr(doc[i], min_chars)]


def _ranges(pages: Sequence[int], chunk_count: int) -> list[tuple[int, int]]:
    """Split sorted pages into ``chunk_count`` contiguous, balanced ranges."""
    if not pages:
        return []
    chunk_count = max(1, min(chunk_count, len(pages)))
    per = math.ceil(len(pages) / chunk_count)
    out: list[tuple[int, int]] = []
    for start in range(0, len(pages), per):
        block = pages[start:start + per]
        out.append((block[0], block[-1]))
    return out


def _worker(task: tuple) -> tuple:
    """OCR one contiguous page range in a separate process.

    Returns ``(first, last, out_path, ok, message, ocrd, passed, rechecked,
    rescued)``.
    """
    (src, first, last, out_path, dpi, language, tessdata, force, min_chars,
     recheck, recheck_dpi) = task
    try:
        prepare_env(find_tesseract())          # child processes need PATH too
        src_doc = pymupdf.open(src)
        out = pymupdf.open()
        ocrd = passed = rechecked = rescued = 0
        try:
            for pno in range(first, last + 1):
                page = src_doc[pno]
                if not force and not needs_ocr(page, min_chars):
                    out.insert_pdf(src_doc, from_page=pno, to_page=pno)
                    passed += 1
                    continue
                data, ink = _ocr_page(page, dpi, language, tessdata,
                                      with_ink=recheck)

                # ---- the second look.  Check the first answer before moving
                # on: if the page has plenty of writing but almost no readable
                # characters came back, the first render clearly lost some, so
                # read it again sharper and keep whichever answer is better.
                if recheck and looks_incomplete(_page_text(data), ink):
                    rechecked += 1
                    try:
                        better, _ = _ocr_page(page, recheck_dpi, language, tessdata)
                    except Exception as exc:
                        LOGGER.warning(
                            "[ocr] second look failed on page %d: %s", pno + 1, exc)
                        better = b""
                    if better and len(_page_text(better)) > len(_page_text(data)):
                        data = better
                        rescued += 1

                with pymupdf.open("pdf", data) as single:
                    out.insert_pdf(single)
                ocrd += 1
            out.save(out_path, garbage=3, deflate=True)
        finally:
            out.close()
            src_doc.close()
        return (first, last, out_path, True, "", ocrd, passed, rechecked, rescued)
    except Exception as exc:  # report, never crash the pool
        return (first, last, "", False, f"{type(exc).__name__}: {exc}",
                0, 0, 0, 0)


def _coverage_ranges(
    needed: Sequence[int], total_pages: int, chunk_count: int
) -> list[tuple[int, int]]:
    """Balance work by OCR-need while still covering every page.

    ``needed`` decides where the boundaries go; the ranges are then widened so
    their union is ``0..total_pages-1`` with no gaps or overlaps.  Pages that
    need no OCR are simply passed through, so output page order always matches
    the input.
    """
    if not needed:
        return []
    seeds = _ranges(needed, chunk_count)
    ranges: list[tuple[int, int]] = []
    for index, (start, _) in enumerate(seeds):
        low = 0 if index == 0 else ranges[-1][1] + 1
        ranges.append((low, max(start, low)))
    for index in range(len(ranges) - 1):
        ranges[index] = (ranges[index][0], ranges[index + 1][0] - 1)
    ranges[-1] = (ranges[-1][0], total_pages - 1)
    return [(low, high) for low, high in ranges if high >= low]


def run_ocr(
    doc_path: str | Path,
    out_path: str | Path,
    *,
    language: str = "eng",
    dpi: int = 300,
    jobs: int = 0,
    mode: str = "skip_text",
    force: bool = False,
    timeout_minutes: float = 60.0,
    tessdata: str = "",
    progress=None,
    keep_parts: bool = False,
    recheck: bool = True,
    recheck_dpi: int = 0,
) -> OcrResult:
    """OCR only what needs it and write a searchable copy of ``doc_path``.

    ``recheck`` turns on the second look: after a page is read, its text is
    held against the amount of ink on the page, and anything that looks like a
    first pass which dropped letters is read again at ``recheck_dpi`` (higher
    than ``dpi`` unless told otherwise).  Only the pages that fail the check are
    read twice, so the cost is paid where the quality was actually poor.
    """
    import shutil

    doc_path, out_path = Path(doc_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = OcrResult(path=out_path)

    tool = find_tesseract()
    if not tool.available:
        raise RuntimeError(
            "Tesseract OCR engine not found. Run SETUP.ps1 once, or set "
            "'paths.tesseract_exe' in config.json."
        )
    prepare_env(tool)
    tessdata_dir = tessdata or tool.extra_dir
    # A second look is pointless at the same resolution, so always read the
    # recheck sharper than the first pass.
    recheck_dpi = int(recheck_dpi or 0) or max(int(dpi) * 2, int(dpi) + 150)

    with pymupdf.open(doc_path) as doc:
        total = doc.page_count
    result.pages_total = total
    needed = plan_pages(doc_path, mode=mode, force=force)

    if not needed:
        LOGGER.info("[ocr] all %d pages already have text - nothing to OCR", total)
        shutil.copy2(doc_path, out_path)
        result.pages_passed_through = total
        result.detail = "no pages required OCR"
        return result

    cpu = os.cpu_count() or 4
    workers = int(jobs) if jobs and int(jobs) > 0 else max(1, cpu - 1)
    if len(needed) <= SERIAL_THRESHOLD:
        workers = 1
    ranges = _coverage_ranges(needed, total, workers)
    LOGGER.info(
        "[ocr] %d of %d pages need OCR | engine: tesseract %s | %d worker(s) @ %d DPI",
        len(needed), total, tool.version or "?", len(ranges), dpi,
    )

    parts_dir = out_path.parent / f"_{out_path.stem}_ocr_parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir, ignore_errors=True)
    parts_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        (str(doc_path), low, high, str(parts_dir / f"part_{index:04d}.pdf"),
         int(dpi), language, tessdata_dir, force, MIN_TEXT_CHARS,
         bool(recheck), int(recheck_dpi))
        for index, (low, high) in enumerate(ranges)
    ]
    started = time.time()
    deadline = started + max(1.0, float(timeout_minutes)) * 60.0
    outcomes: dict[int, tuple] = {}

    if len(tasks) == 1:
        outcomes[0] = _worker(tasks[0])
        if progress:
            progress("[ocr] 1/1 chunk done")
    else:
        with ProcessPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {pool.submit(_worker, task): index for index, task in enumerate(tasks)}
            done = 0
            try:
                for future in as_completed(futures, timeout=max(30.0, deadline - time.time())):
                    index = futures[future]
                    outcomes[index] = future.result()
                    done += 1
                    if progress:
                        ocrd = outcomes[index][5] if outcomes[index][3] else 0
                        progress(f"[ocr] chunk {done}/{len(tasks)} done ({ocrd} pages OCRed)")
                    if time.time() > deadline:
                        LOGGER.warning("[ocr] time budget reached - finishing serially")
                        break
            except TimeoutError:
                LOGGER.warning("[ocr] worker timeout - finishing remaining chunks serially")
            for index, task in enumerate(tasks):        # never lose a page
                if index not in outcomes:
                    outcomes[index] = _worker(task)

    out = pymupdf.open()
    try:
        for index in range(len(tasks)):
            outcome = outcomes.get(index)
            low, high = ranges[index]
            if not outcome:
                result.failed_ranges.append(f"{low + 1}-{high + 1}: missing")
                continue
            (first, last, part, ok, message, ocrd, passed, rechecked,
             rescued) = outcome
            if not ok or not part or not Path(part).is_file():
                result.failed_ranges.append(f"{first + 1}-{last + 1}: {message}")
                LOGGER.error("[ocr] range %d-%d failed (%s) - copied without OCR",
                             first + 1, last + 1, message)
                with pymupdf.open(doc_path) as src:
                    out.insert_pdf(src, from_page=first, to_page=last)
                continue
            result.pages_ocrd += ocrd
            result.pages_passed_through += passed
            result.pages_rechecked += rechecked
            result.pages_rescued += rescued
            with pymupdf.open(part) as part_doc:
                out.insert_pdf(part_doc)

        result.pages_total = out.page_count
        if out.page_count != total:
            LOGGER.warning("[ocr] page count changed: %d -> %d", total, out.page_count)
        try:
            out.set_metadata(_metadata(doc_path, ocrd=result.pages_ocrd))
        except Exception as exc:
            LOGGER.debug("[ocr] metadata update skipped: %s", exc)
        out.save(str(out_path), garbage=3, deflate=True)
    finally:
        out.close()

    if not keep_parts:
        shutil.rmtree(parts_dir, ignore_errors=True)
    result.seconds = time.time() - started
    result.ok = not result.failed_ranges
    LOGGER.info(
        "[ocr] finished in %.1fs - %d pages OCRed, %d passed through (%.1f pages/s)",
        result.seconds, result.pages_ocrd, result.pages_passed_through,
        result.pages_per_second,
    )
    return result


def _metadata(doc_path: Path, *, ocrd: int) -> dict:
    """Metadata written into the searchable copy."""
    try:
        with pymupdf.open(doc_path) as src:
            meta = dict(src.metadata or {})
    except Exception:
        meta = {}
    meta.setdefault("title", doc_path.stem)
    meta["producer"] = "court_ocr (local Tesseract)"
    meta["keywords"] = f"local OCR; pages OCRed: {ocrd}"
    return meta
