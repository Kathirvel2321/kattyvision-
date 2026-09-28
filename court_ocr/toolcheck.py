"""Locate the optional external helpers (Tesseract, Ghostscript).

Nothing here is mandatory: if a tool is missing the pipeline degrades
gracefully instead of crashing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

TESSERACT_HINTS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Tesseract-OCR\tesseract.exe",
    r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
    r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)

GHOSTSCRIPT_HINTS = (
    r"C:\Program Files\gs\gs10.08.0\bin\gswin64c.exe",
    r"C:\Program Files (x86)\gs\gs10.08.0\bin\gswin64c.exe",
    r"C:\Program Files\gs\*\bin\gswin64c.exe",
    r"C:\Program Files (x86)\gs\*\bin\gswin64c.exe",
    r"%LOCALAPPDATA%\Programs\gs\*\bin\gswin64c.exe",
    r"C:\gs\*\bin\gswin64c.exe",
    "gswin64c",
    "gswin32c",
    "gs",
)


@dataclass
class ToolInfo:
    """Result of probing for an optional helper."""

    name: str
    path: str = ""
    version: str = ""
    extra_dir: str = ""      # e.g. Tesseract's tessdata folder

    @property
    def available(self) -> bool:
        return bool(self.path)

    @property
    def status(self) -> str:
        if not self.available:
            return "not found"
        return f"{self.version or 'found'} -> {self.path}"


def _run_version(cmd: list[str]) -> str:
    """Run a ``--version`` style command and return the first output line."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=CREATE_NO_WINDOW,
        )
        blob = (proc.stdout or "") + (proc.stderr or "")
        for line in blob.splitlines():
            line = line.strip()
            if line:
                return line[:120]
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _glob_paths(pattern: str) -> list[str]:
    try:
        from glob import glob

        return sorted(glob(pattern), reverse=True)
    except Exception:  # pragma: no cover
        return []


def _find(explicit: str, hints: tuple[str, ...], which: tuple[str, ...]) -> str:
    """Return the first existing candidate: explicit config > PATH > hints."""
    if explicit:
        candidate = Path(os.path.expandvars(explicit))
        if candidate.is_file():
            return str(candidate)
    for name in which:
        found = shutil.which(name)
        if found:
            return found
    for hint in hints:
        if "*" in hint:
            for hit in _glob_paths(os.path.expandvars(hint)):
                if os.path.isfile(hit):
                    return hit
            continue
        candidate = Path(os.path.expandvars(hint))
        if candidate.is_file():
            return str(candidate)
    return ""


@lru_cache(maxsize=1)
def find_tesseract(explicit: str = "") -> ToolInfo:
    """Locate Tesseract and its ``tessdata`` folder."""
    path = _find(explicit, TESSERACT_HINTS, ("tesseract",))
    if not path:
        return ToolInfo(name="tesseract")
    tessdata = Path(path).parent / "tessdata"
    return ToolInfo(
        name="tesseract",
        path=path,
        version=_run_version([path, "--version"]),
        extra_dir=str(tessdata) if tessdata.is_dir() else "",
    )


@lru_cache(maxsize=1)
def find_ghostscript(explicit: str = "") -> ToolInfo:
    """Locate a Ghostscript CLI (must be a ``c`` console build on Windows)."""
    path = _find(explicit, GHOSTSCRIPT_HINTS, ("gswin64c", "gswin32c", "gs"))
    if not path:
        return ToolInfo(name="ghostscript")
    return ToolInfo(name="ghostscript", path=path, version=_run_version([path, "--version"]))


def languages(tess_info: ToolInfo) -> list[str]:
    """List the Tesseract language packs that are installed."""
    if not tess_info.available:
        return []
    proc = subprocess.run(
        [tess_info.path, "--list-langs"],
        capture_output=True,
        text=True,
        timeout=25,
        creationflags=CREATE_NO_WINDOW,
    )
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return [ln for ln in lines if " " not in ln and len(ln) <= 8][:60]


def prepare_env(tess: ToolInfo) -> dict[str, str]:
    """Put Tesseract on PATH and point TESSDATA_PREFIX at its language data.

    PyMuPDF's OCR needs both; doing this in-process avoids requiring a reboot
    or a system-wide PATH change after installing Tesseract.
    """
    env = dict(os.environ)
    if tess.available:
        exe_dir = str(Path(tess.path).parent)
        env["PATH"] = exe_dir + os.pathsep + env.get("PATH", "")
        os.environ["PATH"] = env["PATH"]
        if tess.extra_dir:
            env["TESSDATA_PREFIX"] = tess.extra_dir
            os.environ["TESSDATA_PREFIX"] = tess.extra_dir
    return env


def diagnose(cfg=None) -> dict[str, ToolInfo]:
    """Probe every optional tool, honouring explicit paths from the config."""
    tesseract = find_tesseract(str(cfg.get("paths.tesseract_exe", "") if cfg else ""))
    ghostscript = find_ghostscript(str(cfg.get("paths.ghostscript_exe", "") if cfg else ""))
    return {"tesseract": tesseract, "ghostscript": ghostscript}
