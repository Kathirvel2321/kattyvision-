"""KattyVision - the desktop app.

Two ways to work:

  AUTOMATION tab   the full case pipeline in one click (folder in, files out)
  PDF TOOLS tab    one small page per task - merge, OCR, compress, page
                   numbers, seal, split - pick the files, run, done
  SETTINGS tab     every technical option, remembered between runs

Run it with:   python -m court_ocr.app
"""

from __future__ import annotations

import logging
import queue
import re
import sys
import threading
import time
import tkinter as tk
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Sequence

try:
    import ttkbootstrap as ttkb
except ImportError:                      # graceful fall-back to stock ttk
    ttkb = None

from .config import PROJECT_ROOT, Config
from .logging_setup import get_logger, reset_logger
from .pipeline import Pipeline
from .splash import make_splash
from .toolcheck import diagnose
from .ui_kit import (ACCENT, ACCENT_SOFT, BG, BORDER, BORDER_STRONG, BODY, CAPTION,
                     CARD, Chip, ChipRow, ChoiceCard, ChoiceGroup, Disclosure,
                     DISPLAY, ERR, ERR_SOFT, Field, FONT, FOCUS, HEADING, Icon,
                     IconButton, MUTED, OK, OK_SOFT, PageMap, SealPreview,
                     PageNumberPreview,
                     Segmented, Stepper, SURFACE, Switch, TEXT, TITLE, WARN,
                     WARN_SOFT, WARN_TEXT, contrast_foreground, px, set_scale)
from .util import MB, open_in_explorer

APP_NAME = "KattyVision"
APP_TAGLINE = "Less paperwork. More peace of mind."
#: Taskbar grouping identity.  It must not contain the old working names
#: ("CourtFiling" / "PDFHelper"): Windows shows this string in the taskbar and
#: in the jump list, so a stale name here is what kept the app looking like
#: something other than KattyVision.
APP_ID = "KattyVision.Desktop.1"
APP_SIGNATURE = "Kathirvel"
ASSETS = PROJECT_ROOT / "assets"

# ------------------------------------------------------------------ palette
#  The colours and the type scale now live in :mod:`court_ocr.ui_kit` and are
#  imported above, so the window, the tool pages and the settings dialog all
#  share one source of truth.  A few names are kept here because the rest of the
#  app already reads them from this module.
ACCENT_HOVER = "#064990"

# Display scale factor (1.0 = 100%, 1.5 = 150%).  Set once by
# enable_dpi_awareness() before the window is created: without DPI awareness
# Windows bitmap-stretches the whole window on a scaled laptop display, which
# both blurs the text and makes a "780 pixel" window 1170 physical pixels tall
# - taller than a 1080p screen, which is how the RUN button ended up below the
# bottom of the screen.
UI_SCALE = 1.0


#: Tool pages that get a visual page map.  Splitting is the case where the user
#: genuinely has to decide *which* pages go together, so the map is the point;
#: for merge/OCR/compress the file list is all that matters.
PAGE_MAP_TOOLS = frozenset({"split"})

#: Tool pages that get the live seal workbench (PDF + seal + picture).
SEAL_PREVIEW_TOOLS = frozenset({"stamp"})

# ------------------------------------------------------------------ presets
@dataclass
class Preset:
    """A friendly bundle of settings, described by the result it produces.

    ``plan`` is the plain-English sentence shown under the choice cards, so the
    user can read exactly what will happen *before* pressing RUN.  It is written
    in outcome language ("make the scans searchable") rather than engine terms.
    """

    key: str
    title: str
    hint: str
    settings: dict = field(default_factory=dict)
    icon: str = "launch"
    badge: str = ""
    plan: str = ""


PRESETS: tuple[Preset, ...] = (
    Preset(
        "standard",
        "Ready to file",
        "Search, compress, number and seal. All in one go.",
        {
            "ocr.enabled": True, "ocr.mode": "skip_text",
            "compress.enabled": True, "compress.level": 2,
            "pagination.enabled": True, "stamp.enabled": True,
            "split.enabled": True, "cover_page.enabled": False,
        },
        icon="launch", badge="MOST USED",
        plan="One PDF with real, selectable text, made small enough to upload, "
             "numbered 'Page 1 of N' on every page, with the bank seal.",
    ),
    Preset(
        "indexed",
        "Add a contents page",
        "A complete filing with a tidy document index.",
        {
            "ocr.enabled": True, "ocr.mode": "skip_text",
            "compress.enabled": True, "compress.level": 2,
            "pagination.enabled": True, "stamp.enabled": True,
            "split.enabled": True, "cover_page.enabled": True,
        },
        icon="book", badge="",
        plan="Everything in 'Ready to file', plus a contents page at the front "
             "listing each document and where it starts.",
    ),
    Preset(
        "quick",
        "Just join the files",
        "Merge, number and compress. Scans stay as images.",
        {
            "ocr.enabled": False, "compress.enabled": True, "compress.level": 2,
            "pagination.enabled": True, "stamp.enabled": False,
            "split.enabled": True, "cover_page.enabled": False,
        },
        icon="bolt", badge="FASTEST",
        plan="The documents joined into one PDF, numbered and compressed. "
             "Scanned pages stay pictures, so the text is NOT searchable.",
    ),
    Preset(
        "custom",
        "My own workflow",
        "Choose the steps that suit this case.",
        {},
        icon="sliders", badge="",
        plan="Use the switches below to choose exactly which steps run.",
    ),
)


@dataclass
class Portal:
    """A court portal's upload limits, chosen with one click.

    Removes the old "type 50 into a box and 4.5 into another" step: the user
    picks their court from a list, and the app remembers the numbers for them.
    """

    key: str
    label: str
    max_pages: int
    max_mb: float
    hint: str


PORTALS: tuple[Portal, ...] = (
    Portal("raigad", "Raigad e-filing", 42, 4.5,
           "District court filing portal in Maharashtra"),
    Portal("standard", "Standard court portal", 30, 2.0,
           "The usual limit for an e-filing website"),
    Portal("large", "Large upload allowed", 100, 10.0,
           "For portals that accept bigger files"),
    Portal("custom", "My portal's own limit", 50, 4.5,
           "Set the numbers yourself"),
)


#: Which quality preset the adaptive optimizer should use for each bundle.
PRESET_QUALITY = {"standard": "balanced", "indexed": "balanced",
                  "quick": "small", "custom": "balanced"}

# steps shown in the live status list (config key -> plain English label)
STEP_ROWS: tuple[tuple[str, str], ...] = (
    ("merge", "Join all documents into one PDF"),
    ("cover", "Add the contents page"),
    ("ocr", "Make the scans searchable"),
    ("compress", "Shrink the file to fit the portal"),
    ("paginate", "Add page numbers"),
    ("stamp", "Add the bank seal"),
    ("split", "Split into upload-sized files"),
)

STEP_ALIASES = {
    "merge": "merge", "cover": "cover", "ocr": "ocr", "compress": "compress",
    "paginate": "paginate", "pagination": "paginate", "stamp": "stamp", "split": "split",
}


# ------------------------------------------------------------------ helpers
def _join(words: Sequence[str]) -> str:
    """Join a list into readable English: "a, b and c"."""
    items = [str(word) for word in words if str(word).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _count_pages(outputs: Sequence[Path]) -> int:
    """Total page count across the finished files (0 if it cannot be read)."""
    total = 0
    for path in outputs:
        if path.suffix.lower() != ".pdf":
            continue
        try:
            import pymupdf

            with pymupdf.open(path) as document:
                total += document.page_count
        except Exception:                       # a chip must never break a run
            continue
    return total


class GuiLogHandler(logging.Handler):
    """Forward log records into the GUI queue (thread safe)."""

    def __init__(self, sink) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink(record.levelname, record.getMessage())
        except Exception:                              # never break logging
            pass


def apply_theme(root: tk.Misc) -> ttk.Style:
    """A clean, flat, easy to read theme."""
    def commit_number(event):
        focused = root.focus_get()
        if focused is not None and focused is not event.widget and isinstance(focused.master, Stepper):
            focused.master._commit()

    root.bind("<Button-1>", commit_number, add="+")
    style = ttk.Style(root)
    if ttkb is None:                         # only when ttkbootstrap is absent
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

    style.configure(".", font=(FONT, BODY), background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=(FONT, CAPTION))
    style.configure("CardHead.TLabel", background=CARD, foreground=ACCENT,
                    font=(FONT, BODY, "bold"))
    style.configure("Title.TLabel", background=BG, foreground=ACCENT,
                    font=(FONT, TITLE, "bold"))
    style.configure("Tagline.TLabel", background=BG, foreground=MUTED, font=(FONT, CAPTION))
    style.configure("Status.TLabel", background=BG, foreground=TEXT, font=(FONT, CAPTION))
    style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT)
    style.configure("Card.TRadiobutton", background=CARD, foreground=TEXT)
    style.configure("TLabelframe", background=CARD, bordercolor=BORDER,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=CARD, foreground=ACCENT,
                    font=(FONT, BODY, "bold"))
    style.configure("Run.TButton", font=(FONT, HEADING, "bold"), padding=(26, 11),
                    background=ACCENT, foreground="#ffffff", borderwidth=0)
    style.map("Run.TButton",
              background=[("active", ACCENT_HOVER), ("disabled", "#9aa7bd")])
    style.configure("TButton", padding=(14, 8), background=SURFACE,
                    foreground=TEXT, bordercolor=BORDER, borderwidth=1)
    style.map("TButton", background=[("active", ACCENT_SOFT)],
              bordercolor=[("focus", FOCUS)])
    for field_style in ("TEntry", "TCombobox", "Number.TSpinbox"):
        style.configure(field_style, padding=(10, 8), fieldbackground=CARD,
                        foreground=TEXT, bordercolor=BORDER_STRONG,
                        lightcolor=CARD, darkcolor=CARD, arrowsize=14)
        style.map(field_style, bordercolor=[("focus", FOCUS)])
    style.configure("Link.TButton", padding=(3, 1), borderwidth=0,
                    background=CARD, foreground=ACCENT_HOVER)
    style.map("Link.TButton", background=[("active", CARD)])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(20, 10), font=(FONT, BODY, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", ACCENT_SOFT), ("active", SURFACE)],
              foreground=[("selected", ACCENT), ("active", TEXT)])
    # A progress bar that reads as one solid ribbon, not a blocky 90s strip.
    style.configure("Horizontal.TProgressbar", thickness=px(9),
                    troughcolor=SURFACE, bordercolor=SURFACE,
                    background=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("TCombobox", padding=(8, 4))

    for name, colour in (("Wait", MUTED), ("Work", WARN_TEXT), ("Done", OK),
                         ("Fail", ERR)):
        style.configure(f"Step{name}.TLabel", background=CARD, foreground=colour,
                        font=(FONT, CAPTION, "bold"))
    style.configure("StepName.TLabel", background=CARD, foreground=TEXT)
    style.configure("StepNote.TLabel", background=CARD, foreground=MUTED, font=(FONT, CAPTION))

    # ---- modern hero band across the top of the window
    style.configure("Hero.TFrame", background=CARD)
    style.configure("HeroTitle.TLabel", background=CARD, foreground=TEXT,
                    font=(FONT, DISPLAY, "bold"))
    style.configure("HeroSub.TLabel", background=CARD, foreground=MUTED,
                    font=(FONT, BODY))
    style.configure("HeroPill.TLabel", background=OK, foreground="#ffffff",
                    font=(FONT, CAPTION, "bold"), padding=(12, 4))
    style.configure("Signature.TLabel", background=BG, foreground="#1f3f68",
                    font=(FONT, CAPTION, "bold"))
    style.configure("SideButton.TButton", padding=(12, 8), font=(FONT, BODY))
    style.map("SideButton.TButton", background=[("active", ACCENT_HOVER)])
    style.configure("Soft.TButton", padding=(10, 6))
    return style


def load_icon(window: tk.Misc, filename: str = "app.ico") -> None:
    """Give the window - and therefore the taskbar button - the brand icon."""
    candidate_paths = [ASSETS / filename, PROJECT_ROOT / "brand" / "kattyvision.png",
                       PROJECT_ROOT / "brand" / "kattyvissionfull.png",
                       ASSETS / "logo_64.png", ASSETS / "logo.png"]
    for path in candidate_paths:
        if not path.is_file():
            continue
        try:
            window.iconbitmap(default=str(path))
            return
        except Exception:
            pass
        try:
            window.iconbitmap(str(path))
            return
        except Exception:
            pass
        try:
            photo = tk.PhotoImage(file=str(path))
            window.iconphoto(True, photo)
            window._brand_icon_photo = photo
            return
        except Exception:
            continue


def enable_dpi_awareness() -> float:
    """Tell Windows we draw our own pixels; return the display scale.

    1.0 = 100%, 1.5 = 150% scaling.  Must run *before* the Tk window exists -
    afterwards Windows has already decided to stretch everything.
    """
    try:
        import ctypes

        try:                                  # per-monitor aware (Windows 8.1+)
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)       # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return max(1.0, min(dpi / 96.0, 3.0))
    except Exception:
        pass
    return 1.0


def set_taskbar_identity() -> None:
    """Stop Windows from grouping us under the generic Python icon."""
    if not hasattr(tk, "Tk"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


class JobCancelled(Exception):
    """Raised inside a job thread when the user presses Cancel."""


# ------------------------------------------------------------------ widgets
class Tooltip:
    """Small dark tooltip after a short hover - plain language only."""

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        self._id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._id = self.widget.after(500, self._show)

    def _show(self) -> None:
        if self._tip:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=self.text, background="#2b3547", foreground="#ffffff",
                 font=(FONT, CAPTION), justify="left", padx=9, pady=6,
                 wraplength=340).pack()

    def _hide(self, _event=None) -> None:
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except Exception:
                pass
            self._id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# plain-language tooltips for every option key used by the tool pages
TIP_TEXT: dict[str, str] = {
    "bookmarks": "Keeps one clickable bookmark per source file, so the big PDF stays easy to navigate.",
    "mode": "'Only pages without text' is the smart default: pages that already have real text are not touched, so it is faster and cannot make quality worse.",
    "dpi": "Sharpness of the OCR scan. 300 suits court documents; 400 if text is small or faint (slower).",
    "language": "Language of the text. 'eng' = English. Use 'eng+mar' for English + Marathi documents.",
    "jobs": "How many copies of the OCR engine run at once. 0 = automatic (uses the PC fully).",
    "level": "The old global strength setting. Only used by the 'older' shrinking method in Settings.",
    "preset": "How the pages are re-encoded. Scanned pages become 1-bit black and white (small and sharp); pages that already have real text are never touched. 'Balanced' is right for almost every filing.",
    "target_mb": "The app keeps trying until every file is under this size. 0 = just make it as small as reasonably possible.",
    "format": "What is printed on every page. {page} becomes the page number, {total} the total.",
    "position": "Where the numbers/seal are printed on the page.",
    "font_size": "Size of the printed numbers.",
    "skip_first": "Useful when page 1 is a title or index page that should stay clean.",
    "style": "1, 2, 3 ... or Roman (I, II, III) or letters (A, B, C).",
    "seal_image": "Your bank's round seal as an image. A transparent PNG looks cleanest.",
    "pages": "Which pages receive the seal. 'Every page' is the usual choice.",
    "width_percent": "Seal size as a share of the page width. Around 20 works for A4.",
    "opacity": "How visible the seal is. 0.9 = clearly visible, 0.4 = faint watermark.",
    "max_pages": "The court portal refuses files with more pages than this.",
    "max_mb": "The court portal refuses files bigger than this (MB).",
    "keep_bookmarks": "When cutting the PDF into parts, it prefers to cut between documents rather than through one.",
}


def flash_window(window: tk.Misc) -> None:
    """Flash the taskbar button so a finished long job gets noticed."""
    try:
        import ctypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint)]

        hwnd = window.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3, 4, 0)   # 3 = FLASHW_ALL
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


_MUTEX_HANDLE = None


def enforce_single_instance() -> bool:
    """True if this is the first copy; otherwise focus the running one and False."""
    global _MUTEX_HANDLE
    try:
        import ctypes

        _MUTEX_HANDLE = ctypes.windll.kernel32.CreateMutexW(
            None, False, "KattyVision.SingleInstance.Mutex")
        if ctypes.windll.kernel32.GetLastError() == 183:          # already exists
            hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            return False
    except Exception:
        pass
    return True


# ------------------------------------------------------------------ drag & drop
DND_OK = False


def try_enable_dnd(root: tk.Misc) -> bool:
    """Load the tiny tkdnd engine into the running Tk interpreter, if installed.

    The app stays fully usable without it (buttons keep working); with it the
    user can drag files and whole folders straight from the Desktop/Explorer.
    """
    global DND_OK
    try:
        from tkinterdnd2 import TkinterDnD

        TkinterDnD._require(root)
        DND_OK = True
    except Exception:
        DND_OK = False
    return DND_OK


def register_drop(widget: tk.Misc, callback) -> bool:
    """Attach <<Drop>> to a widget through the tkdnd package directly.

    Using ``tk.call('tkdnd::drop_target', ...)`` works on ANY root window
    (plain tk.Tk and ttkbootstrap alike), unlike the mixin methods that only
    exist on tkinterdnd2's own Tk subclass.
    """
    if not DND_OK:
        return False
    try:
        widget.tk.call("tkdnd::drop_target", "register", widget, "DND_Files")
        widget.bind("<<Drop>>", callback, add="+")
        return True
    except Exception:
        return False


def parse_dropped(data: str) -> list[Path]:
    """tkdnd returns '{C:/dir with spaces/a.pdf} C:/b.pdf' - split it safely."""
    items: list[str] = []
    token = ""
    in_brace = False
    for char in str(data):
        if char == "{":
            in_brace = True
            continue
        if char == "}":
            in_brace = False
            items.append(token)
            token = ""
            continue
        if char == " " and not in_brace:
            if token:
                items.append(token)
            token = ""
            continue
        token += char
    if token:
        items.append(token)
    return [Path(item) for item in items if item]


