"""Command line entry point.

    python -m court_ocr.cli --doctor
    python -m court_ocr.cli "input\\SAMPLE CASE" -c "S A 245 of 2026"
    python -m court_ocr.cli --set split.max_pages_per_file=30 --set ocr.enabled=false

Any setting in config.json can be overridden with ``--set key=value``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import PROJECT_ROOT, Config
from .pipeline import STEP_ORDER, Pipeline
from .toolcheck import diagnose, languages
from .util import MB, open_in_explorer


def _coerce(text: str):
    """Turn a CLI string into a sensible Python value."""
    low = text.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="court_ocr",
        description="Merge, OCR, compress, number, stamp and split court filings - "
                    "fully offline, no subscription.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example:\n"
               "  python -m court_ocr.cli \"input\\SAMPLE CASE\" -c \"S.A. 245/2026\"\n",
    )
    parser.add_argument("input", nargs="?", help="folder holding the case documents")
    parser.add_argument("-o", "--output", help="folder for the finished files")
    parser.add_argument("-c", "--case", default="", help="case name used in file names")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"),
                        help="path to config.json")
    parser.add_argument("--steps", help="comma separated subset of: " + ", ".join(STEP_ORDER))
    parser.add_argument("--no-ocr", action="store_true", help="skip OCR")
    parser.add_argument("--no-compress", action="store_true", help="skip compression")
    parser.add_argument("--no-split", action="store_true", help="produce a single file")
    parser.add_argument("--no-stamp", action="store_true", help="skip the bank seal")
    parser.add_argument("--cover", action="store_true", help="add an index/cover page")
    parser.add_argument("--max-pages", type=int, help="split: max pages per part")
    parser.add_argument("--max-mb", type=float, help="portal file size limit in MB")
    parser.add_argument("--jobs", type=int, help="OCR worker processes (default: cores-1)")
    parser.add_argument("--dpi", type=int, help="OCR render resolution (default 300)")
    parser.add_argument("--quality", choices=("archival", "high", "balanced", "small"),
                        help="output quality preset (adaptive optimizer)")
    parser.add_argument("--stamp-image", help="path to the bank seal PNG")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override any config value, repeatable")
    parser.add_argument("--doctor", action="store_true",
                        help="check installed tools and exit")
    parser.add_argument("--make-seal", action="store_true",
                        help="generate a placeholder seal image and exit")
    parser.add_argument("--quiet", action="store_true", help="only log warnings/errors")
    parser.add_argument("--version", action="version", version=f"court_ocr {__version__}")
    return parser


def _doctor(cfg: Config) -> int:
    """Report whether the machine can run the workflow."""
    tools = diagnose(cfg)
    print(f"court_ocr {__version__}   project: {PROJECT_ROOT}")
    print("-" * 70)
    for name, info in tools.items():
        print(f"  [{'OK ' if info.available else 'MISS'}] {name:<12} {info.status}")
    tesseract = tools["tesseract"]
    if tesseract.available:
        packs = languages(tesseract)
        print(f"       language packs  : {', '.join(packs) if packs else 'none found'}")
        if tesseract.extra_dir:
            print(f"       tessdata folder : {tesseract.extra_dir}")
    if not tools["ghostscript"].available:
        print("  note: Ghostscript is optional - it only adds an extra squeeze "
              "at the end (see SETUP.ps1).")
    print("-" * 70)
    for label, key in (("input ", "paths.input"), ("output", "paths.output"),
                       ("work  ", "paths.work"), ("logs  ", "paths.logs")):
        target = cfg.resolve(key)
        print(f"  {label} : {target}   {'exists' if target.exists() else 'missing'}")
    print("-" * 70)
    if tesseract.available:
        print("READY: OCR, compression and splitting can all run locally.")
        return 0
    print("OCR is not available yet. Tesseract is free/open-source - run SETUP.ps1 once.")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)

    for override in args.set:
        if "=" not in override:
            print(f"ignoring malformed --set value: {override}")
            continue
        key, _, value = override.partition("=")
        cfg.set(key.strip(), _coerce(value))

    if args.input:
        cfg.set("paths.input", args.input)
    if args.output:
        cfg.set("paths.output", args.output)
    if args.case:
        cfg.set("case.name", args.case)
    if args.no_ocr:
        cfg.set("ocr.enabled", False)
    if args.no_compress:
        cfg.set("compress.enabled", False)
    if args.no_split:
        cfg.set("split.enabled", False)
    if args.no_stamp:
        cfg.set("stamp.enabled", False)
    if args.cover:
        cfg.set("cover_page.enabled", True)
    if args.max_pages:
        cfg.set("split.max_pages_per_file", args.max_pages)
    if args.max_mb:
        cfg.set("split.max_size_mb", args.max_mb)
        cfg.set("compress.target_size_mb", args.max_mb)
    if args.jobs is not None:
        cfg.set("ocr.jobs", args.jobs)
    if args.dpi:
        cfg.set("ocr.oversample_dpi", args.dpi)
    if args.quality:
        cfg.set("quality.preset", args.quality)
    if args.stamp_image:
        cfg.set("stamp.image", args.stamp_image)
    if args.quiet:
        cfg.set("runtime.verbose", False)

    if args.doctor:
        return _doctor(cfg)

    if args.make_seal:
        from .stamp import generate_seal_png

        path = generate_seal_png(
            cfg.resolve("paths.stamp_image"),
            top_text=str(cfg.get("case.bank_name", "") or "GOVERNMENT BANK"),
            bottom_text=str(cfg.get("case.court", "") or ""),
        )
        print(f"placeholder seal written to: {path}")
        return 0

    steps = [s.strip() for s in args.steps.split(",")] if args.steps else None
    progress = None if args.quiet else (lambda message: print(f"   {message}", flush=True))
    try:
        pipeline = Pipeline(cfg, progress=progress)
        report = pipeline.run(case_name=args.case, steps=steps)
    except Exception as exc:                       # noqa: BLE001 - top level guard
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 2

    print()
    print("=" * 70)
    print(f"case      : {report.case}")
    print(f"total time: {report.elapsed:.1f} s")
    print("=" * 70)
    for result in report.steps:
        status, detail = report.row(result)
        print(f"  {result.step:<10} {status}")
        if detail:
            print(f"             {detail}")
    print("=" * 70)
    for output in report.outputs:
        print(f"  -> {output}   ({output.stat().st_size / MB:.2f} MB)")
    if report.warnings:
        print("-" * 70)
        for warning in report.warnings:
            print(f"  ! {warning}")
    print("=" * 70)
    if args.output or cfg.get("output.open_output_folder"):
        open_in_explorer(report.outputs[0].parent if report.outputs else
                         cfg.resolve("paths.output"))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
