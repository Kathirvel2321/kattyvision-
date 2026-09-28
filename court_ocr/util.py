"""Shared helpers for the court-filing PDF automation.

Pure-stdlib utilities only, so this module can be imported even before the
third-party dependencies are installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Iterable, Sequence

# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

MM_TO_PT = 72.0 / 25.4          # 1 mm expressed in PDF points
KB = 1024
MB = 1024 * 1024


def mm2pt(mm: float) -> float:
    """Convert millimetres to PDF points (1/72 inch)."""
    return mm * MM_TO_PT


def human_size(num_bytes: float) -> str:
    """Render a byte count as a compact, human readable string."""
    num_bytes = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(num_bytes)} B"
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} GB"


def size_mb(path: str | os.PathLike[str]) -> float:
    """Size of a file in megabytes."""
    return os.path.getsize(path) / MB


# --------------------------------------------------------------------------
# Sorting
# --------------------------------------------------------------------------

_NUM_CHUNK = re.compile(r"(\d+)")


def natural_key(text: str):
    """Sort key that orders 'Exhibit 2' before 'Exhibit 10'.

    Numbers are compared numerically, everything else case-insensitively.
    """
    parts = _NUM_CHUNK.split(str(text))
    key = []
    for part in parts:
        if part.isdigit():
            key.append((1, int(part), ""))
        elif part:
            key.append((0, 0, part.casefold()))
    return key


def sorted_naturally(items: Iterable[Path], use_natural: bool = True) -> list[Path]:
    """Return ``items`` sorted naturally (or plainly by name)."""
    items = list(items)
    if use_natural:
        return sorted(items, key=lambda p: natural_key(p.name))
    return sorted(items, key=lambda p: p.name.casefold())


# --------------------------------------------------------------------------
# Filesystem
# --------------------------------------------------------------------------

_INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WIN_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str, fallback: str = "output") -> str:
    """Make ``name`` usable as a Windows file name."""
    name = unicodedata.normalize("NFKC", str(name or "")).strip()
    name = _INVALID_WIN_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return fallback
    stem = name.split(".")[0].upper()
    if stem in _RESERVED_WIN_NAMES:
        name = "_" + name
    return name[:180]


def ensure_dir(path: str | os.PathLike[str]) -> Path:
    """Create ``path`` (and parents) if missing and return it as a Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def clear_dir(path: str | os.PathLike[str], keep: Sequence[str] = ()) -> None:
    """Delete every child of ``path`` except names listed in ``keep``."""
    p = Path(path)
    if not p.is_dir():
        return
    keep_set = set(keep)
    for child in p.iterdir():
        if child.name in keep_set:
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError:
            pass


def open_in_explorer(path: str | os.PathLike[str]) -> None:
    """Reveal ``path`` in Windows Explorer (best effort, never raises)."""
    try:
        target = Path(path)
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        elif target.is_dir():
            os.startfile(str(target))  # noqa: S606 - Windows shell open
    except Exception:  # pragma: no cover - purely cosmetic helper
        pass


# --------------------------------------------------------------------------
# Output naming
# --------------------------------------------------------------------------

def apply_template(template: str, **fields) -> str:
    """Format ``template`` with ``fields``, ignoring unknown placeholders.

    Supports the printf style widths used by the splitter, e.g. ``{index:02d}``.
    """

    class _Safe(dict):
        def __missing__(self, key):  # leave unknown {placeholders} untouched
            return "{" + key + "}"

    rendered = str(template).format_map(_Safe(**fields))
    return safe_filename(rendered, fallback="output.pdf")
