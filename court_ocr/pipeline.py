"""One-click end-to-end pipeline.

Turns the old hour-long manual routine into a single call::

    merge -> cover -> OCR -> compress -> paginate -> stamp -> split -> report

Every stage is optional and every stage is logged, so staff can see exactly
what happened, how long it took and how big each file became.
"""

from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import pymupdf

from . import ocr_engine, optimize, pdf_ops
from . import stamp as stamp_mod
from .config import PROJECT_ROOT, Config
from .logging_setup import StepTimer, get_logger
from .toolcheck import diagnose, languages as tesseract_languages
from .util import MB, apply_template, clear_dir, ensure_dir, open_in_explorer, safe_filename

LOGGER = logging.getLogger("court_ocr")

STEP_ORDER = ("merge", "cover", "ocr", "compress", "paginate", "stamp", "split")

ProgressFn = Callable[[str], None]


@dataclass
class PipelineReport:
    """Everything that happened during a run."""

    case: str = ""
    started: datetime = field(default_factory=datetime.now)
    finished: datetime | None = None
    steps: list[pdf_ops.StepResult] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: bool = True

    @property
    def elapsed(self) -> float:
        end = self.finished or datetime.now()
        return (end - self.started).total_seconds()

    def add(self, step: str, **kwargs) -> pdf_ops.StepResult:
        result = pdf_ops.StepResult(step=step, **kwargs)
        self.steps.append(result)
        return result

    def row(self, result: pdf_ops.StepResult) -> tuple[str, str]:
        size = ""
        if result.before_mb or result.after_mb:
            size = f"{result.before_mb:.2f} -> {result.after_mb:.2f} MB"
        detail = result.detail
        if result.pages:
            detail = f"{result.pages} pages; " + detail
        return (result.step, f"{'OK' if result.ok else 'CHECK'} | {size} | {detail}".strip())

    def to_csv(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Case", self.case])
            writer.writerow(["Started", self.started.strftime("%d-%m-%Y %H:%M:%S")])
            writer.writerow(["Finished",
                             (self.finished or datetime.now()).strftime("%d-%m-%Y %H:%M:%S")])
            writer.writerow(["Total time", f"{self.elapsed:.1f} s"])
            writer.writerow([])
            writer.writerow(["Step", "Status", "Size", "Pages", "Seconds", "Detail"])
            for result in self.steps:
                writer.writerow([
                    result.step, "OK" if result.ok else "CHECK",
                    f"{result.before_mb:.2f} -> {result.after_mb:.2f} MB" if result.after_mb else "",
                    result.pages or "", f"{result.seconds:.1f}", result.detail,
                ])
            writer.writerow([])
            writer.writerow(["Output files"])
            for output in self.outputs:
                writer.writerow([output.name, f"{output.stat().st_size / MB:.2f} MB"])
            if self.warnings:
                writer.writerow([])
                writer.writerow(["Warnings"])
                for warning in self.warnings:
                    writer.writerow([warning])
        return path


class Pipeline:
    """Runs the whole document workflow for one case."""

    def __init__(self, cfg: Config, progress: ProgressFn | None = None):
        self.cfg = cfg
        self.progress = progress or (lambda message: LOGGER.debug("%s", message))
        self.tools = diagnose(cfg)
        self.work: Path = ensure_dir(cfg.resolve("paths.work"))

    # ------------------------------------------------------------------ utils
    def _log_tools(self) -> None:
        for name, info in self.tools.items():
            LOGGER.info("tool %-11s : %s", name, info.status)

    def _record(
        self,
        report: PipelineReport,
        step: str,
        before: Path | None,
        after: Path | None,
        seconds: float,
        *,
        ok: bool = True,
        detail: str = "",
        pages: int = 0,
    ) -> pdf_ops.StepResult:
        result = report.add(
            step,
            ok=ok,
            path=after,
            before_mb=(before.stat().st_size / MB) if before and before.is_file() else 0.0,
            after_mb=(after.stat().st_size / MB) if after and after.is_file() else 0.0,
            pages=pages,
            seconds=seconds,
            detail=detail,
        )
        return result

    def _stamp_image(self) -> tuple[Path | None, bool]:
        """Resolve the seal image.

        Returns ``(path, is_placeholder)``.  A placeholder seal is a stand-in
        drawn by the program, NOT the bank's real seal: stamping one onto a
        court filing by accident would be a serious problem, so the caller is
        told about it and the step is reported as needing a look.
        """
        configured = Path(str(self.cfg.get("stamp.image", "") or ""))
        candidate = configured if configured.is_absolute() else (self.cfg.resolve("paths.stamp_image"))
        if candidate.is_file():
            return candidate, False
        fallback = self.cfg.resolve("paths.stamp_image")
        if fallback.is_file():
            return fallback, False
        # No real seal on this machine.  Ship a clearly marked placeholder, or
        # draw one, and say so loudly rather than pretending the step is fine.
        shipped = PROJECT_ROOT / "assets" / "stamp_PLACEHOLDER.png"
        if shipped.is_file():
            return shipped, True
        try:
            return stamp_mod.generate_seal_png(
                fallback,
                top_text=str(self.cfg.get("case.bank_name", "") or "GOVERNMENT BANK"),
                bottom_text=str(self.cfg.get("case.court", "") or ""),
                center_text="AUTHORISED SIGNATORY",
            ), True
        except Exception as exc:
            LOGGER.warning("could not create a placeholder seal: %s", exc)
            return None, False

    # -------------------------------------------------------------------- run
    def run(
        self,
        *,
        input_dir: str | Path | None = None,
        output_dir: str | Path | None = None,
        case_name: str = "",
        steps: Iterable[str] | None = None,
        clean_work: bool = True,
    ) -> PipelineReport:
        """Execute the configured steps and return a full report."""
        get_logger(self.cfg.resolve("paths.logs"),
                   verbose=bool(self.cfg.get("runtime.verbose", True)))
        started = time.time()
        input_dir = Path(input_dir) if input_dir else self.cfg.resolve("paths.input")
        output_dir = Path(output_dir) if output_dir else self.cfg.resolve("paths.output")
        case = safe_filename(case_name or self.cfg.case_name or Path(input_dir).name, "Case")
        wanted = {s.lower() for s in steps} if steps else set(STEP_ORDER)

        out_case = ensure_dir(Path(output_dir) / case)
        work = ensure_dir(self.work / case)
        if clean_work:
            clear_dir(work)
        report = PipelineReport(case=case)
        self._log_tools()
        LOGGER.info("=" * 78)
        LOGGER.info("CASE: %s   input: %s", case, input_dir)
        LOGGER.info("output: %s", out_case)

        # ---------------------------------------------------------- 1. collect
        extensions = self.cfg.get("merge.file_extensions") or [".pdf"]
        files = pdf_ops.collect_inputs(
            input_dir, extensions, natural=bool(self.cfg.get("merge.natural_sort", True))
        )
        if not files:
            raise ValueError(
                f"No documents found in {input_dir}. Put the case PDFs/images there "
                f"(or in a sub-folder) and run again."
            )
        LOGGER.info("found %d source document(s)", len(files))

        valid: list[Path] = []
        for path in files:
            if path.suffix.lower() == ".pdf":
                ok, why = pdf_ops.is_readable_pdf(path)
                if not ok:
                    report.warnings.append(f"skipped {path.name}: {why}")
                    LOGGER.warning("skipping %s (%s)", path.name, why)
                    continue
            valid.append(path)

        current = valid[0]
        pages = 0

        # ------------------------------------------------------------ 2. merge
        merge_target = work / "01_merged.pdf"
        with StepTimer(LOGGER, f"merge {len(valid)} document(s)") as timer:
            merged = pdf_ops.merge_files(
                valid,
                merge_target,
                create_bookmarks=bool(self.cfg.get("merge.create_bookmarks", True)),
                blank_separator=bool(self.cfg.get("merge.separator_blank_page", False)),
                progress=self.progress,
                on_error="abort" if self.cfg.get("runtime.abort_on_error") else "skip",
            )
        current, pages = merged.path, merged.pages
        for name, why in merged.skipped:
            report.warnings.append(f"merge skipped {name}: {why}")
        source_mb = sum(p.stat().st_size for p in valid if p.is_file()) / MB
        report.add(
            "merge", ok=merged.pages > 0, path=current, before_mb=source_mb,
            after_mb=current.stat().st_size / MB, pages=merged.pages, seconds=timer.seconds,
            detail=(f"{merged.included} document(s) -> 1 PDF"
                    + (f"; {len(merged.skipped)} skipped" if merged.skipped else "")),
        )

        # ------------------------------------------------------------ 3. cover
        if "cover" in wanted and self.cfg.get("cover_page.enabled"):
            cover_target = work / "02_cover.pdf"
            with StepTimer(LOGGER, "cover / index page") as timer:
                new_pages = pdf_ops.add_cover_page(
                    current, cover_target, entries=merged.entries,
                    title=str(self.cfg.get("cover_page.title", "") or ""),
                    subtitle=str(self.cfg.get("cover_page.subtitle", "") or ""),
                    court=str(self.cfg.get("case.court", "") or ""),
                    bank=str(self.cfg.get("case.bank_name", "") or ""),
                    number=str(self.cfg.get("case.case_number", "") or ""),
                    show_index=bool(self.cfg.get("cover_page.show_document_index", True)),
                )
            self._record(report, "cover", current, cover_target, timer.seconds,
                         detail="index page inserted at the front", pages=new_pages)
            current, pages = cover_target, new_pages

        # -------------------------------------------------------------- 4. OCR
        if "ocr" in wanted and self.cfg.get("ocr.enabled"):
            tesseract = self.tools["tesseract"]
            if not tesseract.available:
                note = ("Tesseract is not installed - OCR skipped. Run SETUP.ps1 "
                        "(one UAC click), then run this job again.")
                LOGGER.warning("%s", note)
                report.warnings.append(note)
                self._record(report, "ocr", current, current, 0.0, ok=False,
                             detail="skipped: Tesseract missing", pages=pages)
            else:
                ocr_target = work / "03_ocr.pdf"
                lang = str(self.cfg.get("ocr.language", "eng") or "eng")
                installed = set(tesseract_languages(tesseract))
                missing = [c for c in lang.split("+") if installed and c not in installed]
                if missing:
                    note = (f"Tesseract language pack(s) {missing} not installed - "
                            f"using 'eng' instead")
                    LOGGER.warning("%s", note)
                    report.warnings.append(note)
                    lang = "eng"
                with StepTimer(LOGGER, f"OCR (tesseract, lang={lang})") as timer:
                    ocr = ocr_engine.run_ocr(
                        current, ocr_target,
                        language=lang,
                        dpi=int(self.cfg.get("ocr.oversample_dpi", 300) or 300),
                        jobs=int(self.cfg.get("ocr.jobs", 0) or 0),
                        mode=str(self.cfg.get("ocr.mode", "skip_text") or "skip_text"),
                        force=bool(self.cfg.get("ocr.force_full_page_ocr", False)),
                        timeout_minutes=float(self.cfg.get("ocr.timeout_minutes", 60) or 60),
                        progress=self.progress,
                        keep_parts=bool(self.cfg.get("output.keep_intermediate_files", False)),
                        recheck=bool(self.cfg.get("ocr.recheck_weak_pages", True)),
                        recheck_dpi=int(self.cfg.get("ocr.recheck_dpi", 0) or 0),
                    )
                detail = (f"{ocr.pages_ocrd} page(s) OCRed, {ocr.pages_passed_through} already "
                          f"had text ({ocr.pages_per_second:.1f} pages/s)")
                if ocr.pages_rechecked:
                    # Say it plainly: these are the pages where the first read
                    # looked wrong and was repeated, not every page.
                    detail += (f"; {ocr.pages_rechecked} looked doubtful and were read "
                               f"again, {ocr.pages_rescued} read better")
                if ocr.failed_ranges:
                    detail += f"; failed ranges: {', '.join(ocr.failed_ranges)}"
                    report.warnings.append(f"OCR problems: {ocr.failed_ranges}")
                self._record(report, "ocr", current, ocr.path, timer.seconds,
                             ok=ocr.ok, detail=detail, pages=ocr.pages_total)
                current, pages = ocr.path, ocr.pages_total

        # -------------------------------------------------------- 5. compress
        if "compress" in wanted and self.cfg.get("compress.enabled"):
            target = float(self.cfg.get("compress.target_size_mb", 0) or 0)
            # split.max_size_mb is the opt-in *splitting* rule; when it is set it
            # also becomes a reasonable compression target.  It is 0 by default,
            # so the portal's MB limit above is what compression chases.
            target = float(self.cfg.get("split.max_size_mb", 0) or 0) or target
            comp_target = work / "04_compressed.pdf"
            with StepTimer(LOGGER, "compress") as timer:
                if str(self.cfg.get("compress.engine", "adaptive")).lower() == "levels":
                    comp = pdf_ops.compress_pdf(
                        current, comp_target,
                        level=int(self.cfg.get("compress.level", 0) or 0),
                        levels_to_try=self.cfg.get("compress.levels_to_try") or [1, 2, 3],
                        target_mb=target,
                        dpi=int(self.cfg.get("compress.image_downsample_dpi", 150) or 150),
                        jpeg_quality=int(self.cfg.get("ocr.jpeg_quality", 75) or 75),
                        grayscale=bool(self.cfg.get("compress.grayscale_below_mb", 0)),
                        gs_exe=self.tools["ghostscript"].path,
                        progress=self.progress,
                    )
                    note = f"{comp.saving_pct:.0f}% smaller via {comp.engine}"
                else:
                    comp = optimize.optimize_pdf(
                        current, comp_target,
                        preset=str(self.cfg.get("quality.preset", "balanced") or "balanced"),
                        text_dpi=self._quality_int("quality.text_dpi"),
                        photo_dpi=self._quality_int("quality.photo_dpi"),
                        photo_quality=self._quality_int("quality.photo_quality"),
                        gray_quality=self._quality_int("quality.gray_quality"),
                        threshold=self._quality_int("quality.threshold"),
                        bitonal=bool(self.cfg.get("quality.bitonal", True)),
                        scrub_metadata=bool(self.cfg.get("quality.scrub_metadata", True)),
                        target_mb=target,
                        progress=self.progress,
                    )
                    note = comp.detail
            if not comp.ok:
                note += f"; still above the {target:.2f} MB portal limit"
                report.warnings.append(
                    f"merged PDF is {comp.after_mb:.2f} MB - above {target:.2f} MB "
                    f"even after compression; the splitter will keep parts within the limit"
                )
            self._record(report, "compress", current, comp.path, timer.seconds,
                         ok=comp.ok, detail=note, pages=pages)
            current = comp.path

        # ------------------------------------------------------- 6. paginate
        if "paginate" in wanted and self.cfg.get("pagination.enabled"):
            page_target = work / "05_paginated.pdf"
            with StepTimer(LOGGER, "pagination") as timer:
                pag = pdf_ops.add_page_numbers(
                    current, page_target,
                    fmt=str(self.cfg.get("pagination.format", "Page {page} of {total}")),
                    position=str(self.cfg.get("pagination.position", "bottom-center")),
                    font=str(self.cfg.get("pagination.font", "helv")),
                    font_size=float(self.cfg.get("pagination.font_size", 9) or 9),
                    margin_mm=float(self.cfg.get("pagination.margin_mm", 8) or 8),
                    start_number=int(self.cfg.get("pagination.start_number", 1) or 1),
                    skip_first=int(self.cfg.get("pagination.skip_first_pages", 0) or 0),
                    skip_last=int(self.cfg.get("pagination.skip_last_pages", 0) or 0),
                    style=str(self.cfg.get("pagination.style", "arabic") or "arabic"),
                    background=bool(self.cfg.get("pagination.background", False)),
                    progress=self.progress,
                )
            self._record(report, "paginate", current, pag.path, timer.seconds,
                         ok=pag.ok, detail=f"{pag.pages_numbered} page(s) numbered",
                         pages=pages)
            current = pag.path

        # ---------------------------------------------------------- 7. stamp
        if "stamp" in wanted and self.cfg.get("stamp.enabled"):
            stamp_target = work / "06_stamped.pdf"
            seal, seal_is_placeholder = self._stamp_image()
            if seal is None:
                note = ("no seal image found and none could be created - the seal was "
                        "NOT applied. Add the bank's real assets\\stamp.png and run again.")
                LOGGER.error("%s", note)
                report.warnings.append(note)
                self._record(report, "stamp", current, current, 0.0, ok=False,
                             detail="skipped: no seal image available", pages=pages)
            else:
                if seal_is_placeholder:
                    # Court-safety: a stand-in seal must never look like success.
                    note = ("a PLACEHOLDER seal was stamped, not the bank's real seal. "
                            "Replace assets\\stamp.png with the official transparent PNG "
                            "before filing anything with the court.")
                    LOGGER.error("%s", note)
                    report.warnings.append(note)
                text_cfg = dict(self.cfg.get("stamp.text", {}) or {})
                if not text_cfg.get("lines") and self.cfg.get("case.bank_name"):
                    text_cfg["lines"] = [str(self.cfg.get("case.bank_name"))]
                with StepTimer(LOGGER, "stamp") as timer:
                    stamped = stamp_mod.apply_stamp(
                        current, stamp_target,
                        image_path=seal,
                        pages=str(self.cfg.get("stamp.pages", "all") or "all"),
                        position=str(self.cfg.get("stamp.position", "bottom-right")),
                        width_percent=float(self.cfg.get("stamp.width_percent", 20) or 20),
                        offset_x_mm=float(self.cfg.get("stamp.offset_x_mm", 12) or 0),
                        offset_y_mm=float(self.cfg.get("stamp.offset_y_mm", 12) or 0),
                        nudge_x_mm=float(self.cfg.get("stamp.nudge_x_mm", 0) or 0),
                        nudge_y_mm=float(self.cfg.get("stamp.nudge_y_mm", 0) or 0),
                        opacity=float(self.cfg.get("stamp.opacity", 0.9) or 0.9),
                        rotation=int(self.cfg.get("stamp.image_rotation", 0) or 0),
                        remove_white=bool(self.cfg.get("stamp.remove_white", False)),
                        white_threshold=int(self.cfg.get("stamp.remove_white_threshold", 235) or 235),
                        cache_dir=work,
                        text=text_cfg,
                        progress=self.progress,
                    )
                seal_note = "PLACEHOLDER seal" if seal_is_placeholder else "seal"
                self._record(
                    report, "stamp", current, stamped.path, timer.seconds,
                    ok=stamped.ok and not seal_is_placeholder,
                    detail=(f"{seal_note} placed on {stamped.pages_stamped} page(s)"
                            + (f"; text stamp on {stamped.text_lines} page(s)"
                               if stamped.text_lines else "")
                            + ("" if stamped.ok else f"; {stamped.detail}")),
                    pages=pages,
                )
                current = stamped.path

        # ------------------------------------------------------------ 8. split
        final_name = self.cfg.case_field(
            str(self.cfg.get("output.final_name_template", "{case}_Final.pdf"))
        )
        final_path = out_case / final_name
        outputs: list[Path] = []
        if "split" in wanted and self.cfg.get("split.enabled"):
            with StepTimer(LOGGER, "split") as timer:
                parts = pdf_ops.split_pdf(
                    current, out_case,
                    mode=str(self.cfg.get("split.mode", "pages") or "pages"),
                    max_pages=int(self.cfg.get("split.max_pages_per_file", 50) or 50),
                    max_size_mb=float(self.cfg.get("split.max_size_mb", 0) or 0),
                    keep_bookmark_boundaries=bool(
                        self.cfg.get("split.keep_bookmark_boundaries", True)),
                    name_template=str(self.cfg.get("split.name_template",
                                                   "{case}_Part{index:02d}.pdf")),
                    case=case,
                    court=str(self.cfg.get("case.court", "") or ""),
                    bank=str(self.cfg.get("case.bank_name", "") or ""),
                    number=str(self.cfg.get("case.case_number", "") or ""),
                    progress=self.progress,
                )
            outputs = list(parts.files)
            limit = int(self.cfg.get("split.max_pages_per_file", 50) or 50)
            mode_used = str(self.cfg.get("split.mode", "pages") or "pages").lower()
            if len(outputs) == 1:
                single = outputs[0]
                if single.name != final_name:
                    if final_path.exists():
                        final_path.unlink()
                    single.replace(final_path)
                    outputs = [final_path]
                detail = "whole filing fits in one file - no split was needed"
            else:
                # Say exactly what came out, so "I asked for 40 and got 20" can
                # never happen quietly again.
                counts = ", ".join(str(self._page_count(f)) for f in outputs)
                biggest = max((f.stat().st_size / MB) for f in outputs) if outputs else 0.0
                rule = (f"by page limit ({limit}/file)" if mode_used == "pages"
                        else f"by page limit ({limit}/file) and file size")
                detail = (f"{len(outputs)} part(s) {rule}: {counts} page(s); "
                          f"largest part {biggest:.2f} MB")
            for note in parts.over_size:
                # Informational, not a problem: the user asked for this page
                # count, so the size rule did not get a vote.
                LOGGER.info("[split] %s", note)
            for note in parts.skipped:
                report.warnings.append(note)
            written_mb = sum((f.stat().st_size / MB) for f in outputs) if outputs else 0.0
            self._record(report, "split", current, outputs[0] if outputs else None,
                         timer.seconds, ok=bool(outputs), detail=detail, pages=pages)
            if report.steps:                      # show total bytes written, not just part 1
                report.steps[-1].after_mb = written_mb
        else:
            if current.resolve() != final_path.resolve():
                if final_path.exists():
                    final_path.unlink()
                current.replace(final_path)
            outputs = [final_path]

        # ----------------------------------------------------------- 9. finish
        report.finished = datetime.now()
        report.outputs = outputs
        if bool(self.cfg.get("output.write_report", True)) and outputs:
            report.to_csv(out_case / f"{Path(final_name).stem}_report.csv")
        if not bool(self.cfg.get("output.keep_intermediate_files", False)):
            clear_dir(work)

        LOGGER.info("-" * 78)
        for result in report.steps:
            LOGGER.info("  %-10s %s", result.step, report.row(result)[1])
        for warning in report.warnings:
            LOGGER.warning("  ! %s", warning)
        LOGGER.info("-" * 78)
        LOGGER.info("FINISHED in %.1f s -> %d file(s) in %s",
                    report.elapsed, len(outputs), out_case)
        for output in outputs:
            LOGGER.info("   %-58s %8.2f MB  (%d pages)",
                        output.name, output.stat().st_size / MB,
                        self._page_count(output))
        report.ok = all(step.ok for step in report.steps)
        if bool(self.cfg.get("output.open_output_folder", False)):
            open_in_explorer(out_case)
        return report

    def _quality_int(self, key: str) -> int | None:
        """Optional quality override from config; 0/blank means "use the preset"."""
        try:
            value = int(self.cfg.get(key, 0) or 0)
        except (TypeError, ValueError):
            return None
        return value or None

    @staticmethod
    def _page_count(path: Path) -> int:
        try:
            with pymupdf.open(path) as doc:
                return doc.page_count
        except Exception:
            return 0
