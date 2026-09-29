"""Configuration loading / merging for the automation.

The config is a plain nested dict loaded from ``config.json``.  Missing keys
fall back to :data:`DEFAULTS`, so an older config file keeps working after an
upgrade and staff never have to edit code.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

LOGGER = logging.getLogger("court_ocr")

DEFAULTS: dict[str, Any] = {
    "case": {
        "name": "",
        "court": "",
        "bank_name": "",
        "case_number": "",
    },
    "paths": {
        "input": "input",
        "output": "output",
        "work": "work",
        "logs": "logs",
        "stamp_image": "assets/stamp.png",
        "tesseract_exe": "",
        "ghostscript_exe": "",
    },
    "merge": {
        "enabled": True,
        "natural_sort": True,
        "separator_blank_page": False,
        "create_bookmarks": True,
        "file_extensions": [".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"],
    },
    "cover_page": {
        "enabled": False,
        "title": "",
        "subtitle": "Index of Documents",
        "show_document_index": True,
        "show_page_numbers_in_index": True,
    },
    "ocr": {
        "enabled": True,
        "language": "eng",
        "mode": "skip_text",
        "jobs": 0,
        "optimize": 1,
        "jpeg_quality": 75,
        "png_quality": 50,
        "deskew": False,
        "rotate_pages": False,
        "force_full_page_ocr": False,
        "oversample_dpi": 300,
        # After each page is read, the text is checked against how much ink is
        # on the page.  A page full of writing that comes back as almost nothing
        # is read again at a higher resolution.  0 = use twice the first
        # resolution.
        "recheck_weak_pages": True,
        "recheck_dpi": 0,
        "timeout_minutes": 60,
    },
    "compress": {
        "enabled": True,
        "engine": "adaptive",            # "adaptive" = optimize.py, "levels" = old
        "target_size_mb": 4.5,
        "level": 2,
        "levels_to_try": [1, 2, 3],
        "image_downsample_dpi": 150,
        "grayscale_below_mb": 0,
    },
    "quality": {
        # adaptive optimizer (court_ocr/optimize.py) - 0 = use the preset value
        "preset": "balanced",           # archival | high | balanced | small
        "text_dpi": 0,                  # scanned text pages: 1-bit at this dpi
        "photo_dpi": 0,                 # colour/gray pages: JPEG at this dpi
        "photo_quality": 0,             # colour JPEG quality
        "gray_quality": 0,              # grayscale JPEG quality
        "bitonal": True,                # turned off = text pages stay grayscale
        "threshold": 0,                 # 0 = automatic (160)
        "scrub_metadata": True,         # blank author/creator/producer, drop XMP
    },
    "pagination": {
        "enabled": True,
        "format": "Page {page} of {total}",
        "position": "bottom-center",
        "font": "helv",
        "font_file": "",
        "font_size": 9,
        "margin_mm": 8,
        "start_number": 1,
        "skip_first_pages": 0,
        "skip_last_pages": 0,
    },
    "stamp": {
        "enabled": True,
        "image": "assets/stamp.png",
        "pages": "all",
        "position": "bottom-right",
        "width_percent": 20,
        "offset_x_mm": 12,
        "offset_y_mm": 12,
        # Screen-direction nudges, set with the arrow buttons beside the seal
        # preview.  Positive x moves right and positive y moves down, whichever
        # corner the seal happens to be anchored to.
        "nudge_x_mm": 0,
        "nudge_y_mm": 0,
        "opacity": 0.85,
        "image_rotation": 0,
        "text": {
            "enabled": False,
            "lines": [],
            "position": "top-right",
            "font": "helv",
            "font_size": 10,
            "color": [0.0, 0.0, 0.0],
            "line_spacing": 13,
            "width_percent": 38,
            "offset_x_mm": 8,
            "offset_y_mm": 14,
            "date_format": "%d-%m-%Y",
            "include_date": False,
        },
    },
    "split": {
        "enabled": True,
        # "pages" = only the page limit decides.  "size" / "both" additionally
        # obey split.max_size_mb - but that is off by default, because re-cutting
        # a part the user already sized to 40 pages is what breaks the flow.
        "mode": "pages",
        "max_pages_per_file": 50,
        "max_size_mb": 0.0,          # 0 = never split by file size
        "keep_bookmark_boundaries": True,
        "name_template": "{case}_Part{index:02d}.pdf",
    },
    "output": {
        "final_name_template": "{case}_Final.pdf",
        "write_report": True,
        "write_report_pdf": False,
        "keep_intermediate_files": False,
        "open_output_folder": True,
    },
    "runtime": {
        "verbose": True,
        "abort_on_error": False,
    },
    "ui": {
        "preset": "standard",
        "recent_folders": [],
    },
}


def _is_inside(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or lives underneath it."""
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:                      # different drives
        return False