# ==========================================================================
#  Scrollable container - every tab can be taller than the window
# ==========================================================================
class ScrollFrame(ttk.Frame):
    """A frame with a vertical scrollbar and mouse-wheel support."""

    def __init__(self, master: tk.Misc, **kw) -> None:
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=BG)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(
                            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        # Mouse wheel.  Windows sends <MouseWheel> to the widget under the
        # pointer, so a wheel over a Label/Entry/Button never reached this
        # canvas and the page looked like it could not scroll at all.  Listen
        # globally and scroll only when the pointer is really inside this page.
        self.canvas.configure(yscrollincrement=max(14, int(22 * UI_SCALE)))
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, event) -> None:
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except tk.TclError:
            return
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self:
            if widget is not self.canvas and isinstance(widget, (tk.Text, tk.Listbox)):
                return                      # the log box / file list scrolls itself
            widget = getattr(widget, "master", None)
        if widget is None:                  # pointer is on a different tab
            return
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:    # nothing to scroll
            return
        steps = -1 * (event.delta // 120)
        self.canvas.yview_scroll(
            steps if steps else (-1 if event.delta > 0 else 1), "units")

    def scroll_to(self, fraction: float) -> None:
        """Scroll to a 0.0-1.0 position (0 = top, 1 = bottom)."""
        self.canvas.yview_moveto(max(0.0, min(1.0, float(fraction))))


# ==========================================================================
#  Single-task tools - each runs on its own page, results saved right there
# ==========================================================================
def _stamp_text_cfg():
    return None


def _out_name(path: Path, suffix: str) -> Path:
    """'<name>_OCR.pdf' next to the original file (that is where people look)."""
    return path.with_name(f"{path.stem}{suffix}.pdf")


def _tool_merge(paths, out_dir, opts, ctx) -> tuple[list, list]:
    from . import pdf_ops

    target = out_dir / ("Merged_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".pdf")
    merged = pdf_ops.merge_files(paths, target,
                                 create_bookmarks=bool(opts.get("bookmarks", True)),
                                 progress=ctx["progress"])
    warnings = [f"skipped {name}: {why}" for name, why in merged.skipped]
    return [merged.path], warnings


def _tool_ocr(paths, out_dir, opts, ctx) -> tuple[list, list]:
    from . import ocr_engine

    outputs: list[Path] = []
    warnings: list[str] = []
    for index, path in enumerate(paths, 1):
        ctx["progress"](f"file {index} of {len(paths)}: {path.name}")
        result = ocr_engine.run_ocr(
            path, out_dir / _out_name(path, "_OCR"),
            language=str(opts.get("language", "eng") or "eng"),
            dpi=int(opts.get("dpi", 300) or 300),
            jobs=int(opts.get("jobs", 0) or 0),
            mode=str(opts.get("mode", "skip_text") or "skip_text"),
        )
        outputs.append(result.path)
        if not result.ok:
            warnings.append(f"{path.name}: OCR had problems - check the text")
    return outputs, warnings


def _tool_compress(paths, out_dir, opts, ctx) -> tuple[list, list]:
    """Compress with the same smart engine the whole-case flow uses.

    This used to call ``pdf_ops.compress_pdf`` (the old one-size-fits-all levels
    engine).  Measured on a real scanned filing that saved about **1 %**, while
    the adaptive optimizer the Automation tab uses saved **60-95 %** on the very
    same file - so the tool looked broken.  It now goes through
    ``optimize.optimize_pdf``: scanned pages become crisp 1-bit at 300 dpi and
    born-digital pages are never touched.
    """
    from . import optimize

    outputs: list[Path] = []
    warnings: list[str] = []
    preset = str(opts.get("preset", "balanced") or "balanced")
    target_mb = float(opts.get("target_mb", 0) or 0)
    for index, path in enumerate(paths, 1):
        ctx["progress"](f"file {index} of {len(paths)}: {path.name}")
        comp = optimize.optimize_pdf(
            path, out_dir / _out_name(path, "_compressed"),
            preset=preset,
            target_mb=target_mb,
            progress=ctx["progress"],
        )
        outputs.append(comp.path)
        if not comp.ok:
            # ok=False only happens when a target was asked for and could not be
            # met - the file is still written and perfectly usable, so say that
            # plainly instead of reporting a failure.
            warnings.append(f"{path.name}: {comp.detail}")
    return outputs, warnings


def _tool_paginate(paths, out_dir, opts, ctx) -> tuple[list, list]:
    from . import pdf_ops

    outputs: list[Path] = []
    warnings: list[str] = []
    for index, path in enumerate(paths, 1):
        ctx["progress"](f"file {index} of {len(paths)}: {path.name}")
        pag = pdf_ops.add_page_numbers(
            path, out_dir / _out_name(path, "_numbered"),
            fmt=str(opts.get("format", "Page {page} of {total}")),
            position=str(opts.get("position", "bottom-right")),
            font_size=float(opts.get("font_size", 9) or 9),
            font=str(opts.get("font", "helv") or "helv"),
            start_number=int(opts.get("start_number", 1) or 1),
            skip_first=int(opts.get("skip_first", 0) or 0),
            style=str(opts.get("style", "arabic") or "arabic"),
        )
        outputs.append(pag.path)
    return outputs, warnings


def _tool_stamp(paths, out_dir, opts, ctx) -> tuple[list, list]:
    from . import stamp as stamp_mod

    outputs: list[Path] = []
    warnings: list[str] = []
    seal = str(opts.get("seal_image", "") or "")
    if not seal or not Path(seal).is_file():
        raise ValueError("Pick a seal image first (the preview panel has the "
                         "button for it).")
    # Which pages get the seal.  The page chooser on the tool page passes the
    # same "1,3,5-9" style list the stamper already understands, so the map and
    # the run cannot disagree about which pages were chosen.
    pages = str(opts.get("pages", "all") or "all")
    for index, path in enumerate(paths, 1):
        ctx["progress"](f"file {index} of {len(paths)}: {path.name}")
        stamped = stamp_mod.apply_stamp(
            path, out_dir / _out_name(path, "_sealed"),
            image_path=Path(seal),
            pages=pages,
            position=str(opts.get("position", "bottom-right")),
            width_percent=float(opts.get("width_percent", 20) or 20),
            opacity=float(opts.get("opacity", 0.9) or 0.9),
            nudge_x_mm=float(opts.get("nudge_x", 0) or 0),
            nudge_y_mm=float(opts.get("nudge_y", 0) or 0),
        )
        outputs.append(stamped.path)
        if not stamped.ok:
            warnings.append(f"{path.name}: {stamped.detail}")
    return outputs, warnings


def _oversize_warnings(files, opts) -> list[str]:
    """Say plainly when a part breaks a limit the user set.

    Chosen page cuts are always honoured exactly - "5 pages, then 8, then the
    rest" has to be possible - but that means a cut can quietly produce a part
    the portal would reject.  The user set a limit for a reason, so it is
    worth saying which parts missed it rather than leaving them to find out at
    the portal.
    """
    import pymupdf

    from . import pdf_ops

    max_pages = int(opts.get("max_pages", 0) or 0)
    max_mb = float(opts.get("max_mb", 0) or 0)
    if not max_pages and not max_mb:
        return []
    over_pages: list[str] = []
    over_size: list[str] = []
    for path in files:
        try:
            with pymupdf.open(path) as doc:
                pages = doc.page_count
        except Exception:
            continue
        if max_pages and pages > max_pages:
            over_pages.append(f"{path.name} ({pages} pages)")
        if max_mb and path.stat().st_size / pdf_ops.MB > max_mb:
            over_size.append(f"{path.name} ({path.stat().st_size / pdf_ops.MB:.1f} MB)")
    out: list[str] = []
    if over_pages:
        out.append(f"{len(over_pages)} part(s) are over the {max_pages}-page limit: "
                   + ", ".join(over_pages[:4])
                   + ("..." if len(over_pages) > 4 else "")
                   + " - press 'Split evenly' on the page map to redraw the cuts")
    if over_size:
        out.append(f"{len(over_size)} part(s) are over the {max_mb:g} MB limit: "
                   + ", ".join(over_size[:4])
                   + ("..." if len(over_size) > 4 else "")
                   + " - compress the file first, or set a smaller page cut")
    return out


def _tool_split(paths, out_dir, opts, ctx) -> tuple[list, list]:
    """Split, honouring the page cuts the user drew on the page map."""
    from . import pdf_ops

    outputs: list[Path] = []
    warnings: list[str] = []
    sources = opts.get("sources") or []
    cuts = opts.get("cuts") or []

    # The page map gives exact page numbers, so it wins over the plain limits.
    if sources and cuts:
        files = pdf_ops.split_pdf_at_cuts(
            [(str(p), int(n)) for p, n in sources], [tuple(c) for c in cuts],
            out_dir,
            keep_bookmark_boundaries=bool(opts.get("keep_bookmarks", True)),
            progress=ctx["progress"])
        if files:
            warnings.extend(_oversize_warnings(files, opts))
            return files, warnings
        warnings.append("the chosen page cuts produced nothing - "
                        "splitting by page limit instead")

    for index, path in enumerate(paths, 1):
        ctx["progress"](f"file {index} of {len(paths)}: {path.name}")
        parts = pdf_ops.split_pdf(
            path, out_dir,
            mode=str(opts.get("mode", "pages") or "pages"),
            max_pages=int(opts.get("max_pages", 50) or 50),
            max_size_mb=float(opts.get("max_mb", 0) or 0),
            keep_bookmark_boundaries=bool(opts.get("keep_bookmarks", True)),
            name_template="{case}_Part{index:02d}.pdf",
            case=path.stem,
        )
        outputs.extend(parts.files)
        if not parts.files:
            warnings.append(f"{path.name}: nothing was split out of it")
    return outputs, warnings


PDF_TYPES = [("PDF files", "*.pdf"), ("Image files", "*.png *.jpg *.jpeg *.tif *.tiff"),
             ("All files", "*.*")]


@dataclass
class ToolSpec:
    """Definition of one single-task page."""

    key: str
    title: str
    about: str
    options: tuple                 # (kind, label, opts_key, default, extra)
    runner: object                 # (paths, out_dir, opts, ctx) -> (outputs, warnings)
    pick_files: bool = True        # False = pick a single folder instead


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "merge", "Merge",
        "Join several PDFs / images into one PDF. The order below is the page order "
        "(add them in order, or fix it by removing and re-adding).",
        (("bool", "Keep a bookmark for each file", "bookmarks", True, None),),
        _tool_merge,
    ),
    ToolSpec(
        "ocr", "OCR (make text searchable)",
        "Turns scanned pages into real, selectable text. Pages that already have "
        "text are left untouched.",
        (
            ("choice", "Which pages", "mode", "skip_text",
             (("skip_text", "Only pages without text (recommended)"),
              ("force", "Every page (thorough, slower)"))),
            ("int", "Scan resolution (DPI, higher = more accurate)", "dpi", 300, (150, 400)),
            ("text", "Language (eng, or eng+mar)", "language", "eng", None),
            ("int", "Parallel workers (0 = automatic)", "jobs", 0, (0, 64)),
        ),
        _tool_ocr,
    ),
    ToolSpec(
        "compress", "Compress",
        "Makes files much smaller page by page: scanned pages become crisp "
        "1-bit at 300 dpi, and pages that already have real text are left "
        "untouched. Give a target size to chase, or 0 for best effort.",
        (
            ("choice", "Quality", "preset", "balanced",
             (("archival", "Archival - best quality (400 dpi text)"),
              ("high", "High - best everyday (400 dpi text)"),
              ("balanced", "Balanced (recommended)"),
              ("small", "Small - weakest portal limit"))),
            ("float", "Target size in MB (0 = best effort)", "target_mb", 0, None),
        ),
        _tool_compress,
    ),
    ToolSpec(
        "paginate", "Page numbers",
        "Prints 'Page X of Y' on every page.",
        (
            ("text", "Format ({page} and {total})", "format",
             "Page {page} of {total}", None),
            ("choice", "Position", "position", "bottom-right",
             (("bottom-right", "Bottom right"), ("bottom-center", "Bottom centre"),
              ("bottom-left", "Bottom left"), ("top-right", "Top right"),
              ("top-center", "Top centre"), ("top-left", "Top left"))),
            ("int", "Font size", "font_size", 9, (6, 24)),
            ("choice", "Font style", "font", "helv",
             (("helv", "Helvetica"), ("hebo", "Helvetica Bold"),
              ("tiro", "Times Roman"), ("tibo", "Times Bold"),
              ("cour", "Courier"))),
            ("int", "Start numbering from", "start_number", 1, (1, 1000)),
            ("int", "Leave the first N pages unnumbered", "skip_first", 0, (0, 20)),
            ("choice", "Number style", "style", "arabic",
             (("arabic", "1, 2, 3"), ("roman", "I, II, III"), ("alpha", "A, B, C"))),
        ),
        _tool_paginate,
    ),
    ToolSpec(
        "stamp", "Bank seal",
        "Puts the seal image on the chosen pages of each PDF.",
        (
            ("file", "Seal image (transparent PNG works best)", "seal_image", "", None),
            ("choice", "Which pages", "pages", "all",
             (("all", "Every page"), ("first", "First page only"),
              ("last", "Last page only"), ("1,3,5-9", "Custom, e.g. 1,3,5-9"))),
            ("choice", "Position", "position", "bottom-right",
             (("bottom-right", "Bottom right"), ("bottom-left", "Bottom left"),
              ("bottom-center", "Bottom centre"), ("top-right", "Top right"),
              ("top-left", "Top left"), ("center", "Centre"))),
            ("int", "Seal width (% of page width)", "width_percent", 20, (5, 60)),
            ("float", "Opacity (0.3 faint - 1.0 solid)", "opacity", 0.9, None),
        ),
        _tool_stamp,
    ),
    ToolSpec(
        "split", "Split into several files",
        "Add the files, scroll through the pages below, then click the page "
        "where each new file should start. Parts can be any size - 5 pages, "
        "then 8, then the rest.",
        (
            ("choice", "If you click nothing", "mode", "pages",
             (("pages", "Only by page count (recommended)"),
              ("both", "Also by file size (needs a MB limit below)"),
              ("size", "Only by file size"))),
            ("int", "Most pages in one file", "max_pages", 50, (1, 5000)),
            ("float", "Largest file size in MB (0 = ignore file size)", "max_mb", 0, None),
            ("bool", "Never cut a document in half", "keep_bookmarks", True, None),
        ),
        _tool_split,
    ),
)


# Vector glyph per tool tab, drawn by :class:`ui_kit.Icon` on a 24x24 grid.
# These replaced emoji, which rendered at a different size on every machine,
# broke DPI scaling and fought the flat palette.
TOOL_ICON = {"merge": "book", "ocr": "search", "compress": "package",
             "paginate": "hash", "stamp": "tag", "split": "cut"}


#: Which single-task pages logically FOLLOW each tool.  After a run the result
#: card offers exactly these as "use this result in...", so the user hands the
#: finished file to the next step with one click instead of hunting for it in
#: Explorer and re-adding it by hand.
TOOL_HANDOFF: dict[str, tuple[str, ...]] = {
    "merge": ("ocr", "compress", "paginate", "stamp", "split"),
    "ocr": ("compress", "paginate", "stamp", "split"),
    "compress": ("paginate", "stamp", "split"),
    "paginate": ("stamp", "split"),
    "stamp": ("split",),
    "split": (),
}