def _describe_foreign(path: Path, root: Path) -> str:
    """Describe a path that belongs to some *other* installation, or "".

    Only paths that look like another copy of this project (a folder holding a
    ``court_ocr`` package, or a case folder such as ``output/<CASE>``) are
    second-guessed.  A deliberately configured external folder - a network
    share, say - is still honoured.
    """
    if _is_inside(path, root):
        return ""
    # A generic ancestor named ``work`` or ``output`` is common on shared
    # drives and in temporary validation folders.  Treat a path as belonging
    # to another installation only when its layout is backed by the package.
    layout_dirs = {"input", "output", "work", "logs"}
    looks_like_ours = (path / "court_ocr").is_dir() or any(
        part.name.lower() in layout_dirs and (part.parent / "court_ocr").is_dir()
        for part in (path, *path.parents)
    )
    return str(path) if looks_like_ours else ""


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (``base`` is not modified)."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Config:
    """Nested-dict wrapper with dotted access and path resolution."""

    def __init__(self, data: dict[str, Any] | None = None, source: Path | None = None,
                 extras: dict[str, Any] | None = None):
        self.data = _deep_merge(DEFAULTS, data or {})
        self.source = source
        self.extras = dict(extras or {})     # "_comment" style keys, kept on save

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """Load a config file, falling back to defaults when absent/invalid."""
        path = Path(path) if path is not None else (PROJECT_ROOT / "config.json")
        if not path.is_file():
            return cls({}, source=path)
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        extras = {k: v for k, v in raw.items() if str(k).startswith("_")}
        body = {k: v for k, v in raw.items() if not str(k).startswith("_")}
        return cls(body, source=path, extras=extras)

    def save(self, path: str | Path | None = None) -> Path:
        """Persist the current (merged) configuration as JSON."""
        target = Path(path) if path else (self.source or PROJECT_ROOT / "config.json")
        payload: dict[str, Any] = dict(self.extras)
        payload.update({k: v for k, v in self.data.items() if not str(k).startswith("_")})
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return target

    # ----------------------------------------------------------------- access
    def get(self, dotted: str, default: Any = None) -> Any:
        """``cfg.get("ocr.language")`` -> ``"eng"``."""
        node: Any = self.data
        for part in dotted.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set(self, dotted: str, value: Any) -> None:
        """``cfg.set("ocr.enabled", False)``."""
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def update(self, other: dict[str, Any]) -> None:
        self.data = _deep_merge(self.data, other)

    # ------------------------------------------------------------------ paths
    def resolve(self, dotted: str) -> Path:
        """Resolve a configured path relative to the project root.

        Absolute paths are honoured, but one that points at a *different*
        project folder than this one is treated as a leftover from another
        machine and ignored.  Without this, a config.json copied from a
        colleague's PC would quietly read and write case documents in their
        user profile instead of this installation - which looks like it worked.
        """
        raw = str(self.get(dotted, "") or "").strip().replace("\\", "/")
        if not raw:
            return PROJECT_ROOT
        p = Path(raw)
        if p.is_absolute():
            resolved = p
            # A config pointing into another user's profile is a stale copy.
            if not _is_inside(resolved, PROJECT_ROOT):
                other = _describe_foreign(resolved, PROJECT_ROOT)
                if other:
                    LOGGER.warning(
                        "config %s points at %s, which is not this installation (%s); "
                        "using the local folder instead. Fix or remove that setting.",
                        dotted, resolved, PROJECT_ROOT,
                    )
                    return PROJECT_ROOT / p.name
            return resolved
        return PROJECT_ROOT / p

    # ------------------------------------------------------------------ flags
    @property
    def case_name(self) -> str:
        return str(self.get("case.name", "") or "").strip()

    def case_field(self, template: str) -> str:
        """Format a name template using the case metadata."""
        return str(template).format(
            case=self.case_name or "Case",
            court=str(self.get("case.court", "") or "Court"),
            bank=str(self.get("case.bank_name", "") or "Bank"),
            number=str(self.get("case.case_number", "") or "Case"),
        )

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)