class ToolPage:
    """One self-contained page: pick files, tweak options, run, open results."""

    def __init__(self, app: "App", spec: ToolSpec) -> None:
        self.app = app
        self.spec = spec
        self.option_vars: dict[str, tk.Variable] = {}
        self.outputs: list[Path] = []
        self.sources: list[tuple[str, int]] = []
        self.page_map: "PageMap | None" = None
        self.seal_preview: "SealPreview | None" = None
        self.seal_pages = None
        self.nudge_x = 0.0
        self.nudge_y = 0.0
        self._build()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        wrap = ScrollFrame(self.app.tool_book)
        self.app.tool_book.add(wrap, text=f"  {self.spec.title}  ")
        # Keep a handle on the scroller: it is what decides how much vertical
        # room the page map gets, and the screenshot tool scrolls it.
        self.scroller = wrap
        inner = wrap.inner
        inner.columnconfigure(0, weight=1)
        page = ttk.Frame(inner, padding=12)
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)

        # ---- a titled header, so the user always knows which job this is
        head = tk.Frame(page, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", pady=(0, px(8)))
        Icon(head, TOOL_ICON.get(self.spec.key, "document"), 20, ACCENT,
             background=CARD).pack(side="left", padx=(0, px(10)))
        ttk.Label(head, text=self.spec.title, style="CardHead.TLabel",
                  font=(FONT, HEADING, "bold")).pack(side="left")
        ttk.Label(head, text=self.spec.about, style="CardMuted.TLabel",
                  wraplength=px(620), justify="left").pack(side="left",
                                                            padx=(px(10), 0))
        self.header = head

        # -------- files
        files = ttk.LabelFrame(page, text="  Files  ", padding=10)
        files.grid(row=1, column=0, sticky="ew")
        files.columnconfigure(0, weight=1)
        self.file_list = tk.Listbox(files, height=4, font=(FONT, CAPTION),
                                    background="#f7f9fc", relief="flat")
        self.file_list.grid(row=0, column=0, sticky="ew")
        self.paths: list[Path] = []
        self.empty_hint = ttk.Label(
            files, style="CardMuted.TLabel",
            text="No files yet — use 'Add PDFs / images...' or 'Add ALL from a folder...'.")
        self.empty_hint.grid(row=0, column=0, sticky="ew", pady=(4, 4), padx=6)
        self.file_list.grid_remove()              # empty until files are added
        bar = ttk.Frame(files, style="Card.TFrame")
        bar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(bar, text="Add PDFs / images...",
                   command=self._add_files).pack(side="left")
        ttk.Button(bar, text="Add ALL from a folder...",
                   command=self._add_folder).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Remove selected", command=self._remove_selected).pack(
            side="left", padx=(8, 0))
        ttk.Button(bar, text="Clear", command=self._clear_files).pack(
            side="left", padx=(8, 0))
        # Order matters when merging and when splitting a case, so give an
        # explicit way to fix it instead of "remove and re-add in the right order".
        ttk.Button(bar, text="↑ Move up", width=10,
                   command=lambda: self._move_selected(-1)).pack(
            side="left", padx=(8, 0))
        ttk.Button(bar, text="↓ Move down", width=11,
                   command=lambda: self._move_selected(1)).pack(
            side="left", padx=(4, 0))
        self._enable_drop(files)          # drag files/folders straight in

        # -------- save location
        save = ttk.LabelFrame(page, text="  Save results in  ", padding=10)
        save.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        save.columnconfigure(0, weight=1)
        self.var_out = tk.StringVar(value="")
        ttk.Entry(save, textvariable=self.var_out).grid(row=0, column=0, sticky="ew")
        ttk.Button(save, text="Browse...", command=self._pick_out).grid(
            row=0, column=1, padx=(8, 0))
        ttk.Label(save, text="left empty = same folder as the first file",
                  style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        # -------- the seal workbench: the PDF on the left, the seal image and
        #         the live picture on the right.  Putting the picture next to
        #         the size/strength/position controls is the whole point - the
        #         user arranges it and watches it change.
        if self.spec.key in SEAL_PREVIEW_TOOLS:
            self._build_seal_bench(page)
        else:
            self.seal_preview = None
            self.seal_pages = None

        # -------- the page map, on the jobs where seeing pages actually helps.
        #         It sits *above* the options because on the Split page the map
        #         is the main control; the limits below are only the fallback for
        #         someone who does not click any page, so they start folded away.
        if self.spec.key in PAGE_MAP_TOOLS:
            self.page_map = PageMap(page)
            self.page_map.grid(row=3, column=0, sticky="nsew", pady=(px(10), 0))
            page.rowconfigure(3, weight=1)
        else:
            self.page_map = None

        # -------- options (each row is a full-width ui_kit.Field)
        if self.spec.options:
            if self.page_map:
                holder = Disclosure(
                    page, "If you would rather not click pages",
                    hint="The map above is the normal way to split. These are "
                         "the automatic limits, used only when you click no "
                         "page at all.",
                    start_open=False)
                holder.grid(row=4, column=0, sticky="ew", pady=(px(8), 0))
                box = ttk.LabelFrame(holder.body, text="  Automatic limits  ",
                                     padding=10)
                box.grid(row=0, column=0, sticky="ew")
            else:
                box = ttk.LabelFrame(page, text="  Options  ", padding=10)
                box.grid(row=4, column=0, sticky="ew", pady=(px(10), 0))
            box.columnconfigure(0, weight=1)
            for row_index, opt in enumerate(self.spec.options):
                self._add_option(box, row_index, opt)
            # Every control on this page drives the picture, so each one gets a
            # trace that pushes its value into the preview.
            self._watch_seal_controls()
            # And on the split page, the page limit drives "Split evenly".
            self._watch_split_limit()

        # -------- run + live progress
        run = ttk.Frame(page, style="Card.TFrame")
        run.grid(row=5, column=0, sticky="ew", pady=(px(14), 0))
        self.btn_run = ttk.Button(run, text="   RUN   ", style="Run.TButton",
                                  command=self._start)
        self.btn_run.pack(side="left")
        self.btn_cancel = ttk.Button(run, text="Cancel", state="disabled",
                                     command=self.app._cancel)
        self.btn_cancel.pack(side="left", padx=(10, 0))
        # "See the pages" sends the result straight to the Check tab, so the
        # user never has to hunt through the output folder to verify it.
        self.btn_see = IconButton(run, "See the pages", self._see_pages,
                                  icon="eye", background=CARD, surface=SURFACE)
        self.btn_see.pack(side="left", padx=(px(10), 0))
        self.btn_see.set_enabled(False)
        self.btn_open = ttk.Button(run, text="Open folder", command=self._open_results,
                                   state="disabled")
        self.btn_open.pack(side="left", padx=(px(10), 0))
        self.var_status = tk.StringVar(value="Pick the files, then press RUN.")
        ttk.Label(run, textvariable=self.var_status, style="Status.TLabel").pack(
            side="left", padx=(px(14), 0))
        self.progress = ttk.Progressbar(page, mode="indeterminate", length=220)
        self.progress_running = False

        # -------- "what next?" bar (hidden until a run succeeds)
        # Instead of dumping the user into Explorer after every job, this offers
        # the two things they actually want: keep the file (open it) or keep
        # working (hand the result to the next step).
        self.next_bar = tk.Frame(page, bg=CARD, highlightthickness=px(1),
                                 highlightbackground=BORDER)
        self.next_bar.grid(row=6, column=0, sticky="ew", pady=(px(10), 0))
        self.next_bar.grid_remove()
        self.next_row = tk.Frame(self.next_bar, bg=CARD)
        self.next_row.pack(fill="x", padx=px(10), pady=px(8))

    def _see_pages(self) -> None:
        """Open the first finished file on the Check the result tab."""
        self.app._show_outputs_in_preview(self.outputs)

    def _build_seal_bench(self, page: ttk.Frame) -> None:
        """Build the seal workbench: a page picker and the live picture.

        Controls on the left, picture on the right, so every click is answered
        at once instead of the user having to imagine where "bottom-right, 20%,
        0.9" will actually land.  The picture column has a *fixed* width on
        purpose: letting the two columns share the width evenly left a wide
        empty gap and made the bench far taller than the page needed.
        """
        bench = tk.Frame(page, bg=CARD, highlightthickness=px(2),
                         highlightbackground=BORDER)
        bench.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        bench.columnconfigure(0, weight=1)             # controls take the rest
        bench.columnconfigure(1, weight=0)             # picture: its own size
        # "nsew", not "new": the controls column has to *fill* its cell, or Tk
        # keeps it at its natural width and clips whatever hangs off the end -
        # which is how the "Choose..." button and the slider numbers went
        # missing.
        left = tk.Frame(bench, bg=CARD)
        left.grid(row=0, column=0, sticky="nsew", padx=(px(14), px(10)))
        right = tk.Frame(bench, bg=CARD)
        right.grid(row=0, column=1, sticky="ne", padx=(0, px(14)))
        left.columnconfigure(0, weight=1)

        head = tk.Frame(right, bg=CARD)
        head.pack(fill="x", pady=(0, px(6)))
        Icon(head, "eye", 16, ACCENT, background=CARD).pack(side="left",
                                                            padx=(0, px(6)))
        ttk.Label(head, text="What you will get", style="CardHead.TLabel",
                  font=(FONT, HEADING, "bold")).pack(side="left")
        # The sliders live in the controls column, not under the picture: that
        # column is far wider, and a picture-width strip is no room for a
        # track plus a number.
        self.seal_preview = SealPreview(
            right, on_change=self._on_seal_nudge,
            on_slider=self._on_seal_sliders, slider_host=left)
        self.seal_preview.pack()

        # ---- your own seal, right here in the bench
        # This used to live only inside the collapsed "Options" panel, which
        # meant there was no visible way to pick your own seal - the sample one
        # was all you could ever use.  A visible box + button fixes that.
        ttk.Label(left, text="Your seal - PNG, JPG or TIFF",
                  style="CardMuted.TLabel").pack(anchor="w",
                                                 pady=(px(12), px(4)))
        picker = tk.Frame(left, bg=CARD)
        picker.pack(anchor="w", fill="x")
        self.seal_image_var = self.option_vars.get("seal_image")
        if self.seal_image_var is None:
            self.seal_image_var = tk.StringVar(
                value=str(self.app.cfg.resolve("paths.stamp_image")))
            self.option_vars["seal_image"] = self.seal_image_var
        self.seal_image_entry = ttk.Entry(picker, width=20,
                                          textvariable=self.seal_image_var)
        self.seal_image_entry.pack(side="left")
        ttk.Button(picker, text="Choose...",
                   command=lambda: self._pick_image(
                       self.seal_image_var)).pack(side="left", padx=(px(8), 0))

        # ---- which page the picture shows
        ttk.Label(left, text="Look at the page",
                  style="CardMuted.TLabel").pack(anchor="w",
                                                 pady=(px(12), px(4)))
        viewer = tk.Frame(left, bg=CARD)
        viewer.pack(anchor="w")
        self.page_stepper = Stepper(viewer, minimum=1, maximum=1, value=1,
                                    step=1, width=3,
                                    command=self._on_step_page)
        self.page_stepper.pack(side="left")
        self.var_preview_total = tk.StringVar(value="")
        ttk.Label(viewer, textvariable=self.var_preview_total,
                  style="CardMuted.TLabel").pack(side="left", padx=(px(8), 0))

        # ---- which pages actually get the seal
        ttk.Label(left, text="Put the seal on", style="CardMuted.TLabel").pack(
            anchor="w", pady=(px(16), px(4)))
        self.var_seal_pages = tk.StringVar(value="all")
        self.seal_pages = Segmented(
            left, (("all", "Every page"), ("first", "First only"),
                   ("last", "Last only"), ("odd", "Odd pages"),
                   ("even", "Even pages")),
            variable=self.var_seal_pages, fill=False)
        self.seal_pages.pack(anchor="w")
        self.var_seal_pages.trace_add("write", self._on_seal_pages)

        # Size and strength, as horizontal volume-bar style sliders.  Built
        # last so they sit under the choices rather than above them.
        ttk.Label(left, text="How it looks", style="CardMuted.TLabel").pack(
            anchor="w", pady=(px(18), 0))
        self.seal_preview.build_sliders()

    def _watch_seal_controls(self) -> None:
        """Point the preview at the position / size / strength controls."""
        for key in ("position", "width_percent", "opacity", "seal_image"):
            var = self.option_vars.get(key)
            if var is None:
                continue
            if key == "seal_image":
                var.trace_add("write", lambda *_a: self._push_seal())
            else:
                var.trace_add("write",
                              lambda *_a, a=key: self._push_placement(a))

    def _watch_split_limit(self) -> None:
        """Keep the page map's "Split evenly" in step with the page limit.

        This used to happen only inside ``_start`` - at RUN time.  So pressing
        "Split evenly" before running used a part size of zero, which the map
        treated as "the whole document", and the marks it produced ignored the
        limit the user had typed.  Those marks are then sent to the splitter as
        explicit page cuts, which is why parts came back far over the limit.
        """
        if self.page_map is None:
            return
        var = self.option_vars.get("max_pages")
        if var is None:
            return
        self._push_split_limit()                    # so it is right immediately
        var.trace_add("write", lambda *_a: self._push_split_limit())

    def _push_split_limit(self) -> None:
        if self.page_map is None:
            return
        var = self.option_vars.get("max_pages")
        if var is None:
            return
        try:
            pages = int(str(var.get()).strip() or 0)
        except ValueError:
            pages = 0
        if pages > 0:
            self.page_map.set_part_size(pages)

    def _push_seal(self) -> None:
        if self.seal_preview is not None:
            self.seal_preview.set_seal(self.option_vars["seal_image"].get())

    def _push_placement(self, key: str) -> None:
        """Send one control's value to the preview, in its stored form."""
        if self.seal_preview is None:
            return
        var = self.option_vars.get(key)
        if var is None:
            return
        raw = var.get()
        mapping = getattr(self, "_choice_maps", {}).get(key)
        if mapping:
            raw = mapping.get(str(raw), str(raw))
        self.seal_preview.set_placement(**{key: raw})

    def _on_seal_nudge(self, x_mm: float, y_mm: float) -> None:
        """Remember the nudge so the runner places the seal identically."""
        self.nudge_x, self.nudge_y = x_mm, y_mm

    def _on_seal_sliders(self) -> None:
        """A slider moved: copy the value into the options the run reads.

        Without this the sliders would only change the picture: drag the size
        slider, admire it, press RUN, and the seal comes out the old size.
        """
        preview = self.seal_preview
        if preview is None:
            return
        for key, value in (("width_percent", preview.width_percent),
                           ("opacity", preview.opacity)):
            var = self.option_vars.get(key)
            if var is not None and str(var.get()) != str(value):
                var.set(value)

    def _on_step_page(self, value: float) -> None:
        """The -/+ arrows move the page shown in the picture."""
        if self.seal_preview is not None:
            self.seal_preview.set_page(max(0, int(value) - 1))

    def _on_seal_pages(self, *_args) -> None:
        """Keep the picture on a page that will actually be sealed."""
        total = self.seal_preview.page_count if self.seal_preview else 0
        if not total:
            return
        spec = str(self.var_seal_pages.get() or "all")
        # notify=True so the picture follows the jump as well as the label.
        if spec == "first":
            self.page_stepper.set(1, notify=True)
        elif spec == "last":
            self.page_stepper.set(total, notify=True)

    def _refresh_seal_preview(self) -> None:
        """Point the preview at the first chosen file and the chosen seal."""
        if self.seal_preview is None:
            return
        first = next((p for p in self.paths if p.suffix.lower() == ".pdf"), None)
        self.seal_preview.set_document(first)
        self._push_seal()
        for key in ("position", "width_percent", "opacity"):
            self._push_placement(key)
        total = self.seal_preview.page_count
        self.var_preview_total.set(f"of {total}" if total else "")
        if total:
            self.page_stepper.set_range(1, total)
            if self.page_stepper.get() > total:
                self.page_stepper.set(total, notify=True)

    def _add_option(self, box: ttk.Frame, row: int, opt: tuple) -> None:
        """Build one tool option, click-only where a click makes sense.

        Mirrors the settings dialog: booleans are switches, short choice lists
        are button rows and numbers are -/+ steppers.  Only free text and file
        paths stay as entries.
        """
        kind, label, key, default, extra = opt
        hint = TIP_TEXT.get(key, "")
        if kind == "bool":
            var: tk.Variable = tk.BooleanVar(value=bool(default))
            Switch(box, label, value=bool(default), hint=hint, variable=var,
                   background=CARD).grid(row=row, column=0, columnspan=3,
                                         sticky="ew", pady=px(3))
        elif kind == "int":
            low, high = extra or (0, 9999)
            var = tk.IntVar(value=int(default))
            step = 1 if high - low <= 30 else 10
            Field(box, label, hint=hint, background=CARD,
                  factory=lambda field: Stepper(
                      field, minimum=low, maximum=high, value=int(default),
                      step=step, width=8, command=var.set)).grid(
                row=row, column=0, columnspan=3, sticky="ew", pady=px(3))
        elif kind == "float":
            var = tk.DoubleVar(value=float(default))
            Field(box, label, hint=hint, background=CARD,
                  factory=lambda field: Stepper(
                      field, minimum=0.0, maximum=1000.0, value=float(default),
                      step=0.1, decimals=1, width=8, command=var.set)).grid(
                row=row, column=0, columnspan=3, sticky="ew", pady=px(3))
        elif kind == "choice":
            labels = [text for _v, text in extra]
            reverse = {text: str(v) for v, text in extra}
            forward = {str(v): text for v, text in extra}
            if len(extra) <= 3:
                var = tk.StringVar(value=str(default))
                widget = Segmented(box, extra, variable=var, fill=True)
                widget.set(forward.get(str(default), labels[0]))
                control = Field(box, label, widget, hint=hint, background=CARD)
            else:
                var = tk.StringVar(value=forward.get(str(default), labels[0]))
                control = Field(box, label, ttk.Combobox(
                    box, state="readonly", width=32, values=labels,
                    textvariable=var), hint=hint, background=CARD)
            control.grid(row=row, column=0, columnspan=3, sticky="ew",
                         pady=px(3))
            self._choice_maps = getattr(self, "_choice_maps", {})
            self._choice_maps[key] = reverse
        elif kind == "file":
            # The seal bench builds a visible box for this *before* the options
            # are built, so reuse that variable when it exists - otherwise the
            # run would read a different one from the one on screen.
            var = self.option_vars.get(key)
            if var is None:
                var = tk.StringVar(
                    value=str(self.app.cfg.resolve("paths.stamp_image")))
            holder = tk.Frame(box, bg=CARD)
            ttk.Entry(holder, textvariable=var, width=44).pack(side="left")
            ttk.Button(holder, text="Choose...",
                       command=lambda v=var: self._pick_image(v)).pack(
                side="left", padx=px(8))
            Field(box, label, holder, hint=hint, background=CARD).grid(
                row=row, column=0, columnspan=3, sticky="ew", pady=px(3))
        else:
            var = tk.StringVar(value=str(default))
            Field(box, label, ttk.Entry(box, textvariable=var, width=40),
                  hint=hint, background=CARD).grid(
                row=row, column=0, columnspan=3, sticky="ew", pady=px(3))
        self.option_vars[key] = var

    # ------------------------------------------------------------- file list
    def _enable_drop(self, widget: tk.Misc) -> None:
        """Accept files AND folders dragged in from Explorer / Desktop."""
        register_drop(widget, self._on_drop)

    def _on_drop(self, event) -> None:
        added = self._ingest_paths(parse_dropped(event.data))
        if added:
            self.var_status.set(f"{added} file(s) added by drag & drop - "
                                f"{len(self.paths)} ready - press RUN.")
            self.app.log_line(f"{self.spec.title}: {added} file(s) added by drag & drop")
        else:
            self.var_status.set("Nothing usable in what was dropped - "
                                "use PDF or image files.")

    def _ingest_paths(self, paths: list[Path]) -> int:
        """Add dropped files; for dropped FOLDERS add every usable file inside."""
        from . import pdf_ops

        exts = set(self.app.cfg.get("merge.file_extensions") or [".pdf"])
        added = 0
        for path in paths:
            if path.is_dir():
                for found in pdf_ops.collect_inputs(path, list(exts)):
                    if found not in self.paths:
                        self.paths.append(found)
                        added += 1
            elif path.is_file() and path.suffix.lower() in exts:
                if path not in self.paths:
                    self.paths.append(path)
                    added += 1
        if added:
            self._refresh_list()
        return added

    def _refresh_list(self) -> None:
        self.file_list.delete(0, "end")
        for path in self.paths:
            self.file_list.insert("end", str(path))
        if self.paths:
            self.empty_hint.grid_remove()
            self.file_list.grid()
        else:
            self.file_list.grid_remove()
            self.empty_hint.grid()
        if self.paths and not self.var_out.get().strip():
            self.var_out.set("")            # keep 'same folder as first file'
        self._refresh_page_map()

    def _refresh_page_map(self) -> None:
        """Re-read the page counts and rebuild the strip of pages.

        Page counting opens each PDF but never renders it, so this stays quick
        even with a hundred files; the thumbnails themselves are lazy.
        """
        if self.page_map is not None:
            sources: list[tuple[str, int]] = []
            for path in self.paths:
                if path.suffix.lower() != ".pdf":
                    continue
                try:
                    import pymupdf

                    with pymupdf.open(path) as document:
                        sources.append((str(path), document.page_count))
                except Exception:
                    sources.append((str(path), 0))
            self.sources = sources
            self.page_map.set_sources(sources)
        self._refresh_seal_preview()
        self._refresh_seal_preview()

    def _add_files(self) -> None:
        chosen = filedialog.askopenfilenames(title=f"{self.spec.title} - choose files",
                                             filetypes=PDF_TYPES)
        existing = set(self.paths)
        for item in chosen:
            path = Path(item)
            if path not in existing:
                self.paths.append(path)
        self._refresh_list()
        self.var_status.set(f"{len(self.paths)} file(s) ready - press RUN.")

    def _add_folder(self) -> None:
        """One click: every usable PDF/image inside a folder (sub-folders too)."""
        chosen = filedialog.askdirectory(title="Choose a folder - all usable files are added")
        if not chosen:
            return
        from . import pdf_ops

        found = pdf_ops.collect_inputs(
            chosen, self.app.cfg.get("merge.file_extensions") or [".pdf"])
        existing = set(self.paths)
        added = 0
        for path in found:
            if path not in existing:
                self.paths.append(path)
                existing.add(path)
                added += 1
        self._refresh_list()
        self.var_status.set(f"{added} file(s) added from {Path(chosen).name} "
                            f"- {len(self.paths)} ready - press RUN." if added
                            else "Nothing usable in that folder.")

    def _remove_selected(self) -> None:
        for index in reversed(self.file_list.curselection()):
            del self.paths[index]
        self._refresh_list()

    def _move_selected(self, direction: int) -> None:
        """Move the selected files up/down in the list (order matters)."""
        picked = list(self.file_list.curselection())
        if not picked:
            return
        if direction < 0:
            picked.sort()
            for index in picked:
                if index == 0:
                    continue
                self.paths[index - 1], self.paths[index] = (
                    self.paths[index], self.paths[index - 1])
            keep = [i - 1 for i in picked if i > 0]
        else:
            picked.sort(reverse=True)
            last = len(self.paths) - 1
            for index in picked:
                if index == last:
                    continue
                self.paths[index + 1], self.paths[index] = (
                    self.paths[index], self.paths[index + 1])
            keep = [i + 1 for i in picked if i < last]
        self._refresh_list()
        for index in keep:
            self.file_list.selection_set(index)
        if keep:
            self.file_list.see(min(keep))

    def _clear_files(self) -> None:
        self.paths.clear()
        self._refresh_list()

    def _pick_out(self) -> None:
        chosen = filedialog.askdirectory()
        if chosen:
            self.var_out.set(str(Path(chosen)))

    def _pick_image(self, variable: tk.StringVar) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose the seal image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tif"), ("All files", "*.*")])
        if chosen:
            variable.set(chosen)

    # ------------------------------------------------------------------ run
    def _start(self) -> None:
        app = self.app
        if app.worker and app.worker.is_alive():
            messagebox.showinfo("Busy", "A job is already running - wait for it to finish.")
            return
        if not self.paths:
            messagebox.showerror("No files", "Add at least one PDF or image first.")
            return
        opts = {}
        for key, var in self.option_vars.items():
            mapping = getattr(self, "_choice_maps", {}).get(key)
            if mapping:
                opts[key] = mapping.get(str(var.get()), str(var.get()))
            else:
                opts[key] = var.get() if isinstance(var, tk.BooleanVar) else str(var.get()).strip()
        # Hand the page map's decisions to the runner.  "Split evenly" is wired
        # through the same map, so both routes end up in one place.
        if self.page_map is not None:
            self.page_map.set_part_size(int(opts.get("max_pages", 50) or 50))
            opts["sources"] = list(self.sources)
            opts["cuts"] = [tuple(cut) for cut in self.page_map.cuts()]
        if self.seal_preview is not None:
            # Hand the picture's decisions to the runner, so what was arranged
            # is what gets written.
            opts["nudge_x"] = self.nudge_x
            opts["nudge_y"] = self.nudge_y
            opts["pages"] = str(self.var_seal_pages.get() or "all")
        out_text = self.var_out.get().strip()
        out_dir = Path(out_text) if out_text else self.paths[0].parent
        if not out_dir.is_dir():
            messagebox.showerror("Folder not found", f"This folder does not exist:\n{out_dir}")
            return

        self.outputs = []
        self.btn_run.state(["disabled"])
        self.btn_cancel.state(["!disabled"])
        self.btn_open.state(["disabled"])
        self.btn_see.set_enabled(False)
        self.next_bar.grid_remove()
        self.app.cancel_flag.clear()
        self.var_status.set("Working...")
        if not self.progress_running:
            # grid, not pack: the page's rows are all grid-managed, and Tk
            # refuses to mix the two inside one parent.
            self.progress.grid(row=5, column=0, sticky="ew", pady=(px(10), 0))
            self.progress.start(12)
            self.progress_running = True
        app.log_line("")
        app.log_line(f"{self.spec.title}: {len(self.paths)} file(s) -> {out_dir}")
        app.log_line("-" * 60)
        paths = list(self.paths)
        app.worker = threading.Thread(target=self._run_job,
                                      args=(paths, out_dir, opts), daemon=True)
        app.worker.start()

    def _run_job(self, paths: list[Path], out_dir: Path, opts: dict) -> None:
        app = self.app
        logger = None
        handler = None
        try:
            reset_logger()
            logger = get_logger(app.cfg.resolve("paths.logs"), verbose=True)
            handler = GuiLogHandler(
                lambda level, message: app.events.put(("log", level, message)))
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)
            ctx = {"progress": self._progress, "gs_path": app.gs_path}
            outputs, warnings = self.spec.runner(paths, out_dir, opts, ctx)
            app.events.put(("tool_done", self.spec.key, outputs, warnings))
        except JobCancelled:
            app.events.put(("tool_cancelled", self.spec.key))
        except Exception as exc:              # noqa: BLE001 - shown in the window
            app.events.put(("tool_error", self.spec.key, f"{type(exc).__name__}: {exc}"))
        finally:
            if logger is not None and handler is not None:
                try:
                    logger.removeHandler(handler)
                except Exception:
                    pass

    def _progress(self, message: str) -> None:
        if self.app.cancel_flag.is_set():
            raise JobCancelled()
        self.app.events.put(("progress", message))

    def _open_results(self) -> None:
        if self.outputs:
            open_in_explorer(self.outputs[0].parent)

    # ------------------------------------------------------------ completion
    def _stop_progress(self) -> None:
        """Hide the progress bar.  Uses grid because the page rows are grid."""
        if not self.progress_running:
            return
        self.progress.stop()
        self.progress.grid_remove()
        self.progress_running = False

    def _finish(self, outputs: list, warnings: list) -> None:
        self.outputs = list(outputs)
        self.btn_run.state(["!disabled"])
        self.btn_cancel.state(["disabled"])
        self.btn_open.state(["!disabled"])
        self.btn_see.set_enabled(bool(outputs))
        self._stop_progress()
        for warning in warnings:
            self.app.log_line(f"WARNING: {warning}")
        self.app.log_line("-" * 60)
        self.app.log_line(f"{self.spec.title} finished - {len(outputs)} file(s):")
        for output in outputs:
            self.app.log_line(f"   {output.name}   {output.stat().st_size / MB:.2f} MB")
        saved_mb = sum(o.stat().st_size for o in outputs) / MB
        self.var_status.set(f"Done - {len(outputs)} file(s), {saved_mb:.2f} MB.")
        self.app.celebrate()
        # No more "dumped into Explorer after every job": the next-step bar
        # below offers the useful moves instead.
        self._show_next_steps(outputs)

    def _show_next_steps(self, outputs: list) -> None:
        """Offer the two things that matter now: keep it, or keep working.

        'Open PDF' is the download; every other button hands the finished file
        to the next logical tool with its files already loaded, so the user
        never re-adds the same file by hand.
        """
        for child in self.next_row.winfo_children():
            child.destroy()
        pdfs = [p for p in outputs if p.suffix.lower() == ".pdf"]
        if not pdfs:
            self.next_bar.grid_remove()
            return
        self.next_bar.grid()
        tk.Label(self.next_row, text="What next?", bg=CARD, fg=ACCENT,
                 font=(FONT, HEADING, "bold")).pack(side="left", padx=(0, px(10)))
        IconButton(self.next_row, "Open the PDF",
                   lambda p=pdfs[0]: self.app._open_file(p), icon="download",
                   primary=True, background=CARD).pack(side="left")
        for tool_key in TOOL_HANDOFF.get(self.spec.key, ()):
            spec = next(s for s in TOOL_SPECS if s.key == tool_key)
            IconButton(
                self.next_row, f"{spec.title} →",
                lambda k=tool_key, p=list(pdfs): self.app._hand_off_to_tool(k, p),
                icon=TOOL_ICON.get(tool_key, "launch"),
                background=CARD, surface=SURFACE).pack(side="left", padx=px(8))
        IconButton(self.next_row, "Preview pages", self._see_pages,
                   icon="eye", background=CARD, surface=SURFACE).pack(
            side="left", padx=px(8))
        IconButton(self.next_row, "Open folder", self._open_results,
                   icon="folder", background=CARD, surface=SURFACE).pack(
            side="left", padx=px(8))

    def _fail(self, message: str) -> None:
        self.btn_run.state(["!disabled"])
        self.btn_cancel.state(["disabled"])
        self._stop_progress()
        self.app.log_line(f"FAILED: {message}")
        self.var_status.set(f"Failed - {message[:100]}")

    def _cancelled(self) -> None:
        self.btn_run.state(["!disabled"])
        self.btn_cancel.state(["disabled"])
        self._stop_progress()
        self.app.log_line(f"{self.spec.title}: cancelled by user.")
        self.var_status.set("Cancelled - your files are untouched.")


# ==========================================================================
#  Preview tab - see your PDF before you upload it (100% local, PyMuPDF)
# ==========================================================================
class PreviewPanel:
    """Thumbnail strip + big page view + per-page facts, rendered on demand.

    Rendered pages are cached under work/preview_cache so re-opening is instant
    and nothing ever leaves the machine.
    """

    THUMB_W = 96

    def __init__(self, app: "App") -> None:
        self.app = app
        self.doc = None                       # pymupdf document
        self.path: Path | None = None
        self.mtime = 0.0
        self.page_no = 0
        self._images: list[tk.PhotoImage] = []          # keep Tk alive
        self._thumbs: list[tuple[tk.Canvas, int]] = []
        self.cache = PROJECT_ROOT / "work" / "preview_cache"

    # ------------------------------------------------------------------ build
    def build(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        bar = ttk.Frame(parent, padding=(10, 8))
        bar.grid(row=0, column=0, sticky="ew")
        IconButton(bar, "Open a PDF", self.open_dialog, icon="folder",
                   background=BG, surface=SURFACE).pack(side="left")
        IconButton(bar, "Latest result", self.open_latest, icon="bolt",
                   background=BG, surface=SURFACE).pack(side="left", padx=px(8))
        ttk.Button(bar, text="◀", width=3, command=lambda: self.show(self.page_no - 1)).pack(
            side="left", padx=(px(14), 0))
        self.var_page = tk.StringVar(value="no PDF open")
        ttk.Label(bar, textvariable=self.var_page, style="Status.TLabel").pack(
            side="left", padx=px(8))
        ttk.Button(bar, text="▶", width=3, command=lambda: self.show(self.page_no + 1)).pack(
            side="left", padx=px(8))
        IconButton(bar, "Open in another program", self.open_externally,
                   icon="launch", background=BG, surface=SURFACE).pack(
            side="right")

        content = ttk.Frame(parent, padding=(10, 0, 10, 8))
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        # left: thumbnail strip (one thumb per page, click to view)
        thumbs_wrap = ttk.Frame(content, width=136)
        thumbs_wrap.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        self.thumbs_canvas = tk.Canvas(thumbs_wrap, width=132, highlightthickness=0,
                                       background="#dde3ee")
        thumbs_scroll = ttk.Scrollbar(thumbs_wrap, orient="vertical",
                                      command=self.thumbs_canvas.yview)
        self.thumbs_inner = ttk.Frame(self.thumbs_canvas)
        self.thumbs_canvas.create_window((0, 0), window=self.thumbs_inner, anchor="nw")
        self.thumbs_inner.bind(
            "<Configure>", lambda _e: self.thumbs_canvas.configure(
                scrollregion=self.thumbs_canvas.bbox("all")))
        self.thumbs_canvas.configure(yscrollcommand=thumbs_scroll.set)
        thumbs_scroll.pack(side="right", fill="y")
        self.thumbs_canvas.pack(side="left", fill="both", expand=True)
        self.thumbs_canvas.bind(
            "<MouseWheel>", lambda e: self.thumbs_canvas.yview_scroll(
                -1 * (e.delta // 120), "units"))

        # right: the big page view
        self.big = tk.Canvas(content, highlightthickness=0, background="#4b5567")
        self.big.grid(row=0, column=1, sticky="nsew")
        self.var_facts = tk.StringVar(
            value="Open a PDF, drag one here, or use 'Latest result' after a run.")
        ttk.Label(parent, textvariable=self.var_facts, style="Status.TLabel",
                  padding=(12, 2)).grid(row=2, column=0, sticky="w")
        self._enable_drop(parent)

    def _enable_drop(self, widget: tk.Misc) -> None:
        register_drop(widget, self._on_drop)

    def _on_drop(self, event) -> None:
        for path in parse_dropped(event.data):
            if path.is_file() and path.suffix.lower() == ".pdf":
                self.open_path(path)
                return
            if path.is_dir():
                from . import pdf_ops

                found = pdf_ops.collect_inputs(
                    path, [".pdf"])
                if found:
                    self.open_path(found[0])
                    return
        self.var_facts.set("Drop a PDF (or a folder holding PDFs) to preview it.")

    # ----------------------------------------------------------------- opening
    def open_dialog(self) -> None:
        chosen = filedialog.askopenfilename(title="Preview a PDF",
                                            filetypes=[("PDF files", "*.pdf"),
                                                       ("All files", "*.*")])
        if chosen:
            self.open_path(Path(chosen))

    def open_latest(self) -> None:
        """Show the newest finished filing (the file the last run produced)."""
        candidates: list[Path] = []
        if self.app.report and self.app.report.outputs:
            candidates = list(self.app.report.outputs)
        out_root = self.app.cfg.resolve("paths.output")
        if out_root.is_dir():
            candidates += sorted(out_root.rglob("*Final*.pdf")) + \
                sorted(out_root.rglob("*_Part*.pdf"))
        if candidates:
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            self.open_path(newest)
        else:
            self.var_facts.set("No finished filing yet - RUN the automation once, "
                               "then come back here.")

    def open_externally(self) -> None:
        import os

        if self.path:
            try:
                os.startfile(str(self.path))
            except Exception as exc:
                messagebox.showerror("Could not open", str(exc))

    def open_path(self, path: Path) -> None:
        try:
            import pymupdf

            doc = pymupdf.open(path)
        except Exception as exc:
            messagebox.showerror("Cannot preview", f"{path.name}: {exc}")
            return
        self.close_doc()
        self.doc = doc
        self.path = path
        try:
            self.mtime = path.stat().st_mtime
        except OSError:
            self.mtime = 0.0
        self.page_no = 0
        self.app.log_line(f"Preview: {path.name} ({doc.page_count} pages)")
        self._build_thumbs()
        self.show(0)

    def close_doc(self) -> None:
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        self.path = None
        self._images.clear()
        self._thumbs.clear()
        for child in self.thumbs_inner.winfo_children():
            child.destroy()
        self.big.delete("all")

    # ----------------------------------------------------------------- rendering
    def _render_png(self, page_no: int, target_w: int) -> str:
        """Render one page to a cached PNG, return it base64 (tk PhotoImage data)."""
        import base64
        import hashlib

        zoom = target_w / max(1.0, self.doc[page_no].rect.width)
        key = hashlib.sha1(f"{self.path}|{self.mtime:.0f}|{page_no}|{zoom:.4f}"
                           .encode()).hexdigest()[:20]
        cached = self.cache / f"{key}.png"
        if cached.is_file():
            return base64.b64encode(cached.read_bytes()).decode("ascii")
        self.cache.mkdir(parents=True, exist_ok=True)
        data = self.doc[page_no].get_pixmap(matrix=pymupdf_zoom(zoom), alpha=False)
        cached.write_bytes(data.tobytes("png"))
        return base64.b64encode(data.tobytes("png")).decode("ascii")

    def _build_thumbs(self) -> None:
        for child in self.thumbs_inner.winfo_children():
            child.destroy()
        self._thumbs.clear()
        self._thumb_next = 0
        self._build_thumb_batch()

    def _build_thumb_batch(self) -> None:
        """Render thumbnails in small batches so the UI never freezes."""
        if self.doc is None:
            return
        total = self.doc.page_count
        batch_end = min(total, self._thumb_next + 20)
        for index in range(self._thumb_next, batch_end):
            frame = ttk.Frame(self.thumbs_inner)
            frame.pack(fill="x", pady=3)
            canvas = tk.Canvas(frame, width=120, height=158, highlightthickness=0,
                               background="#dde3ee")
            canvas.pack()
            try:
                png = self._render_png(index, self.THUMB_W)
                image = tk.PhotoImage(data=png)
                self._images.append(image)
                canvas.create_image(60, 6, image=image, anchor="n")
            except Exception:
                canvas.create_text(60, 70, text="?", fill=MUTED)
            canvas.create_text(60, 150, text=f"Page {index + 1}", fill=MUTED,
                               font=(FONT, 8))
            canvas.bind("<Button-1>", lambda _e, n=index: self.show(n))
            self._thumbs.append((canvas, index))
        self._thumb_next = batch_end
        if self._thumb_next < total:
            self.after(40, self._build_thumb_batch)

    def show(self, page_no: int) -> None:
        """Display one page, big, with its plain-English facts."""
        if self.doc is None:
            return
        page_no = max(0, min(page_no, self.doc.page_count - 1))
        self.page_no = page_no
        try:
            png = self._render_png(page_no, 700)
            self.big.delete("all")
            image = tk.PhotoImage(data=png)
            self._images.append(image)
            self.big.create_image(12, 12, image=image, anchor="nw")
            self.big.configure(scrollregion=(0, 0, image.width() + 24, image.height() + 24))
        except Exception as exc:
            self.var_facts.set(f"Could not render page {page_no + 1}: {exc}")
            return
        self.var_page.set(f"Page {page_no + 1} of {self.doc.page_count}")
        self._set_facts(page_no)

    def _set_facts(self, page_no: int) -> None:
        """Per-page facts: what kind of page it is, how sharp, how heavy."""
        try:
            page = self.doc[page_no]
            text_chars = len(page.get_text().strip())
            mm_w, mm_h = page.rect.width * 25.4 / 72, page.rect.height * 25.4 / 72
            file_kb = self.path.stat().st_size / 1024
            per_page_kb = file_kb / max(1, self.doc.page_count)
            kind = ("born-digital (real text)" if text_chars > 40
                    else "scan (no text layer)" if text_chars < 5
                    else f"mixed ({text_chars} characters of text)")
            self.var_facts.set(
                f"Page {page_no + 1}: {kind}  ·  {mm_w:.0f} × {mm_h:.0f} mm  ·  "
                f"about {per_page_kb:.0f} KB of {file_kb / 1024:.2f} MB file  ·  "
                f"{self.path.name}")
        except Exception:
            self.var_facts.set("")


def pymupdf_zoom(zoom: float):
    """pymupdf.Matrix without importing pymupdf at module level."""
    import pymupdf

    return pymupdf.Matrix(zoom, zoom)


# ==========================================================================
#  Main window
# ==========================================================================
class App(ttkb.Window if ttkb else tk.Tk):
    """Three clear steps, one big RUN button, no jargon on screen."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        if ttkb is not None:
            # BUGFIX: this used to be alpha=0.0, which made the whole window
            # 100% transparent - it appeared in the taskbar but was invisible
            # on screen. It must stay fully opaque.
            super().__init__(themename="flatly", alpha=1.0)
        else:
            super().__init__()

        # ---- 1. scale for this display, before a single widget exists ------
        #  Font metrics are frozen when a widget is created, so a window built
        #  at scaling 1.0 and resized afterwards keeps the wrong text sizes for
        #  ever.  Setting it here also makes ui_kit.px() - every padding, gap
        #  and minimum height in the app - correct on the real monitor.
        set_scale(UI_SCALE)
        try:
            self.tk.call("tk", "scaling", UI_SCALE * 96.0 / 72.0)
        except tk.TclError:
            pass

        # ---- 2. hide the window, show the loading card ---------------------
        #  Building the workspace takes ~1.9 s (measured, and longer on a cold
        #  or virus-scanned machine).  Without this the user watched a blank
        #  window appear, sit completely unresponsive - Windows sometimes
        #  labelled it "(Not Responding)" - and then jump to its real size when
        #  _fit_to_screen() ran.  That was the "broken / frozen" start-up.
        #  Now the half-built window is never visible at all: the card covers
        #  the wait and the finished window appears at its final size.
        self._hide_main_window()
        self._splash = make_splash(self)
        # Kept for _boot(), which does the work: storing it now means the config
        # path is read from one place only.
        self._config_path = config_path
        try:
            self._boot()
        finally:
            # Whatever happens - including a step raising - the card comes down.
            # A crash that leaves a loading screen on the desktop with no window
            # behind it is the least helpful failure there is; the error is
            # reported by _report_startup_failure() instead.
            self._splash.close()
        self._show_main_window()

        # belt-and-braces: guarantee the window is actually visible and on top
        # (protects against a remembered off-screen/alpha state)
        self.after(150, lambda: self._ensure_visible())

        # ---- remembered window size + last used tab (clamped to the screen)
        self._restore_geometry()
        try:
            tab = int(self.cfg.get("ui.tab", 0) or 0)
            if 0 <= tab < len(self.book.tabs()):
                self.book.select(tab)
        except Exception:
            pass

        # ---- keyboard: F1 help, F5 re-check folder, Ctrl+O add files (tools)
        self.bind_all("<F1>", lambda _e: self._show_help())
        self.bind_all("<F5>", lambda _e: self._check_documents())
        self.bind_all("<Control-o>", lambda _e: self._add_files_current_tool())

        # ---- drag & drop engine (optional, graceful without it)
        if try_enable_dnd(self):
            self.log_line("Drag & drop is on - drop files or whole folders "
                          "onto any tool page.")
        else:
            self.log_line("(Drag & drop engine not installed - the Add buttons "
                          "do the same job.)")

    def _boot(self) -> None:
        """Everything between "the card is up" and "the window is ready".

        Split out of :meth:`__init__` so the caller can wrap it in a ``finally``
        that always takes the loading card down, even if a step here raises.

        This is the one place a *new* slow step may be introduced, and it must
        come with a ``splash.step()`` in front of it: the card's ``pump()`` is
        what keeps it animating while a long step blocks the main thread, and a
        step with no message behind it is a step that looks like a freeze.
        """
        splash = self._splash
        splash.step(0.08, "Reading your settings...")

        self.cfg = Config.load(self._config_path)
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.tool_worker: threading.Thread | None = None
        self.report = None
        self.last_output: Path | None = None
        self.step_rows: dict[str, tuple[tk.Label, tk.Label]] = {}
        self.step_state: dict[str, str] = {}
        self.gs_path: str | None = None
        #: Set for real once the background tool check reports back; False until
        #: then so nothing can read a missing attribute.
        self.ghostscript_ready = False
        self.cancel_flag = threading.Event()
        self.run_started_at: datetime | None = None

        set_taskbar_identity()
        self.title(APP_NAME)
        self.configure(background=BG)
        splash.step(0.18, "Preparing the workspace...")
        apply_theme(self)
        load_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.tool_pages: dict[str, "ToolPage"] = {}
        self._make_variables()
        splash.step(0.34, "Building the workspace...")
        self._build_layout(splash)
        self._load_from_config()
        splash.step(0.78, "Getting the details right...")
        self._fit_to_screen()                      # fits + centres on this display
        self._mark_all_steps("waiting", "not started")
        self.after(120, self._drain_events)

        # The tool check runs tesseract and ghostscript as subprocesses.  It used
        # to block the UI thread right here.  It now runs in the background, but
        # we give it a short, bounded moment on the card first: it usually takes
        # ~0.2 s, and when it does, the banner and the log are already correct by
        # the time the user sees the window.  A slow machine is never made to
        # wait for it - see _check_tools_async().
        splash.step(0.88, "Checking the OCR engine...")
        self._check_tools_async(wait_seconds=0.9)
        splash.step(1.0, "Ready.")
        splash.pump()

    def _enable_automation_drop(self, page: ScrollFrame) -> None:
        """Drop on the Automation tab: a folder becomes the documents folder."""
        register_drop(page.inner, self._on_automation_drop)

    def _on_automation_drop(self, event) -> None:
        paths = parse_dropped(event.data)
        if len(paths) == 1 and paths[0].is_dir():
            self.var_input.set(str(paths[0]))
            if not self.var_case.get().strip():
                self.var_case.set(paths[0].name)
            self._remember_folder(paths[0])
            self._check_documents()
            return
        folders = [p for p in paths if p.is_dir()]
        if folders:
            self.var_input.set(str(folders[0]))
            self._remember_folder(folders[0])
            self._check_documents()
            return
        self._set_status("Drop a FOLDER here to load a case - or drag files "
                         "into a page under PDF Tools.")

    # ----------------------------------------------------- "what next?" flow
    def _open_file(self, path: Path) -> None:
        """Open one file with its normal Windows program (the 'download')."""
        import os

        if not path.is_file():
            messagebox.showinfo("File not found", f"{path} is no longer there.")
            return
        try:
            os.startfile(str(path))                # noqa: S606 - Windows shell
        except Exception as exc:
            messagebox.showerror("Could not open", str(exc))

    def _hand_off_to_tool(self, tool_key: str, outputs) -> None:
        """Load a finished result into another tool page, ready to RUN.

        This is the 'go to the next step' move: instead of downloading the file,
        finding it in Explorer and re-adding it by hand, one click carries the
        outputs straight into the next job.
        """
        page = self.tool_pages.get(tool_key)
        if page is None:
            return
        pdfs: list[Path] = []
        seen = set()
        for path in outputs:
            path = Path(path)
            if path.suffix.lower() != ".pdf" or not path.is_file():
                continue
            resolved = str(path.resolve()).lower()
            if resolved in seen:
                continue
            seen.add(resolved)
            pdfs.append(path)
        if not pdfs:
            self._set_status("That step works on PDFs - nothing to hand over.")
            return
        page.paths = pdfs
        page._refresh_list()
        try:
            self.book.select(1)                       # the tools tab
            for index, tab_id in enumerate(self.tool_book.tabs()):
                if str(self.tool_book.nametowidget(tab_id)) == str(page.scroller):
                    self.tool_book.select(index)
                    break
        except Exception:
            pass
        page.var_status.set(f"{len(pdfs)} file(s) loaded from the previous "
                            f"step - press RUN when ready.")
        self.log_line(f"Hand-off: {len(pdfs)} file(s) loaded into "
                      f"'{page.spec.title}'.")
        self._set_status(f"Ready for '{page.spec.title}' - the files are "
                         f"already loaded, just press RUN.")

    def build_handoff_row(self, host: tk.Frame, outputs,
                          background: str = CARD) -> None:
        """Fill ``host`` with 'use this result in...' buttons for a result."""
        for child in host.winfo_children():
            child.destroy()
        pdfs = [p for p in outputs if str(Path(p).suffix).lower() == ".pdf"
                and Path(p).is_file()]
        if not pdfs:
            host.grid_remove()
            return
        host.grid()
        tk.Label(host, text="Use this result in:", bg=background, fg=MUTED,
                 font=(FONT, CAPTION, "bold")).pack(side="left", padx=(0, px(8)))
        offered = ("ocr", "compress", "paginate", "stamp", "split")
        for tool_key in offered:
            spec = next(s for s in TOOL_SPECS if s.key == tool_key)
            IconButton(host, spec.title,
                       lambda k=tool_key, p=list(pdfs): self._hand_off_to_tool(k, p),
                       icon=TOOL_ICON.get(tool_key, "launch"),
                       background=background, surface=SURFACE).pack(
                side="left", padx=px(4))

    def _add_files_current_tool(self) -> None:
        """Ctrl+O: add files on whichever tool page is open."""
        try:
            page = self.tool_pages[TOOL_SPECS[self.tool_book.index("current")].key]
        except Exception:
            return
        if self.book.index(self.book.select()) == 1:
            page._add_files()

    def _fit_to_screen(self, height_logical: int = 780) -> None:
        """Size and centre the window so it always fits the visible screen.

        The old hard-coded ``1020x780`` became 1530x1170 physical pixels on a
        150% laptop display - taller than a 1080p screen, so the bottom of the
        window (RUN button, step list, progress bar) sat under the taskbar with
        no way to reach it.  Sizes here are multiplied by :data:`UI_SCALE` and
        then clamped to the screen, with the font scaling matched to the DPI so
        the text is crisp instead of stretched.
        """
        try:
            self.tk.call("tk", "scaling", UI_SCALE * 96.0 / 72.0)
        except tk.TclError:
            pass
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = int(min(1020 * UI_SCALE, screen_w - 40))
        height = int(min(height_logical * UI_SCALE, screen_h - 90))
        self.minsize(int(min(720 * UI_SCALE, width)), int(min(400 * UI_SCALE, height)))
        self.geometry(
            f"{width}x{height}"
            f"+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}"
        )

    def _restore_geometry(self) -> bool:
        """Re-apply the window size remembered from the last session.

        The value is **clamped to the current screen**, and a value that looks
        like a maximised window is ignored outright.

        This matters more than it sounds.  Tk records the geometry of a
        maximised window, so a machine where the app was ever zoomed saves
        something like ``1920x974+322+35`` - full screen width, offset 322 px to
        the right.  Restoring that unclamped put the right-hand third of the
        interface off the edge of a 1920-wide display, on every single launch,
        which is exactly the sort of thing that gets reported as "the UI looks
        broken".  The screen it was recorded on may not even be plugged in any
        more.

        Returns ``True`` when a remembered size was usable.
        """
        saved = str(self.cfg.get("ui.geometry", "") or "").strip()
        match = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", saved)
        if not match:
            return False
        width, height, x, y = (int(value) for value in match.groups())

        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        # Leave room for the taskbar and for the window borders Windows adds to
        # GetWindowRect but not to this number.
        max_w, max_h = screen_w - 40, screen_h - 90
        min_w = int(min(720 * UI_SCALE, max_w))
        min_h = int(min(400 * UI_SCALE, max_h))

        # "As big as the screen" is a zoomed window, not a size the user picked.
        if width >= screen_w - 20 or height >= screen_h - 60:
            return False

        width = max(min_w, min(width, max_w))
        height = max(min_h, min(height, max_h))
        x = max(0, min(x, screen_w - width))
        y = max(0, min(y, screen_h - height))
        try:
            self.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            return False
        return True

    def _ensure_visible(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            self.attributes("-alpha", 1.0)
        except Exception:
            pass

    def _hide_main_window(self) -> None:
        """Keep the half-built window off the screen while it is assembled.

        A window that is created but not yet laid out is painted at Tk's default
        geometry and cannot answer Windows, so the user sees a blank rectangle
        that then jumps.  Withdrawing it means the first thing they ever see is
        the finished window at its final size, straight out of the loading card.
        """
        try:
            self.withdraw()
        except tk.TclError:
            pass

    def _show_main_window(self) -> None:
        """Reveal the finished window, centred, opaque and on top."""
        try:
            self.deiconify()
            self.attributes("-alpha", 1.0)
            self.lift()
        except tk.TclError:
            pass
        self._ensure_visible()

    # ------------------------------------------------------------- variables
    def _make_variables(self) -> None:
        cfg = self.cfg
        self.var_case = tk.StringVar(value=str(cfg.get("case.name", "") or ""))
        self.var_input = tk.StringVar(value=str(cfg.resolve("paths.input")))
        self.var_output = tk.StringVar(value=str(cfg.resolve("paths.output")))
        self.var_seal = tk.StringVar(value=str(cfg.resolve("paths.stamp_image")))
        self.var_preset = tk.StringVar(value=str(cfg.get("ui.preset", "standard")))
        self.var_max_pages = tk.StringVar(value=str(cfg.get("split.max_pages_per_file", 50)))
        self.var_max_mb = tk.StringVar(
            value=str(cfg.get("compress.target_size_mb", 4.5)))
        self.var_status = tk.StringVar(value="Ready. Pick the documents folder, then press RUN.")
        self.var_found = tk.StringVar(value="No folder checked yet.")
        self.var_details = tk.BooleanVar(value=False)

    # ---------------------------------------------------------------- layout
    def _build_layout(self, splash=None) -> None:
        """Four tabs: the one-click automation, the tools, settings, preview.

        ``splash`` is optional and only used during start-up.  Each tab is a
        sizeable chunk of work (the tool pages alone build six sub-pages), so
        pumping the loading card between them keeps its animation moving instead
        of freezing for the whole 0.5 s and looking - again - like a crash.
        """
        def breathe(fraction: float, message: str = "") -> None:
            if splash is not None:
                splash.step(fraction, message or None)

        self._build_header(self)
        self._build_footer(self)
        breathe(0.40, "Building the workspace...")
        book = ttk.Notebook(self)
        book.pack(fill="both", expand=True, padx=px(8), pady=(px(8), px(4)))
        self.book = book

        # ---- tab 1: the automation pipeline (the one-click path)
        auto = ScrollFrame(book)
        book.add(auto, text="  Prepare a case  ")
        book.tab(auto, padding=(px(10), px(6)))
        auto.inner.columnconfigure(0, weight=1)
        self._build_step1(auto.inner)
        self._build_step2(auto.inner)
        self._build_step3(auto.inner)

        # ---- tab 2: one page per single task
        breathe(0.52, "Building the workspace...")
        tools = ttk.Frame(book)
        book.add(tools, text="  PDF tools  ")
        book.tab(tools, padding=(px(10), px(6)))
        self._build_tools(tools)

        # ---- tab 3: settings
        breathe(0.64, "Getting the details right...")
        settings = ScrollFrame(book)
        book.add(settings, text="  Settings  ")
        book.tab(settings, padding=(px(10), px(6)))
        self._build_settings(settings.inner)

        # ---- tab 4: preview (thumbnails + page facts, fully local)
        breathe(0.72, "Getting the details right...")
        preview_frame = ttk.Frame(book)
        book.add(preview_frame, text="  Preview  ")
        book.tab(preview_frame, padding=(px(10), px(6)))
        parent_frame = preview_frame
        parent_frame.rowconfigure(1, weight=1)
        parent_frame.columnconfigure(0, weight=1)
        self.preview = PreviewPanel(self)
        self.preview.build(preview_frame)

        # ---- tab 1 (back-reference): drop a folder anywhere on the automation
        #      page and it becomes the documents folder
        self.auto_page = auto
        self._enable_automation_drop(auto)

    def _build_header(self, parent: ttk.Frame) -> None:
        """Keep the original wordmark readable and present across every workspace."""
        hero = ttk.Frame(parent, style="Hero.TFrame", padding=(px(22), px(14)))
        hero.pack(fill="x")
        hero.columnconfigure(1, weight=1)
        self.logo_image = None
        for logo_path in (PROJECT_ROOT / "brand" / "kattyvissionfull.png",
                          PROJECT_ROOT / "brand" / "kattyvision.png"):
            try:
                from PIL import Image, ImageTk

                with Image.open(logo_path) as source:
                    mark = source.convert("RGBA")
                    # Display the artwork, without its transparent export margins.
                    bounds = mark.getchannel("A").point(lambda a: 255 if a > 10 else 0).getbbox()
                    if bounds:
                        mark = mark.crop(bounds)
                    mark.thumbnail((px(280), px(48)), Image.Resampling.LANCZOS)
                    self.logo_image = ImageTk.PhotoImage(mark, master=self)
                break
            except (OSError, tk.TclError):
                continue
        ttk.Label(hero, image=self.logo_image, text=APP_NAME if self.logo_image is None else "",
                  style="HeroTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(hero, text=APP_TAGLINE, style="HeroSub.TLabel").grid(
            row=1, column=0, sticky="w", pady=(px(5), 0))
        ttk.Label(hero, text="YOUR PDF WORKSPACE", style="HeroSub.TLabel",
                  font=(FONT, CAPTION, "bold")).grid(row=0, column=1, sticky="e")
        self.hero_badge = tk.Frame(hero, background=OK_SOFT, padx=px(10), pady=px(4))
        self.hero_badge.grid(row=1, column=1, sticky="e")
        tk.Label(self.hero_badge, text="Local files. Just on your device.",
                 background=OK_SOFT, foreground=OK, font=(FONT, CAPTION)).pack()
        self.var_banner = tk.StringVar(value="")
        self.banner = tk.Frame(hero, background=WARN_SOFT, padx=px(10), pady=px(5))
        self.banner.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(px(8), 0))
        tk.Label(self.banner, textvariable=self.var_banner, background=WARN_SOFT,
                 foreground=WARN_TEXT, font=(FONT, CAPTION), wraplength=px(720)).pack(anchor="w")
        self.banner.grid_remove()

    def _show_banner(self, text: str, colour: str = WARN) -> None:
        """A visible, plain-English warning strip in the hero band."""
        self.var_banner.set(text)
        try:
            self.banner.configure(background=colour)
            for child in self.banner.winfo_children():
                child.configure(background=colour, foreground=contrast_foreground(colour))
            self.banner.grid()
        except Exception:
            pass

    def _hide_banner(self) -> None:
        try:
            self.banner.grid_remove()
        except Exception:
            pass

    def _build_step1(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="  01   Your documents  ", padding=12)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="Case name", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", pady=4, padx=(0, 10))
        ttk.Entry(card, textvariable=self.var_case).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(card, text="shown in every file name", style="CardMuted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(10, 0))

        ttk.Label(card, text="Documents folder", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=4, padx=(0, 10))
        ttk.Entry(card, textvariable=self.var_input).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(card, text="Browse folder", command=lambda: self._pick_dir(self.var_input)).grid(
            row=1, column=2, padx=(10, 0))

        ttk.Label(card, text="Save results in", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=4, padx=(0, 10))
        ttk.Entry(card, textvariable=self.var_output).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(card, text="Browse folder", command=lambda: self._pick_dir(self.var_output)).grid(
            row=2, column=2, padx=(10, 0))

        found = ttk.Frame(card, style="Card.TFrame")
        found.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        summary = ttk.Label(found, textvariable=self.var_found, style="CardMuted.TLabel",
                            wraplength=px(820), justify="left")
        summary.pack(fill="x", pady=(0, px(8)))
        summary.bind("<Configure>", lambda event: summary.configure(wraplength=max(1, event.width)))
        actions = ttk.Frame(found, style="Card.TFrame")
        actions.pack(fill="x")
        found = actions
        self.btn_recent = ttk.Button(found, text="Recent folders",
                                     command=self._show_recent)
        self.btn_recent.pack(side="right", padx=(10, 0))
        ttk.Button(found, text="Check the folder", command=self._check_documents).pack(side="right")
        ttk.Button(found, text="Try sample files", style="Link.TButton",
                   command=self._make_sample).pack(side="right", padx=(0, 10))
        self._recent_menu = tk.Menu(self, tearoff=0)

    def _recent_list(self) -> list[str]:
        return list(self.cfg.get("ui.recent_folders") or [])

    def _remember_folder(self, folder: str | Path) -> None:
        folder = str(folder)
        recent = [f for f in self._recent_list() if f != folder]
        recent.insert(0, folder)
        self.cfg.set("ui.recent_folders", recent[:6])
        try:
            self.cfg.save()
        except Exception:
            pass

    def _show_recent(self) -> None:
        """Pop a menu of the last used document folders - one click re-fills."""
        menu = self._recent_menu
        menu.delete(0, "end")
        recent = self._recent_list()
        if not recent:
            menu.add_command(label="(no recent folders yet)", state="disabled")
        for folder in recent:
            menu.add_command(label=folder,
                             command=lambda f=folder: self._use_recent(f))
        try:
            menu.tk_popup(self.btn_recent.winfo_rootx(),
                          self.btn_recent.winfo_rooty() + self.btn_recent.winfo_height())
        finally:
            menu.grab_release()

    def _use_recent(self, folder: str) -> None:
        self.var_input.set(folder)
        name = Path(folder).name
        if not self.var_case.get().strip():
            self.var_case.set(name)          # auto-fill the case name
        self._check_documents()

    def _build_step2(self, parent: ttk.Frame) -> None:
        """Step 2 - say what you want, with clicks only.

        Replaces the old stack of radio buttons plus two spin boxes.  Nothing
        here needs typing: the four bundles are cards, the portal limits are a
        row of court presets, and a plain-English "here is what will happen"
        line updates the moment anything is clicked.
        """
        card = ttk.LabelFrame(parent, text="  02   Make it yours  ",
                              padding=(14, 12))
        card.pack(fill="x", pady=(10, 0))
        card.columnconfigure(0, weight=1)

        # ---- 1. the four bundles, as clickable cards (2x2 so the page stays short)
        self.preset_group = ChoiceGroup(command=self._on_preset_card)
        self.preset_cards: list[ChoiceCard] = []
        picker = tk.Frame(card, bg=CARD)
        picker.grid(row=0, column=0, sticky="ew", pady=(0, px(4)))
        for column in (0, 1):
            picker.columnconfigure(column, weight=1, uniform="preset")
        for preset in PRESETS:
            index = len(self.preset_cards)
            box = ChoiceCard(picker, preset.title, preset.hint,
                             value=preset.key, icon=preset.icon,
                             badge=preset.badge, group=self.preset_group,
                             background=CARD, wrap=px(330))
            box.grid(row=index // 2, column=index % 2, sticky="ew",
                     padx=(0, px(6)) if index % 2 == 0 else (px(6), 0),
                     pady=px(3))
            self.preset_cards.append(box)
        self.preset_group.select(str(self.cfg.get("ui.preset", "standard")),
                                 notify=False)

        # ---- 2. the live plan sentence (explain before the user commits)
        self.plan_bar = tk.Frame(card, bg=ACCENT_SOFT)
        self.plan_bar.grid(row=1, column=0, sticky="ew", pady=(px(11), px(4)))
        self.plan_icon = Icon(self.plan_bar, "sparkle", 15, ACCENT,
                              background=ACCENT_SOFT)
        self.plan_icon.pack(side="left", padx=(px(12), px(9)), pady=px(10))
        self.var_plan = tk.StringVar(value="")
        self.plan_label = tk.Label(
            self.plan_bar, textvariable=self.var_plan, bg=ACCENT_SOFT,
            fg=ACCENT, font=(FONT, CAPTION), anchor="w", justify="left",
            wraplength=px(700))
        self.plan_label.pack(side="left", fill="x", expand=True, pady=px(10),
                             padx=(0, px(12)))

        # ---- 3. the court portal, chosen from a list instead of typed
        portals = tk.Frame(card, bg=CARD)
        portals.columnconfigure(0, weight=1)
        portals.grid(row=2, column=0, sticky="ew", pady=(px(12), 0))
        ttk.Label(portals, text="Your court portal's upload limit",
                  style="CardHead.TLabel").grid(row=0, column=0, sticky="w")
        self.var_portal_hint = tk.StringVar(value="")
        ttk.Label(portals, textvariable=self.var_portal_hint,
                  style="CardMuted.TLabel").grid(row=1, column=0, sticky="w",
                                                  pady=(2, px(9)))
        self.portal_group = ChoiceGroup(command=self._on_portal_card)
        self.portal_cards: dict[str, ChoiceCard] = {}
        grid = tk.Frame(portals, bg=CARD)
        grid.grid(row=2, column=0, sticky="ew")
        grid.columnconfigure((0, 1), weight=1, uniform="portal")
        for index, portal in enumerate(PORTALS):
            entry = ChoiceCard(
                grid, portal.label,
                f"{portal.max_pages} pages  ·  {portal.max_mb:g} MB",
                value=portal.key, icon="scale", group=self.portal_group,
                background=CARD, wrap=px(300))
            entry.grid(row=index // 2, column=index % 2, sticky="ew",
                       padx=(0 if index % 2 == 0 else px(6),
                             px(6) if index % 2 == 0 else 0), pady=px(3))
            self.portal_cards[portal.key] = entry
        self.portal_group.select(self._current_portal().key, notify=False)
        self.var_portal_hint.set(self._current_portal().hint)

        # ---- 4. the raw numbers, only when "my portal" is chosen
        self.limits_panel = Disclosure(
            card, "your portal's own limit",
            hint="Type your limit directly, or use the arrow keys to adjust it.")
        self.limits_panel.grid(row=4, column=0, sticky="ew", pady=(px(6), 0))
        self.limits_panel.set_open(self._current_portal().key == "custom")
        self._build_limit_steppers(self.limits_panel.body)

        # ---- 5. everything else, behind one honest button
        bottom = tk.Frame(card, bg=CARD)
        bottom.grid(row=3, column=0, sticky="ew", pady=(px(12), 0))
        self.btn_advanced = IconButton(
            bottom, "All other settings", self._open_advanced, icon="settings",
            background=CARD, surface=SURFACE)
        self.btn_advanced.pack(side="left")
        ttk.Label(bottom,
                  text="seal size, language, file names and more - only if you "
                       "need them",
                  style="CardMuted.TLabel").pack(side="left", padx=(px(10), 0))
        self._refresh_plan()

    def _build_limit_steppers(self, limits: tk.Frame) -> None:
        """Editable limits with optional arrow-key adjustments."""
        limits.columnconfigure(0, weight=1)
        pages_field = Field(limits, "Most pages in one upload",
                            hint="anything longer is split into several files",
                            factory=lambda field: self._make_pages_stepper(
                                field))
        mb_field = Field(limits, "Largest file size in MB",
                         hint="the app keeps shrinking until every file fits",
                         factory=lambda field: self._make_mb_stepper(field))
        self.limits_panel.add_row(pages_field)
        self.limits_panel.add_row(mb_field)
    def _make_pages_stepper(self, field: tk.Frame) -> Stepper:
        """Build the page-limit stepper inside its Field (see ui_kit.Field)."""
        self.step_pages = Stepper(
            field, minimum=1, maximum=5000,
            value=self._int_or(self.var_max_pages, 50), step=5, width=6,
            command=lambda _value: self._after_limit_change())
        return self.step_pages

    def _make_mb_stepper(self, field: tk.Frame) -> Stepper:
        """Build the size-limit stepper inside its Field."""
        self.step_mb = Stepper(
            field, minimum=0.5, maximum=500.0,
            value=self._float_or(self.var_max_mb, 4.5), step=0.5, decimals=1,
            width=6, command=lambda _value: self._after_limit_change())
        return self.step_mb

    # ------------------------------------------------------------- step 2 glue
    @staticmethod
    def _int_or(variable: tk.Variable, fallback: int) -> int:
        """Read a variable as an int, falling back instead of raising."""
        try:
            return int(float(str(variable.get()).strip()))
        except (tk.TclError, TypeError, ValueError):
            return fallback

    @staticmethod
    def _float_or(variable: tk.Variable, fallback: float) -> float:
        """Read a variable as a float, falling back instead of raising."""
        try:
            return float(str(variable.get()).strip())
        except (tk.TclError, TypeError, ValueError):
            return fallback

    def _current_portal(self) -> Portal:
        """The portal matching the stored limits, else the custom entry."""
        pages = self._int_or(self.var_max_pages, 50)
        megabytes = self._float_or(self.var_max_mb, 4.5)
        for portal in PORTALS:
            if (portal.key != "custom" and portal.max_pages == pages
                    and abs(portal.max_mb - megabytes) < 0.01):
                return portal
        return next(p for p in PORTALS if p.key == "custom")

    def _on_portal_card(self, key: str) -> None:
        """The user picked a court portal - fill the limits in for them."""
        portal = next((p for p in PORTALS if p.key == key), None)
        if portal is None:
            return
        self.var_portal_hint.set(portal.hint)
        self.limits_panel.set_open(portal.key == "custom")
        if portal.key != "custom":
            self.step_pages.set(portal.max_pages)
            self.step_mb.set(portal.max_mb)
        self._sync_limits_to_config()
        self.cfg.set("ui.portal", portal.key)
        self._refresh_plan()

    def _after_limit_change(self) -> None:
        """A stepper moved: mirror it and re-describe the plan."""
        self._sync_limits_to_config()
        self._refresh_plan()

    def _sync_limits_to_config(self) -> None:
        """Push the stepper values into the config (not written to disk yet).

        The MB box is the *compression* target only.  It deliberately does not
        write ``split.max_size_mb``: that is a separate, opt-in rule, and having
        one portal number quietly re-arm it is what used to chop a 40-page part
        into pieces again.
        """
        self.var_max_pages.set(str(int(self.step_pages.get())))
        self.var_max_mb.set(str(self.step_mb.get()))
        self.cfg.set("split.max_pages_per_file", int(self.step_pages.get()))
        self.cfg.set("compress.target_size_mb", float(self.step_mb.get()))

    def _on_preset_card(self, key: str) -> None:
        """The user clicked a bundle card instead of a radio button."""
        self.var_preset.set(key)
        self._on_preset()
        self._refresh_plan()

    def _refresh_plan(self) -> None:
        """Rebuild the "here is what will happen" sentence.

        This is the most valuable line on the screen: it removes all doubt
        about what the chosen options do, in the user's own words rather than
        in engine terminology.
        """
        preset = self._current_preset()
        if preset.key == "custom":
            steps = []
            for key, label in (("ocr.enabled", "make the scans searchable"),
                               ("cover_page.enabled", "add a contents page"),
                               ("compress.enabled", "shrink the file"),
                               ("pagination.enabled", "number the pages"),
                               ("stamp.enabled", "put on the bank seal"),
                               ("split.enabled", "split it if it is too big")):
                if self.cfg.get(key, True):
                    steps.append(label)
            plan = ("You have switched on: " + _join(steps) + "." if steps
                    else "You have switched everything off - nothing changes.")
        else:
            plan = preset.plan
        if self.cfg.get("split.enabled", True):
            portal = self._current_portal()
            limit = (f"Each file will be under {portal.max_mb:g} MB and "
                     f"{portal.max_pages} pages.")
        else:
            limit = "Splitting is off, so one single file is produced."
        self.var_plan.set(f"{plan}  {limit}")


    def _build_step3(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="  03   Ready when you are  ", padding=12)
        card.pack(fill="both", expand=True, pady=(10, 0))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        buttons = ttk.Frame(card, style="Card.TFrame")
        buttons.grid(row=0, column=0, sticky="ew")
        self.btn_run = ttk.Button(buttons, text="Run workflow  →", style="Run.TButton",
                                  command=self._start)
        self.btn_run.pack(side="left")
        self.btn_cancel = ttk.Button(buttons, text="Cancel", state="disabled",
                                     command=self._cancel)
        self.btn_cancel.pack(side="left", padx=(10, 0))
        self.var_elapsed = tk.StringVar(value="")
        ttk.Label(buttons, textvariable=self.var_elapsed,
                  style="CardMuted.TLabel").pack(side="left", padx=(14, 0))
        ttk.Button(buttons, text="Open results folder",
                   command=self._open_output).pack(side="right")
        ttk.Button(buttons, text="Help", command=self._show_help).pack(
            side="right", padx=(0, 10))

        self.progress = ttk.Progressbar(card, mode="determinate", maximum=len(STEP_ROWS))
        self.progress.grid(row=1, column=0, sticky="ew", pady=(10, 10))

        listing = ttk.Frame(card, style="Card.TFrame")
        listing.grid(row=2, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        for index, (key, label) in enumerate(STEP_ROWS):
            row = ttk.Frame(listing, style="Card.TFrame")
            row.grid(row=index, column=0, sticky="ew", pady=1)
            row.columnconfigure(3, weight=1)
            # A drawn glyph, not a "[ ]"/"[OK]" character: it renders the same
            # on every machine and the row keeps its width while it changes.
            status_icon = Icon(row, "minus", 15, MUTED, background=CARD)
            status_icon.grid(row=0, column=0, sticky="w", padx=(px(2), px(8)))
            # The state name is kept as text for screen readers and for anyone
            # who cannot rely on the colour.
            status = ttk.Label(row, text="not started", style="StepWait.TLabel",
                               width=10, anchor="w")
            status.grid(row=0, column=1, sticky="w")
            ttk.Label(row, text=label, style="StepName.TLabel").grid(
                row=0, column=2, sticky="w", padx=(2, px(12)))
            note = ttk.Label(row, text="", style="StepNote.TLabel")
            note.grid(row=0, column=3, sticky="w")
            self.step_rows[key] = (status, note, status_icon)

        detail_bar = tk.Frame(card, bg=CARD)
        detail_bar.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        # A Switch, not a check box: the ttk indicator renders inconsistently
        # across themes and a switch also states what turning it on does.
        self.details_switch = Switch(
            detail_bar, "Show the technical log", value=False,
            hint="Only needed for support - it lists every step and message",
            variable=self.var_details, command=self._toggle_details,
            background=CARD)
        self.details_switch.pack(anchor="w")

        self.log_box = ScrolledText(card, height=9, font=("Consolas", 9),
                                    background="#12161c", foreground="#c9d4e2",
                                    insertbackground="#c9d4e2", state="disabled",
                                    relief="flat", borderwidth=0)
        self.log_visible = False

        # -------- the result card (hidden until a run succeeds)
        result = ttk.LabelFrame(card, text="  What you got  ", padding=12)
        result.grid(row=4, column=0, sticky="ew", pady=(px(10), 0))
        result.columnconfigure(0, weight=1)
        self.result_card = result
        header = tk.Frame(result, bg=CARD)
        header.grid(row=0, column=0, sticky="ew")
        self.result_status = tk.Label(header, text="Ready to use",
                                     bg=OK_SOFT, fg=OK, font=(FONT, CAPTION, "bold"),
                                     padx=px(10), pady=px(6), anchor="w")
        self.result_status.pack(side="left")
        self.result_summary = tk.Label(header, text="Your filing is prepared and waiting.",
                                      bg=CARD, fg=TEXT, font=(FONT, BODY),
                                      anchor="w")
        self.result_summary.pack(side="left", padx=(px(10), 0))
        # A chip per fact, so the outcome reads at a glance instead of being
        # buried in a sentence.  Colour is never the only signal - each chip
        # carries its own words, so nothing depends on seeing colour alone.
        self.result_chips = ChipRow(result, background=CARD)
        self.result_chips.grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        self.result_hint = tk.Label(result, text="Best next move: review the pages, then upload the PDF.",
                                   bg=SURFACE, fg=ACCENT, font=(FONT, CAPTION, "bold"),
                                   anchor="w", padx=px(10), pady=px(6))
        self.result_hint.grid(row=2, column=0, sticky="ew", pady=(px(8), 0))
        # Every finished file as a clickable row.  Clicking one opens it in the
        # Preview tab, so "what did I actually produce?" is always one click
        # away instead of a trip to the folder.
        self.result_files = tk.Frame(result, bg=CARD)
        self.result_files.grid(row=3, column=0, sticky="ew", pady=(px(8), 0))
        result_buttons = tk.Frame(result, bg=CARD)
        result_buttons.grid(row=4, column=0, sticky="w", pady=(px(8), 0))
        self.btn_open_pdf = IconButton(result_buttons, "Open the PDF",
                                       self._open_first_pdf, icon="download",
                                       primary=True, background=CARD)
        self.btn_open_pdf.pack(side="left")
        self.btn_open_pdf.set_enabled(False)
        for text, icon, action in (
                ("Preview pages", "eye", self._preview_result),
                ("Open folder", "folder", self._open_output),
                ("Copy log", "copy", self._copy_log),
                ("Copy path", "link", self._copy_output_path)):
            button = IconButton(result_buttons, text, action, icon=icon,
                                background=CARD, surface=SURFACE)
            button.pack(side="left", padx=px(8))
        # 'use this result in ...' - the same hand-off the tool pages offer:
        # the finished filing goes to the next step with one click.
        self.result_next = tk.Frame(result, bg=CARD)
        self.result_next.grid(row=5, column=0, sticky="w", pady=(px(8), 0))
        result.grid_remove()                      # hidden until the first success

    def _show_result_files(self, outputs: Sequence[Path]) -> None:
        """List each finished file as a clickable row (opens in Preview)."""
        for child in self.result_files.winfo_children():
            child.destroy()
        for path in outputs:
            row = tk.Frame(self.result_files, bg=SURFACE, cursor="hand2",
                           takefocus=1, highlightthickness=px(2),
                           highlightbackground=BORDER_STRONG,
                           highlightcolor=FOCUS)
            row.pack(fill="x", pady=px(2))
            try:
                size_mb = path.stat().st_size / MB
            except OSError:
                size_mb = 0.0
            Icon(row, "document", 15, ACCENT, background=SURFACE).pack(
                side="left", padx=(px(10), px(8)), pady=px(7))
            tk.Label(row, text=path.name, bg=SURFACE, fg=TEXT,
                     font=(FONT, BODY), anchor="w").pack(side="left")
            tk.Label(row, text=f"{size_mb:.2f} MB", bg=SURFACE, fg=MUTED,
                     font=(FONT, CAPTION), anchor="e").pack(side="right",
                                                             padx=px(10))
            tk.Label(row, text="click to see the pages  >", bg=SURFACE,
                     fg=ACCENT, font=(FONT, CAPTION)).pack(
                side="right", padx=(0, px(10)))
            for widget in (row, *row.winfo_children()):
                widget.bind("<Button-1>",
                            lambda _e, p=path: self._show_in_preview(p))
            row.bind("<Return>", lambda _e, p=path: self._show_in_preview(p))

    def _show_in_preview(self, path: Path) -> None:
        """Open one file on the Preview tab, whatever produced it."""
        if not path.is_file():
            messagebox.showinfo("File not found", f"{path} is no longer there.")
            return
        self.book.select(3)                   # the Check the result tab
        self.preview.open_path(path)

    def _show_outputs_in_preview(self, outputs: Sequence[Path]) -> None:
        """Open the first finished file on the Preview tab (tool pages)."""
        first = next((p for p in outputs if p.suffix.lower() == ".pdf"), None)
        if first is not None:
            self._show_in_preview(first)

    def _build_footer(self, parent: ttk.Frame) -> None:
        from . import __version__

        bar = ttk.Frame(parent, padding=(px(22), px(10)))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.var_status, style="Status.TLabel",
                  wraplength=px(850)).pack(anchor="w", pady=(0, px(6)))
        signature = ttk.Frame(bar)
        signature.pack(fill="x")
        bar = signature
        ttk.Label(bar, text=f"Made with care by {APP_SIGNATURE}",
                  style="Signature.TLabel").pack(side="left")
        ttk.Label(bar, text=f"A little more time for you.   ·   v{__version__}",
                  style="Tagline.TLabel").pack(side="right")

    # ------------------------------------------------------------------- tabs
    def _build_tools(self, parent: ttk.Frame) -> None:
        """One sub-page per single task.

        There used to be an "Activity" log pinned across the bottom of this
        tab.  It was noise: nobody reads a wall of console output, and it cost
        ~180 px of height that the seal preview and the RUN button needed
        more.  The technical log behind "Show technical details" on the first
        tab still records everything, and anything the user genuinely has to
        act on is raised as a warning dialog at the end of a run.
        """
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.tool_book = ttk.Notebook(parent)
        self.tool_book.grid(row=0, column=0, sticky="nsew")
        self.tool_pages = {}
        for spec in TOOL_SPECS:
            self.tool_pages[spec.key] = ToolPage(self, spec)
            # Six pages is the single biggest chunk of the start-up build, so
            # let the loading card draw a frame between them (no-op once the
            # splash has been closed).
            splash = getattr(self, "_splash", None)
            if splash is not None:
                splash.pump()

    def _build_settings(self, parent: ttk.Frame) -> None:
        """The Settings tab: a short, honest explanation of what else exists."""
        parent.columnconfigure(0, weight=1)
        card = ttk.LabelFrame(parent, text="  Settings  ", padding=14)
        card.pack(fill="x", padx=px(14), pady=px(14))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="A few thoughtful defaults to get you started. "
                             "Adjust the details here, and we'll remember them for next time.",
                  style="Card.TLabel", wraplength=px(820),
                  justify="left").grid(row=0, column=0, sticky="w",
                                        pady=(0, px(10)))
        buttons = tk.Frame(card, bg=CARD)
        buttons.grid(row=1, column=0, sticky="w")
        IconButton(buttons, "All other settings", self._open_advanced,
                   icon="settings", background=CARD,
                   surface=SURFACE).pack(side="left")
        IconButton(buttons, "Open the settings file",
                   lambda: open_in_explorer(self.cfg.source), icon="document",
                   background=CARD, surface=SURFACE).pack(side="left",
                                                          padx=px(10))
        self.ghostscript_note = ttk.Label(card, text="", style="CardMuted.TLabel",
                                         wraplength=px(800), justify="left")
        self.ghostscript_note.grid(row=2, column=0, sticky="ew", pady=(px(12), 0))
        self.ghostscript_note.bind("<Configure>", lambda event:
                                  self.ghostscript_note.configure(wraplength=max(1, event.width)))

    # ------------------------------------------------------------ details box
    def _toggle_details(self, *_args) -> None:
        """Show or hide the technical log.  Accepts the Switch's bool argument.

        The log lives inside the card, which is laid out with ``grid``, so it
        has to be shown and hidden with ``grid`` too - packing it in here
        raised "cannot use geometry manager" and the switch did nothing at all.
        """
        show = bool(self.var_details.get())
        if show and not self.log_visible:
            self.log_box.grid(row=5, column=0, sticky="ew", pady=(px(8), 0))
            self.log_visible = True
        elif not show and self.log_visible:
            self.log_box.grid_remove()
            self.log_visible = False
        self._fit_to_screen(880 if self.log_visible else 780)

    def log_line(self, text: str = "") -> None:
        for box in (self.log_box, getattr(self, "tool_log", None)):
            if box is None:
                continue
            box.configure(state="normal")
            box.insert("end", str(text) + "\n")
            # keep the log readable during long OCR runs: drop the oldest lines
            if float(box.index("end-1c").split(".")[0]) > 500:
                box.delete("1.0", "50.0")
            box.see("end")
            box.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        self.var_status.set(text)
        self.update_idletasks()

    def celebrate(self) -> None:
        """A friendly 'finished!' moment: chime + flashing green badge."""
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass
        self._flash_badge(6)

    def _flash_badge(self, remaining: int) -> None:
        if not getattr(self, "hero_badge", None):
            return
        colours = (OK, ACCENT_HOVER)
        colour = colours[remaining % 2]
        try:
            self.hero_badge.configure(background=colour)
            for child in self.hero_badge.winfo_children():
                child.configure(background=colour, foreground=contrast_foreground(colour))
            self.hero_badge.winfo_toplevel().title(APP_NAME)
        except Exception:
            pass
        if remaining > 0:
            self.after(280, lambda: self._flash_badge(remaining - 1))
        else:
            try:
                self.hero_badge.configure(background=OK)
                for child in self.hero_badge.winfo_children():
                    child.configure(background=OK)
            except Exception:
                pass

    # --------------------------------------------------------- step statuses
    def _mark_all_steps(self, state: str, note: str) -> None:
        for key in self.step_rows:
            self._mark_step(key, state, note)

    def _mark_step(self, key: str, state: str, note: str = "") -> None:
        """state: waiting | working | done | failed | skipped."""
        row = self.step_rows.get(key)
        if not row:
            return
        status_label, note_label, status_icon = row
        # Each state carries a drawn glyph, a word, and a colour - three
        # independent signals, so nothing depends on seeing colour alone.
        icons = {
            "waiting": ("minus", "waiting", "StepWait.TLabel", MUTED),
            "working": ("bolt", "working", "StepWork.TLabel", WARN_TEXT),
            "done": ("check", "done", "StepDone.TLabel", OK),
            "failed": ("cross", "failed", "StepFail.TLabel", ERR),
            "skipped": ("minus", "skipped", "StepWait.TLabel", MUTED),
        }
        glyph, word, style_name, colour = icons.get(state, icons["waiting"])
        status_label.configure(text=word, style=style_name)
        status_icon.set_name(glyph)
        status_icon.set_color(colour)
        note_label.configure(text=note)
        self.step_state[key] = state
        done = sum(1 for value in self.step_state.values()
                   if value in ("done", "failed", "skipped"))
        self.progress.configure(value=done)

    # ------------------------------------------------------- advanced dialog
    def _open_advanced(self) -> None:
        AdvancedDialog(self, self.cfg, on_save=self._after_advanced)

    def _after_advanced(self) -> None:
        """Re-read values that also appear on the main window."""
        self.var_seal.set(str(self.cfg.resolve("paths.stamp_image")))
        self._on_preset(apply=False, persist=False)
        self.var_max_pages.set(str(self.cfg.get("split.max_pages_per_file", 50)))
        self.var_max_mb.set(str(self.cfg.get("compress.target_size_mb", 4.5)))
        self._set_status("Advanced settings saved.")
        self.log_line("Advanced settings saved.")

    # ------------------------------------------------------------ config glue
    def _load_from_config(self) -> None:
        self._on_preset(apply=False, persist=False)
        input_name = Path(self.var_input.get()).name
        self.var_found.set(f"Folder: {input_name or '(not chosen)'}  -  press "
                           f"'Check the folder' to see what is inside.")
        self.log_line(f"Starting up with option: {self._current_preset().title}")
        self._set_status("Ready. Choose the documents folder, then press RUN.")

    def _check_tools_async(self, wait_seconds: float = 0.0) -> None:
        """Find out whether OCR is available **without freezing the window**.

        ``diagnose()`` starts ``tesseract --version`` and ``gs --version`` as
        subprocesses.  That is ~0.2 s when everything is warm, but it can be
        several seconds on a cold, network-installed or virus-scanned machine -
        and it used to run on the UI thread during start-up, which is part of
        why the app looked frozen.  The work now runs on a thread and the result
        arrives through the normal event queue, so the window stays responsive
        no matter how long it takes.

        ``wait_seconds`` lets the caller hold the loading card open for a moment
        so the common (fast) case has everything ready before the window is
        shown.  It never waits longer than that: an unfinished check simply
        reports a few hundred milliseconds later.
        """
        if self.tool_worker is not None and self.tool_worker.is_alive():
            return

        def job() -> None:
            try:
                tools = diagnose(self.cfg)
            except Exception as exc:                   # pragma: no cover
                self.events.put(("log", "WARN", f"could not check the OCR engine: {exc}"))
                tools = None
            self.events.put(("tools", tools))

        self.tool_worker = threading.Thread(target=job, name="toolcheck",
                                            daemon=True)
        self.tool_worker.start()

        if wait_seconds <= 0:
            return
        deadline = time.perf_counter() + wait_seconds
        while self.tool_worker.is_alive() and time.perf_counter() < deadline:
            self._splash.pump()
            time.sleep(0.01)
        # If the check finished while we were waiting, apply it now: the window
        # is about to be shown and the OCR warning banner has to be right on the
        # first frame the user sees, not a moment later.
        self._process_events()

    def _report_tools(self, tools: dict | None = None) -> None:
        """Say plainly whether OCR is available.

        ``tools`` is the result of :func:`court_ocr.toolcheck.diagnose` when it
        was collected on a worker thread.  Called with no argument it does the
        check inline, which keeps the method usable on its own (the UI tests and
        the screenshot tools call it that way).
        """
        if tools is None:
            tools = diagnose(self.cfg)
        if not tools:
            # The background check itself failed (not "OCR is missing" - we
            # simply could not tell).  Say so honestly instead of claiming the
            # engine is absent and switching the user to "merge only".
            self.log_line("Could not check the OCR engine just now - "
                          "if scans do not become searchable, run SETUP.ps1.")
            return
        tesseract = tools["tesseract"]
        self.ghostscript_ready = tools["ghostscript"].available
        if tesseract.available:
            self.log_line(f"OCR engine ready: {tesseract.version}")
            self._hide_banner()
        else:
            self.var_preset.set("quick")
            self._on_preset(apply=True, persist=False)
            self.log_line("OCR engine (Tesseract) is not installed - switched to "
                          "'Quick merge only'. Run SETUP.ps1 once to enable OCR.")
            self._set_status("OCR unavailable - run SETUP.ps1 once (free), or continue with "
                             "Quick merge.")
            self._show_banner("OCR is not installed - scans will NOT be made "
                              "searchable. Run SETUP.ps1 once (free) to fix this.")
        if not self.ghostscript_ready:
            self.log_line("Ghostscript not installed - compression uses the built-in engine "
                          "(optional, see SETUP.ps1).")
        self.gs_path = tools["ghostscript"].path
        if getattr(self, "ghostscript_note", None) is not None:
            if self.ghostscript_ready:
                self.ghostscript_note.configure(
                    text="Ghostscript is available - the strongest compression level works "
                         "at its best.")
            else:
                self.ghostscript_note.configure(
                    text="Ghostscript is not installed - the strongest compression level "
                         "is weaker. Install it with SETUP.ps1 -WithGhostscript (optional).")

    def _current_preset(self) -> Preset:
        key = self.var_preset.get()
        for preset in PRESETS:
            if preset.key == key:
                return preset
        return PRESETS[0]

    def _on_preset(self, apply: bool = True, persist: bool = True) -> None:
        """The user picked an option bundle (or we are only reading it back)."""
        preset = self._current_preset()
        if apply and preset.settings:
            self.cfg.update(preset.settings)
            self.log_line(f"Option selected: {preset.title}")
        self.cfg.set("ui.preset", preset.key)
        if persist:
            self._apply_ui_to_config(persist=True)

    def _apply_ui_to_config(self, persist: bool = False) -> None:
        """Push everything the user can see into the Config, optionally saving it."""
        cfg = self.cfg
        cfg.set("ui.preset", self.var_preset.get())
        cfg.set("case.name", self.var_case.get().strip())
        cfg.set("paths.input", self.var_input.get().strip())
        cfg.set("paths.output", self.var_output.get().strip())
        for key, text, cast in (
            ("split.max_pages_per_file", self.var_max_pages, int),
            ("compress.target_size_mb", self.var_max_mb, float),
        ):
            try:
                cfg.set(key, cast(str(text.get()).strip()))
            except (TypeError, ValueError):
                pass
        cfg.set("output.open_output_folder", False)     # the app opens it itself
        cfg.set("runtime.verbose", True)
        if persist:
            try:
                cfg.save()
            except Exception as exc:
                self.log_line(f"(settings could not be saved: {exc})")

    # ------------------------------------------------------------- browsing
    def _pick_dir(self, variable: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(initialdir=variable.get() or str(PROJECT_ROOT))
        if chosen:
            variable.set(str(Path(chosen)))
            if variable is self.var_input:
                self._remember_folder(chosen)
                if not self.var_case.get().strip():
                    self.var_case.set(Path(chosen).name)   # auto-fill the case name
                self._check_documents()

    def _check_documents(self) -> None:
        """Look inside the folder and say, in plain English, what was found."""
        folder = Path(self.var_input.get().strip())
        if not folder.is_dir():
            messagebox.showerror("Folder not found",
                                 f"This folder does not exist:\n{folder}")
            return
        from . import pdf_ops

        self._set_status("Checking the folder...")
        files = pdf_ops.collect_inputs(folder,
                                       self.cfg.get("merge.file_extensions") or [".pdf"])
        if not files:
            self.var_found.set(f"Folder: {folder.name}  -  nothing to process yet")
            messagebox.showinfo(
                "Nothing to process yet",
                f"No PDF or image documents were found in:\n{folder}\n\n"
                f"Put the case documents inside it (sub-folders are fine) and check again.")
            return

        pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
        images = len(files) - len(pdfs)
        pages = scans = 0
        problems: list[str] = []
        for path in pdfs:
            ok, why = pdf_ops.is_readable_pdf(path)
            if not ok:
                problems.append(f"{path.name}  -  {why}")
                continue
            info = pdf_ops.inspect(path)
            pages += info.pages
            scans += info.scanned_pages

        summary = (f"Folder: {folder.name}  -  {len(files)} documents "
                   f"({len(pdfs)} PDF, {images} image), about {pages + images} pages")
        if scans:
            summary += f", {scans} page(s) look like scans and will be OCRed"
        self.var_found.set(summary)
        self.log_line(summary)
        for problem in problems:
            self.log_line(f"  ! {problem}")
        if problems:
            messagebox.showwarning("Some files cannot be used",
                                   "These will be skipped:\n\n" + "\n".join(problems))
        else:
            self._set_status("Looks good - press RUN.")

    # ------------------------------------------------------------------- run
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self._apply_ui_to_config(persist=True)
        # Tk variables may only be read on the main thread - capture them here
        case_name = self.var_case.get().strip()
        input_dir = Path(self.var_input.get().strip())
        if not input_dir.is_dir():
            messagebox.showerror("Documents folder not found",
                                 f"This folder does not exist:\n{input_dir}\n\n"
                                 f"Use 'Change...' to pick the folder that holds the case "
                                 f"documents.")
            return
        from . import pdf_ops

        files = pdf_ops.collect_inputs(input_dir,
                                       self.cfg.get("merge.file_extensions") or [".pdf"])
        if not files:
            messagebox.showerror("No documents found",
                                 f"No PDF or image files were found in:\n{input_dir}\n\n"
                                 f"Put the case documents there and press RUN again.")
            return
        if not case_name:
            case_name = input_dir.name       # the folder name IS the case name
            self.var_case.set(case_name)
        self._remember_folder(input_dir)

        self._mark_all_steps("waiting", "not started")
        self.progress.configure(value=0)
        self.btn_run.state(["disabled"])
        self.btn_cancel.state(["!disabled"])
        self.cancel_flag.clear()
        self.run_started_at = datetime.now()
        self._tick_run()
        self._set_status(f"Working on {len(files)} document(s) - you can Cancel at any time.")
        self.log_line("")
        self.log_line("=" * 72)
        self.log_line(f"RUN STARTED - {len(files)} document(s)")
        self.log_line("=" * 72)
        self.worker = threading.Thread(target=self._run_job, args=(input_dir, case_name),
                                       daemon=True)
        self.worker.start()

    def _run_job(self, input_dir: Path, case_name: str) -> None:
        """Background thread: run the pipeline, forward every log line to the UI.

        Only plain data crosses the thread boundary - never Tk objects.
        """
        logger = None
        handler = None
        try:
            reset_logger()
            logger = get_logger(self.cfg.resolve("paths.logs"), verbose=True)
            handler = GuiLogHandler(
                lambda level, message: self.events.put(("log", level, message)))
            handler.setLevel(logging.INFO)
            logger.addHandler(handler)

            def progress(message: str) -> None:
                if self.cancel_flag.is_set():
                    raise JobCancelled()
                self.events.put(("progress", message))

            pipeline = Pipeline(self.cfg, progress=progress)
            report = pipeline.run(case_name=case_name, input_dir=input_dir)
            self.events.put(("done", report))
        except JobCancelled:
            self.events.put(("cancelled",))
        except Exception as exc:                  # noqa: BLE001 - reported in the window
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            if logger is not None and handler is not None:
                try:
                    logger.removeHandler(handler)
                except Exception:
                    pass

    def _drain_events(self) -> None:
        """Move worker-thread messages into the widgets (Tk is not thread safe)."""
        self._process_events()
        self.after(120, self._drain_events)

    def _process_events(self) -> None:
        """Apply everything the worker threads have queued up, right now.

        Split out of :meth:`_drain_events` so start-up can call it directly:
        after the (bounded) wait for the tool check, the window is about to be
        shown and the OCR banner has to already be correct.
        """
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    _, level, message = event
                    self.log_line(f"{level:<7} {message}")
                    self._step_from_log(message)
                elif kind == "progress":
                    self.log_line(f"        {event[1]}")
                elif kind == "tools":
                    # Result of the background _check_tools_async() - applied
                    # here because Tk widgets may only be touched from the main
                    # thread.
                    self._report_tools(event[1])
                elif kind == "done":
                    self._finish(event[1])
                elif kind == "error":
                    self._failed(event[1])
                elif kind == "cancelled":
                    self._cancelled()
                elif kind == "tool_done":
                    _, key, outputs, warnings = event
                    self.tool_pages[key]._finish(outputs, warnings)
                elif kind == "tool_error":
                    _, key, message = event
                    self.tool_pages[key]._fail(message)
                elif kind == "tool_cancelled":
                    _, key = event
                    self.tool_pages[key]._cancelled()
        except queue.Empty:
            pass

    def _step_from_log(self, message: str) -> None:
        """Translate the pipeline's step banners into the friendly status list."""
        text = str(message).strip()
        if text[:3] not in (">>>", "<<<", "!!!"):
            return
        body = text[3:].strip()
        if text.startswith("<<<"):
            head, _, tail = body.partition(" done in ")
            key = STEP_ALIASES.get(head.split(" ")[0].lower())
            if key:
                seconds = tail.rstrip("s").strip()
                self._mark_step(key, "done",
                                f"finished in {seconds}s" if seconds else "finished")
        elif text.startswith("!!!"):
            key = STEP_ALIASES.get(body.split(" ")[0].lower())
            if key:
                self._mark_step(key, "failed", "did not finish - see details")
                self._set_status("A step failed - see the details box.")
        else:
            key = STEP_ALIASES.get(body.split(" ")[0].lower())
            if key:
                self._mark_step(key, "working", "working...")
                label = body.rstrip(" .").split("  ")[0]
                self._set_status(f"Working: {label}")

    # --------------------------------------------------------------- results
    def _tick_run(self) -> None:
        """Show a running 'elapsed' counter while a job is alive."""
        if not getattr(self, "run_started_at", None):
            return
        if self.worker and self.worker.is_alive():
            seconds = (datetime.now() - self.run_started_at).total_seconds()
            self.var_elapsed.set(f"{seconds:.0f}s elapsed")
            self.after(500, self._tick_run)

    def _run_done_ui(self) -> None:
        """Common end-of-run cleanup (success, failure or cancel)."""
        self.run_started_at = None
        self.var_elapsed.set("")
        self.btn_cancel.state(["disabled"])
        self.cancel_flag.clear()

    def _cancel(self) -> None:
        if self.worker and self.worker.is_alive():
            self.cancel_flag.set()
            self._set_status("Cancelling - waiting for the current page/file to finish...")

    def celebrate(self) -> None:
        """A friendly 'finished!' moment: chime + badge flash + taskbar flash."""
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass
        self._flash_badge(6)
        try:
            flash_window(self)
        except Exception:
            pass

    def _cancelled(self) -> None:
        for key, state in self.step_state.items():
            if state == "working":
                self._mark_step(key, "skipped", "cancelled here")
        self._run_done_ui()
        self.btn_run.state(["!disabled"])
        self.log_line("")
        self.log_line("CANCELLED by user.")
        self._set_status("Cancelled - nothing was harmed, your files are untouched.")

    def _preview_result(self) -> None:
        """Send the finished filing to the Preview tab."""
        if self.report and self.report.outputs:
            first = next((p for p in self.report.outputs if p.suffix.lower() == ".pdf"),
                         None)
            if first:
                self._show_in_preview(first)
                return
        messagebox.showinfo("Nothing to preview yet", "RUN first, then come back.")

    def _suggest_next_step(self) -> None:
        """Offer the likely next move after a successful run."""
        if self.report and self.report.outputs:
            first = next((p for p in self.report.outputs if p.suffix.lower() == ".pdf"), None)
            if first:
                self._show_in_preview(first)
                self._set_status("Ready for the next pass - review pages, OCR, numbers or seal if needed.")
                return
        self.book.select(1)
        self._set_status("Pick the next job from the PDF Tools tab.")

    def _open_first_pdf(self) -> None:
        import os

        if self.report and self.report.outputs:
            first = next((p for p in self.report.outputs if p.suffix.lower() == ".pdf"),
                         self.report.outputs[0])
            try:
                os.startfile(str(first))               # noqa: S606 - Windows shell
            except Exception as exc:
                messagebox.showerror("Could not open", str(exc))

    def _copy_log(self) -> None:
        text = self.log_box.get("1.0", "end").strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Log copied - paste it into an email or chat if you need help.")

    def _copy_output_path(self) -> None:
        """Copy the output folder path to the clipboard for quick sharing."""
        target = self.last_output or Path(self.var_output.get().strip())
        if not target:
            self._set_status("No output folder is available yet.")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(str(target))
            self._set_status("Output path copied - you can paste it into the portal or chat.")
        except Exception:
            self._set_status("The output path could not be copied to the clipboard.")

    def _finish(self, report) -> None:
        self.report = report
        for key, state in self.step_state.items():
            if state == "waiting":
                self._mark_step(key, "skipped", "not used for this option")
        self.btn_run.state(["!disabled"])
        self.log_line("-" * 72)
        for warning in report.warnings:
            self.log_line(f"WARNING: {warning}")
        self.log_line(f"FINISHED in {report.elapsed:.1f} seconds -> "
                      f"{len(report.outputs)} file(s)")
        for output in report.outputs:
            self.log_line(f"   {output.name}   {output.stat().st_size / MB:.2f} MB")
        self._set_status(f"Done in {report.elapsed:.1f} seconds - "
                         f"{len(report.outputs)} file(s) ready.")
        self.celebrate()

        if not report.outputs:
            messagebox.showwarning("Nothing was produced",
                                   "No file came out. Tick 'Show technical details', "
                                   "take a screenshot of the log and ask for support.")
            return
        # The Activity panel is gone, so a warning the user can do something
        # about is raised instead of being written where nobody will read it.
        if report.warnings:
            shown = report.warnings[:6]
            more = (f"\n...and {len(report.warnings) - 6} more."
                    if len(report.warnings) > 6 else "")
            messagebox.showwarning(
                "Finished, with things worth knowing",
                "\n".join(f"• {w}" for w in shown) + more)
        self._run_done_ui()
        self.last_output = report.outputs[0].parent

        # ------- the result card: one glance at everything that matters.
        # Each fact becomes its own chip, worded so it is readable on its own -
        # no "✔"/"⚠" prefixes, because a symbol means nothing to everyone.
        total_mb = sum(f.stat().st_size for f in report.outputs) / MB
        has_pdf = any(f.suffix.lower() == ".pdf" for f in report.outputs)
        pages = _count_pages(report.outputs)
        facts: list[tuple[str, str]] = [
            (f"{len(report.outputs)} file"
             f"{'s' if len(report.outputs) != 1 else ''}", "info"),
            (f"{total_mb:.2f} MB", "muted"),
        ]
        if pages:
            facts.append((f"{pages} page{'s' if pages != 1 else ''}", "muted"))
        if report.elapsed:
            facts.append((f"took {report.elapsed:.0f}s", "muted"))

        target = float(self.cfg.get("compress.target_size_mb", 0) or 0)
        biggest = max((f.stat().st_size for f in report.outputs), default=0) / MB
        if target and biggest <= target:
            facts.append((f"fits the portal limit ({target:g} MB)", "ok"))
        elif target:
            facts.append((f"over the {target:g} MB portal limit - see log", "warn"))
        if self.cfg.get("ocr.enabled", True):
            facts.append(("searchable text", "ok"))
        if self.cfg.get("stamp.enabled", True):
            facts.append(("seal added", "ok"))
        failed = [r.step for r in report.steps if not r.ok]
        if failed:
            facts.append((f"{len(failed)} step(s) need a look", "warn"))
        if report.warnings:
            facts.append((f"{len(report.warnings)} thing(s) to know", "warn"))
        self.result_chips.show(facts)
        self.result_summary.config(text=f"{len(report.outputs)} ready file(s) in {self.last_output.name or self.var_output.get()}")
        if report.warnings:
            self.result_status.config(text="Review required")
            self.result_status.config(bg=WARN_SOFT, fg=WARN_TEXT)
            self.result_hint.config(text="A few details need your attention before final upload.")
        else:
            self.result_status.config(text="Ready to use")
            self.result_status.config(bg=OK_SOFT, fg=OK)
            self.result_hint.config(text="Best next move: preview the pages, then upload the PDF.")
        self.btn_open_pdf.set_enabled(has_pdf)
        self._show_result_files(report.outputs)
        self.build_handoff_row(self.result_next, report.outputs)
        self.result_card.grid()
        self._fit_to_screen(880)
        open_in_explorer(self.last_output)

    def _failed(self, message: str) -> None:
        for key, state in self.step_state.items():
            if state == "working":
                self._mark_step(key, "failed", "stopped here")
        self._run_done_ui()
        self.btn_run.state(["!disabled"])
        self.log_line("")
        self.log_line(f"FAILED: {message}")
        self._set_status("Something went wrong - see the details.")
        messagebox.showerror(
            "Could not finish",
            f"{message}\n\nTick 'Show technical details' for the full log. The log file is "
            f"also saved in the 'logs' folder.")

    # ----------------------------------------------------------- small actions
    def _make_sample(self) -> None:
        import subprocess
        import sys

        script = PROJECT_ROOT / "tools" / "make_sample_pdf.py"
        if not script.is_file():
            messagebox.showerror("Missing file", f"Not found:\n{script}")
            return
        self.log_line("")
        self.log_line("Creating practice documents...")
        self._set_status("Creating practice documents...")
        try:
            proc = subprocess.run([sys.executable, str(script)], cwd=str(PROJECT_ROOT),
                                  capture_output=True, text=True, timeout=300,
                                  creationflags=0x08000000)
            for line in (proc.stdout or "").splitlines():
                self.log_line(f"  {line}")
            if proc.returncode != 0:
                self.log_line(f"  {proc.stderr}")
                messagebox.showerror("Could not create them", (proc.stderr or "")[:600])
                return
            self.var_input.set(str(PROJECT_ROOT / "input" / "SAMPLE CASE"))
            self.var_case.set("SAMPLE CASE")
            self._check_documents()
            self._set_status("Practice documents ready - press RUN to try it.")
        except Exception as exc:
            self.log_line(f"  failed: {exc}")
            messagebox.showerror("Could not create them", str(exc))

    def _show_help(self) -> None:
        messagebox.showinfo(
            "How to use it",
            "TAB 1 - DO THE WHOLE CASE  (this is the one you want)\n"
            "   1. Click 'Change...' and pick the folder holding the case\n"
            "      documents.  Sub-folders are fine, and files are ordered\n"
            "      naturally, so 'Exhibit 2' comes before 'Exhibit 10'.\n"
            "   2. Click the card that matches what you are doing.\n"
            "      'Ready to file' suits almost every case.  The blue bar\n"
            "      underneath spells out exactly what will happen.\n"
            "   3. Click your court portal so the app knows its upload\n"
            "      limits.  Nothing else needs typing.\n"
            "   4. Press RUN and wait.\n\n"
            "TAB 2 - ONE JOB AT A TIME\n"
            "   Need only one thing - OCR, or compress, or page numbers?\n"
            "   Open that page, add the files, press RUN.  The result is\n"
            "   saved next to your files, or wherever you choose.\n\n"
            "TAB 4 - CHECK THE RESULT\n"
            "   Look at the pages before you upload.  Everything stays on\n"
            "   this PC; nothing is sent anywhere.\n\n"
            "WHAT YOU GET\n"
            "   . one searchable PDF (the scans become real text)\n"
            "   . made small enough for the portal, text still sharp\n"
            "   . 'Page X of Y' on every page\n"
            "   . the bank seal on the pages you chose\n"
            "   . split into parts if the case is too big for one upload\n"
            "   . a CSV report listing every step, size and timing\n\n"
            "NEED SOMETHING SPECIFIC?\n"
            "   'All other settings' opens every remaining option, with a\n"
            "   search box, grouped by topic.  Your choices are remembered.")
        self._set_status("Ready.")

    def _open_output(self) -> None:
        target = self.last_output or Path(self.var_output.get().strip())
        if target and Path(target).exists():
            open_in_explorer(target)
        else:
            messagebox.showinfo("Nothing yet",
                                "Results are saved here after the first run:\n"
                                f"{self.var_output.get()}")

    def _on_close(self) -> None:
        """Remember the settings, window size and last tab, then close."""
        try:
            self._apply_ui_to_config(persist=True)
            # Only remember a size the user actually chose.  While the window is
            # zoomed (maximised) `geometry()` reports the full-screen rectangle,
            # and saving that made the app reopen oversized and pushed off the
            # edge of the screen on every later launch.
            if self.state() == "normal":
                self.cfg.set("ui.geometry", self.geometry())
            try:
                self.cfg.set("ui.tab", int(self.book.index(self.book.select())))
            except Exception:
                pass
            self.cfg.save()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


# ==========================================================================
#  Advanced settings window - every knob, in five tidy tabs
# ==========================================================================
#   (kind, label, config key, options, hint, icon)
#
#  Rewritten so the wording is outcome-first ("Sharp and clear", not "400 dpi")
#  and every row carries a one-line reason.  `hint` is what the user reads
#  under the control, so nobody has to guess what a setting will do.
ADVANCED_SPEC: tuple[tuple[str, tuple], ...] = (
    ("Scans & text", (
        ("bool", "Make the scans searchable", "ocr.enabled",
         None, "Turns pictures of text into real, selectable text", "search"),
        ("choice", "Which pages to read", "ocr.mode", (
            ("skip_text", "Pages without text"),
            ("force", "Every page"),
            ("off", "None"),
        ), "'Pages without text' is faster and cannot spoil good pages", "document"),
        ("int", "How sharp the scan is", "ocr.oversample_dpi", (150, 400),
         "300 suits court papers; 400 if the print is small or faint", "sun"),
        ("int", "How many pages at once", "ocr.jobs", (0, 64),
         "0 uses every processor. Lower it if the PC feels slow", "bolt"),
        ("text", "Language of the documents", "ocr.language", None,
         "eng = English. Use eng+mar for English and Marathi", "info"),
    )),
    ("Size & quality", (
        ("bool", "Make the file smaller", "compress.enabled",
         None, "Shrinks scanned pages without making them look bad", "package"),
        ("choice", "How much detail to keep", "quality.preset", (
            ("small", "Smallest"),
            ("balanced", "Balanced"),
            ("high", "Sharp"),
            ("archival", "Highest"),
        ), "Balanced suits most filings; scanned text is never softened", "sliders"),
        ("bool", "Keep scans pure black and white", "quality.bitonal",
         None, "Turns grey scans into sharp 1-bit text - much smaller, and "
               "text loses nothing", "document"),
        ("bool", "Remove private information", "quality.scrub_metadata", None,
         "Clears author, scanner and camera details before you upload", "lock"),
        ("float", "Shrink harder if still too big", "compress.target_size_mb",
         None, "The app tries harder until each file is under this size",
         "scale"),
        # The next three were in config.json and are still read by
        # pipeline.py, but the rebuilt settings screen had dropped them
        # entirely.  Restored here in outcome language: engine is a safety
        # valve, level only matters if engine is set to "levels", and the
        # number format is the one thing a portal can reject.
        ("choice", "Which shrinking method", "compress.engine", (
            ("adaptive", "The automatic one (recommended)"),
            ("levels", "The older one - only if a filing comes out wrong"),
        ), "Automatic tries several qualities and keeps the best. Switch to "
           "the older one only if you are told to", "sliders"),
        ("int", "Strength, if using the older method", "compress.level",
         (0, 4), "0 = try each strength in turn, 1-4 = fix one strength. "
                 "Ignored by the automatic method", "sliders"),
        ("text", "What the page number says", "pagination.format", None,
         "The words around the number. {page} is the number and {total} is "
         "how many pages there are", "hash"),
    )),
    ("Page numbers", (
        ("bool", "Add page numbers", "pagination.enabled",
         None, "Prints 'Page 1 of 20' on every page", "hash"),
        ("choice", "Where the numbers go", "pagination.position", (
            ("bottom-center", "Bottom centre"), ("bottom-right", "Bottom right"),
            ("bottom-left", "Bottom left"), ("top-right", "Top right"),
            ("top-center", "Top centre"), ("top-left", "Top left"),
        ), None, "bottom-right"),
        ("int", "Size of the numbers", "pagination.font_size", (6, 24),
         "9 is a good size for A4 paper", "sun"),
        # PyMuPDF's built-in PDF fonts.  These names are what the paginate
        # step passes to page.insert_text(), so the preview renders with the
        # exact same face the finished filing will carry.
        ("choice", "Font style", "pagination.font", (
            ("helv", "Helvetica"), ("hebo", "Helvetica Bold"),
            ("tiro", "Times Roman"), ("tibo", "Times Bold"),
            ("cour", "Courier"),
        ), "Times Roman looks closest to a typed pleading", "text"),
        ("choice", "Number style", "pagination.style", (
            ("arabic", "1, 2, 3"), ("roman", "i, ii, iii"),
            ("ROMAN", "I, II, III"), ("alpha", "A, B, C"),
        ), None, "hash"),
        ("int", "Start numbering from", "pagination.start_number", (1, 1000),
         "The first printed number. Set 12 and the pages read 12, 13, 14... "
         "when this file continues an already-numbered bundle", "hash"),
        ("int", "Leave the first pages blank", "pagination.skip_first_pages",
         (0, 20), "Useful when page 1 is a cover that should stay clean",
         "minus"),
        # pagination.format is set on the "Size & quality" tab next to the
        # other compression controls, so it is not repeated here.
        ("bool", "Add a contents page", "cover_page.enabled",
         None, "Lists every document and the page it starts on", "book"),
        ("text", "Heading on the contents page", "cover_page.subtitle",
         None, "For example: Index of Documents", "document"),
        ("text", "Court name", "case.court", None,
         "Printed on the contents page", "info"),
        ("text", "Bank name", "case.bank_name", None,
         "Printed on the contents page", "info"),
    )),
    ("Bank seal", (
        ("bool", "Put on the bank seal", "stamp.enabled",
         None, "Stamps your bank's seal onto the pages", "tag"),
        ("file", "Seal image", "paths.stamp_image", None,
         "A transparent PNG looks cleanest", "image"),
        ("choice", "Which pages get the seal", "stamp.pages", (
            ("all", "Every page"), ("first", "First only"),
            ("last", "Last only"), ("1,3,5-9", "Only some"),
        ), "'Only some' then lists the pages, e.g. 1,3,5-9", "document"),
        ("choice", "Where the seal sits", "stamp.position", (
            ("bottom-right", "Bottom right"), ("bottom-left", "Bottom left"),
            ("bottom-center", "Bottom centre"), ("top-right", "Top right"),
            ("top-left", "Top left"), ("center", "Centre"),
        ), None, "bottom-right"),
        ("int", "Seal size (% of page width)", "stamp.width_percent", (5, 60),
         "Around 20 looks right on A4", "scale"),
        ("float", "How strong the seal looks", "stamp.opacity", None,
         "0.9 is solid, 0.4 is a faint watermark", "sparkle"),
        ("bool", "Remove the paper behind a scanned seal", "stamp.remove_white",
         None, "Helpful when the seal was scanned on paper", "image"),
    )),
    ("Splitting & names", (
        ("bool", "Split so every file fits", "split.enabled",
         None, "Breaks a long filing into upload-sized parts", "cut"),
        ("choice", "What decides a split", "split.mode", (
            ("pages", "Pages only (recommended)"),
            ("both", "Pages and file size"),
            ("size", "File size only"),
        ), "'Pages only' gives exactly the number of parts you asked for", "cut"),
        ("int", "Most pages in one upload", "split.max_pages_per_file",
         (1, 5000), "Set by your court portal on the main window", "hash"),
        ("float", "File size limit for splitting (0 = ignore)", "split.max_size_mb",
         None, "Leave 0 unless your portal rejects a part on size alone. "
               "Compression already chases the MB limit on the main window.",
         "package"),
        ("bool", "Never cut a document in half",
         "split.keep_bookmark_boundaries", None,
         "Keeps each exhibit whole, even if the file gets longer", "book"),
        ("text", "Name for the split files", "split.name_template", None,
         "{case} and {index} are filled in for you", "document"),
        ("text", "Name for a single file", "output.final_name_template", None,
         "{case} is filled in for you", "document"),
        ("bool", "Keep the working files", "output.keep_intermediate_files",
         None, "Only useful if you want to inspect the steps", "folder"),
        ("bool", "Write a spreadsheet report", "output.write_report", None,
         "A CSV listing every step and size", "document"),
    )),
)


class AdvancedDialog(tk.Toplevel):
    """Every setting, grouped into five tabs and built from ADVANCED_SPEC.

    Two ideas make this usable rather than merely complete:

    * **Search** - with roughly forty settings, typing a word ("seal", "black
      and white") beats scrolling five tabs looking for it.
    * **Click-only controls** - booleans are switches, short choice lists are
      segmented button rows and numbers are -/+ steppers, so the dialog no
      longer asks the user to type a setting they could simply pick.
    """

    def __init__(self, master: tk.Misc, cfg: Config, on_save=None) -> None:
        super().__init__(master)
        self.cfg = cfg
        self.on_save = on_save
        self.rows: list[dict] = []
        self.book: ttk.Notebook | None = None
        self._search_origin: int | None = None
        self._match_tabs: set[int] = set()
        self.title("All other settings")
        self.configure(background=BG)
        self.transient(master)
        self.resizable(True, True)
        load_icon(self)
        self._build()
        self._centre()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _centre(self) -> None:
        """Size the dialog to a comfortable slice of the screen and centre it.

        It is wide on purpose: the label, its explanation and the control sit
        in three columns, and at 880 px the explanation wrapped onto two lines
        and the segmented controls ran out of room, clipping their own labels.
        """
        width, height = px(1080), px(660)
        try:
            screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
            width = min(width, screen_w - 60)
            height = min(height, screen_h - 120)
            self.minsize(px(760), px(400))
            self.geometry(f"{width}x{height}+{max(0, (screen_w - width) // 2)}"
                          f"+{max(0, (screen_h - height) // 3)}")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        wrap = ttk.Frame(self, padding=14)
        wrap.pack(fill="both", expand=True)
        self._build_search(wrap)

        book = ttk.Notebook(wrap)
        book.pack(fill="both", expand=True)
        self.book = book
        for tab_index, (tab_name, rows) in enumerate(ADVANCED_SPEC):
            page = tk.Frame(book, bg=CARD, padx=px(14), pady=px(12))
            book.add(page, text=tab_name)
            page.columnconfigure(0, weight=1)
            page.tab_name = tab_name          # read back by the search filter
            page.tab_index = tab_index
            row_offset = 0
            if tab_name == "Page numbers":
                self.page_number_preview = PageNumberPreview(page)
                self.page_number_preview.grid(row=0, column=0, sticky="w",
                                              pady=(0, px(10)))
                row_offset = 1
            for index, spec in enumerate(rows):
                self._add_row(page, index + row_offset, spec)
        self._connect_page_number_preview()

        buttons = tk.Frame(wrap, bg=BG)
        buttons.pack(fill="x", pady=(px(14), 0))
        IconButton(buttons, "Save these settings", self._save, icon="check",
                   primary=True, background=BG, surface=ACCENT).pack(side="right")
        IconButton(buttons, "Cancel", self.destroy, background=BG,
                   surface=SURFACE).pack(side="right", padx=px(10))
        IconButton(buttons, "Undo all my changes", self._restore, icon="cross",
                   background=BG, surface=SURFACE).pack(side="left")

    def _build_search(self, parent: ttk.Frame) -> None:
        """A search box: with ~40 settings, finding beats scrolling."""
        ttk.Label(parent, style="Tagline.TLabel",
                  text="Everything here is optional - the main window already "
                       "uses sensible defaults. Your choices are remembered "
                       "for next time.").pack(anchor="w", pady=(0, px(8)))
        bar = tk.Frame(parent, bg=CARD, highlightthickness=px(2),
                       highlightbackground=BORDER_STRONG, highlightcolor=FOCUS)
        bar.pack(fill="x", ipady=px(2))
        Icon(bar, "search", 15, MUTED, background=CARD).pack(
            side="left", padx=(px(10), px(8)))
        self.var_search = tk.StringVar(value="")
        self.var_search.trace_add("write", lambda *_a: self._apply_search())
        self.search_entry = tk.Entry(bar, textvariable=self.var_search,
                                     font=(FONT, BODY), bg=CARD, fg=TEXT,
                                     relief="flat", borderwidth=0,
                                     highlightthickness=0)
        self.search_entry.pack(side="left", fill="both", expand=True, ipady=px(6))
        self.search_hint = tk.Label(bar, text="Search all 34 settings", bg=CARD,
                                    fg=MUTED, font=(FONT, CAPTION))
        self.search_hint.pack(side="left", padx=(0, px(4)))
        self.search_count = tk.Label(bar, text="", bg=CARD, fg=ACCENT,
                                     font=(FONT, CAPTION, "bold"))
        self.search_count.pack(side="right", padx=(px(6), px(10)))
        self.search_entry.bind("<FocusIn>", lambda _e: self._hide_hint())
        self.search_entry.bind("<Escape>", lambda _e: self._clear_search())

    def _clear_search(self) -> None:
        """Empty the box and show every setting again."""
        self.var_search.set("")
        self.search_entry.focus_set()
        self._show_hint()
        self._apply_search()

    def _hide_hint(self) -> None:
        """Drop the placeholder as soon as the box is used."""
        try:
            self.search_hint.pack_forget()
        except tk.TclError:
            pass

    def _show_hint(self) -> None:
        """Bring the placeholder back when the box is empty and idle."""
        if self.var_search.get():
            return
        try:
            self.search_hint.pack(side="left", padx=(0, px(4)))
        except tk.TclError:
            pass

    def _apply_search(self) -> None:
        """Show only the settings that match, and jump to the first match.

        Filtering alone was not enough: the matches could all live in a tab
        that was not on screen, so the user would be told "3 found" while
        staring at an empty tab.  Now the tab holding the first match is opened
        automatically, the count says whether the matches are on one tab or
        spread over several, and the tab the user started on is remembered so
        clearing the box puts them back where they were.
        """
        needle = self.var_search.get().strip().lower()
        if needle:
            self._hide_hint()
        else:
            self._show_hint()
        shown = 0
        first_tab: int | None = None
        match_tabs: set[int] = set()
        for row in self.rows:
            match = (not needle) or needle in row["haystack"]
            if match:
                row["control"].grid()
                shown += 1
                if needle:
                    match_tabs.add(row["tab_index"])
                    if first_tab is None:
                        first_tab = row["tab_index"]
            else:
                row["control"].grid_remove()
        if needle and first_tab is not None:
            if self._search_origin is None:
                self._search_origin = self.book.index("current")
            # Selected by index, not by tab name: ttk::notebook's "tabs"
            # command returns pane *paths*, so a name lookup never matches and
            # the dialog used to sit on an empty tab while counting matches.
            self.book.select(first_tab)
        elif not needle and self._search_origin is not None:
            if 0 <= self._search_origin < len(self.book.tabs()):
                self.book.select(self._search_origin)     # back where we began
            self._search_origin = None
        self._match_tabs = match_tabs
        if not needle:
            self.search_count.configure(text="")
        elif shown:
            names = {self.book.tab(i, "text") for i in match_tabs}
            where = names.pop() if len(names) == 1 else None
            self.search_count.configure(
                text=f"{shown} found" + (f"  ·  on the '{where}' tab" if where
                                         else "  ·  across several tabs"))
        else:
            self.search_count.configure(text="nothing matches - try another word")

    def _add_row(self, parent: tk.Frame, index: int, spec: tuple) -> None:
        """Build one labelled row, using a click-only control where possible.

        Booleans become switches, short choice lists become segmented button
        rows and numbers become -/+ steppers, so almost nothing here needs
        typing.  Text and file fields stay as entries - that is the one case
        where typing is genuinely the right interaction.
        """
        kind, label, key = spec[0], spec[1], spec[2]
        options = spec[3] if len(spec) > 3 else None
        hint = spec[4] if len(spec) > 4 else None
        current = self.cfg.get(key)
        reverse: dict = {}

        # Every row is a ui_kit.Field, and each control is built *inside* it via
        # the factory form so Tk parents it to the right widget.
        if kind == "bool":
            # A Switch already carries its own label and reason, so it is used
            # directly - wrapping it in a Field would print the label twice.
            var: tk.Variable = tk.BooleanVar(value=bool(current))
            control: tk.Widget = Switch(parent, label, value=bool(current),
                                        hint=hint or "", variable=var,
                                        background=CARD)
        elif kind == "int":
            low, high = options or (0, 9999)
            var = tk.IntVar(value=int(current or 0))
            step = 1 if high - low <= 30 else 10
            control = Field(
                parent, label, hint=hint or "", background=CARD,
                factory=lambda field: Stepper(
                    field, minimum=low, maximum=high, value=int(current or 0),
                    step=step, width=8, command=var.set))
        elif kind == "float":
            var = tk.DoubleVar(value=float(current or 0))
            control = Field(
                parent, label, hint=hint or "", background=CARD,
                factory=lambda field: Stepper(
                    field, minimum=0.0, maximum=1000.0,
                    value=float(current or 0), step=0.1, decimals=1, width=8,
                    command=var.set))
        elif kind == "choice":
            labels = [text for _value, text in options]
            reverse = {text: str(value) for value, text in options}
            forward = {str(value): text for value, text in options}
            if len(options) <= 4:
                # A short list is a row of buttons, not a drop-down.  The
                # variable is bound so saving reads whatever is selected.
                var = tk.StringVar(value=str(current))
                control = Field(
                    parent, label, hint=hint or "", background=CARD,
                    factory=lambda field: Segmented(
                        field, options, variable=var,
                        fill=len(options) <= 4))
                control.control.set(forward.get(str(current), labels[0]))
            else:
                # A long list stays a drop-down.  It is bound to ``var`` via
                # textvariable so the picked label reaches _value_of(); before
                # this the combo box was never linked and a changed selection
                # was silently thrown away on Save.
                var = tk.StringVar(value=forward.get(str(current), labels[0]))
                control = Field(
                    parent, label, hint=hint or "", background=CARD,
                    factory=lambda field: ttk.Combobox(
                        field, state="readonly", width=34, values=labels,
                        textvariable=var))
        elif kind == "file":
            var = tk.StringVar(value=str(current or ""))
            control = Field(
                parent, label, hint=hint or "", background=CARD,
                factory=lambda field, v=var: self._make_file_picker(field, v))
        else:
            var = tk.StringVar(value=str(current or ""))
            control = Field(
                parent, label, hint=hint or "", background=CARD,
                factory=lambda field: ttk.Entry(
                    field, textvariable=var, width=38))

        control.grid(row=index, column=0, sticky="ew", pady=px(4))
        self.rows.append({
            "kind": kind, "key": key, "var": var, "reverse": reverse,
            "control": control,
            # Which notebook tab this row lives on, so a search can open the
            # tab that actually holds the matches.
            "tab_index": getattr(parent, "tab_index", 0),
            "tab": getattr(parent, "tab_name", ""),
            "haystack": f"{label} {hint or ''} {key}".lower(),
        })

    def _connect_page_number_preview(self) -> None:
        """Keep the preview synced even when a setting lives on another tab."""
        preview_keys = {
            "pagination.enabled": "enabled",
            "pagination.format": "format",
            "pagination.position": "position",
            "pagination.font_size": "font_size",
            "pagination.skip_first_pages": "skip_first",
            "pagination.start_number": "start_number",
            "pagination.font": "font",
            "pagination.style": "style",
        }
        for row in self.rows:
            preview_key = preview_keys.get(row["key"])
            if not preview_key:
                continue
            variable = row["var"]
            variable.trace_add(
                "write",
                lambda *_a, k=preview_key, v=variable:
                self.page_number_preview.set_values(**{k: v.get()}))
            self.page_number_preview.set_values(**{preview_key: variable.get()})

    def _make_file_picker(self, field: tk.Frame,
                          var: tk.StringVar) -> tk.Frame:
        """An entry plus a Choose button, built inside its Field."""
        holder = tk.Frame(field, bg=CARD)
        ttk.Entry(holder, textvariable=var, width=38).pack(side="left")
        ttk.Button(holder, text="Choose...",
                   command=lambda: self._pick(var)).pack(
            side="left", padx=px(8))
        return holder


    # ---------------------------------------------------------------- actions
    @staticmethod
    def _pick(variable: tk.StringVar) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose the bank seal image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tif"), ("All files", "*.*")])
        if chosen:
            variable.set(chosen)

    def _value_of(self, kind: str, var: tk.Variable, reverse: dict):
        """Convert a widget value back into a config value."""
        if kind == "bool":
            return bool(var.get())
        if kind == "int":
            return int(var.get())
        if kind == "float":
            return round(float(var.get()), 3)
        if kind == "choice":
            return reverse.get(str(var.get()), var.get())
        return str(var.get()).strip()

    def _save(self) -> None:
        """Write every row back into the config and persist it."""
        focused = self.focus_get()
        if focused is not None and isinstance(focused.master, Stepper):
            focused.master._commit()
        for row in self.rows:
            try:
                value = self._value_of(row["kind"], row["var"], row["reverse"])
            except (tk.TclError, ValueError, TypeError):
                continue
            key = row["key"]
            if key == "paths.stamp_image":
                self.cfg.set("paths.stamp_image", value)
                self.cfg.set("stamp.image", value)
            else:
                self.cfg.set(key, value)
        try:
            self.cfg.save()
        except Exception as exc:
            messagebox.showwarning("Could not save settings", str(exc), parent=self)
        if self.on_save:
            self.on_save()
        self.destroy()

    def _restore(self) -> None:
        """Put every visible setting back to the value the app ships with."""
        from .config import DEFAULTS

        if not messagebox.askyesno(
                "Undo all my changes",
                "Put every setting in this window back to the default?",
                parent=self):
            return
        for row in self.rows:
            top, _, leaf = row["key"].partition(".")
            default = (DEFAULTS.get(top) or {}).get(leaf)
            if default is None:
                continue
            var, kind, reverse = row["var"], row["kind"], row["reverse"]
            try:
                if kind == "choice":
                    forward = {text: value for text, value in reverse.items()}
                    var.set(forward.get(str(default), str(default)))
                elif kind == "bool":
                    var.set(bool(default))
                elif kind in ("int", "float"):
                    var.set(float(default))
                else:
                    var.set(str(default))
            except (tk.TclError, ValueError, TypeError):
                continue


# ==========================================================================
#  Entry point
# ==========================================================================
def main(argv: list[str] | None = None) -> int:
    """Start the desktop app (optionally with --config <path>)."""
    global UI_SCALE
    UI_SCALE = enable_dpi_awareness()     # must happen before Tk creates a window
    # Also before any window exists: Windows fixes a process's taskbar identity
    # at the moment its FIRST window is created, and looks the name and icon up
    # through that identity.  App.__init__ used to call this after the window
    # was already up, which is too late - the taskbar button had been grouped
    # under pythonw.exe, showing the Python icon instead of the brand mark and
    # the name of whichever shortcut happened to launch it.
    set_taskbar_identity()
    if not enforce_single_instance():
        return 0                          # another copy is running; we focused it
    config_path: str | None = None
    args = list(argv if argv is not None else [])
    for index, item in enumerate(args):
        if item in ("--config", "-c") and index + 1 < len(args):
            config_path = args[index + 1]
    app = App(config_path)
    app.mainloop()
    return 0


def _report_startup_failure() -> Path | None:
    """Write a crash report to logs\\startup_error.log and show it to the user.

    The icon launches ``pythonw.exe``, so there is no console window: without
    this a start-up error looks exactly like "the icon does nothing".
    """
    details = traceback.format_exc()
    logs = PROJECT_ROOT / "logs"
    target: Path | None = None
    try:
        logs.mkdir(parents=True, exist_ok=True)
        target = logs / "startup_error.log"
        target.write_text(
            f"{APP_NAME} could not start\n"
            f"{datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"python  : {sys.executable}\n"
            f"folder  : {PROJECT_ROOT}\n\n{details}\n",
            encoding="utf-8",
        )
    except OSError:
        target = None

    message = f"{APP_NAME} could not open."
    if target is not None:
        message += f"\n\nThe technical details were saved to:\n{target}"
    else:
        message += "\n\n" + details.strip().splitlines()[-1]
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(f"{APP_NAME} - problem at start-up", message)
        root.destroy()
    except Exception:               # no Tk at all - the log file is enough
        pass
    return target


def _run() -> int:
    """Run :func:`main`, turning a crash into a log file plus an error box."""
    try:
        return main()
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception:
        _report_startup_failure()
        return 1


if __name__ == "__main__":          # required so parallel OCR works on Windows
    raise SystemExit(_run())
