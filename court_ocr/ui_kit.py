"""Design-system widgets for KattyVision.

Why this module exists
----------------------
The main window used to make the user *type numbers* (DPI spin boxes, "MB per
upload") and read engine jargon ("bilevel", "compression effort").  Research on
Windows progressive-disclosure, Primer's segmented-control guidance and court
form-design work all point at the same three fixes:

* **Pick, don't type** - a choice the app can make for you should be a button.
* **Outcome language** - label controls by what the user gets, not by the
  algorithm underneath ("Sharp and clear", not "400 dpi").
* **Safe defaults, one primary action** - never more than one bold button on
  screen, and the default must be the one a court clerk would pick.

Everything here follows the rules the rest of the app already uses: plain
English strings, graceful degradation, and no new third-party dependency.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable, Sequence

# --------------------------------------------------------------------------- #
#  Design tokens
# --------------------------------------------------------------------------- #
# Navy text, blue actions and pale surfaces echo the original wordmark.
# WARN is a background colour; WARN_TEXT carries readable warning copy.
BG = "#f3f6fb"
CARD = "#ffffff"
#: Unselected cards and buttons use this.  It was #f7f9fc - almost white - so
#: cards blended into the white panel behind them and the screen read as one
#: flat sheet.  A visible step down from CARD makes every card pop as a thing
#: you can click.
SURFACE = "#f5f8fc"
BORDER = "#dce4ef"
BORDER_STRONG = "#a9bbd1"
TEXT = "#12233d"
MUTED = "#5d6b82"
ACCENT = "#075cba"
ACCENT_HOVER = "#064990"
#: The fill behind a chosen card.  Kept soft so the ACCENT title still has strong
#: contrast against it, but deep enough to be obvious at a glance.
ACCENT_SOFT = "#e8f2ff"
OK = "#1a7f45"
OK_SOFT = "#e3f3ea"
WARN = "#a86a00"
WARN_TEXT = "#8a5600"
WARN_SOFT = "#fdf1dd"
ERR = "#b3261e"
ERR_SOFT = "#fdeae8"
FOCUS = "#2f6fdd"

FONT = "Segoe UI"

# --------------------------------------------------------------------------- #
#  Page thumbnails
# --------------------------------------------------------------------------- #
#  Rendering a page is the slowest thing this app does, so:
#    * thumbnails are tiny (about 74x98 px) - a few milliseconds each
#    * they are cached on disk, keyed by the file's size and mtime, so a
#      second visit to the same filing is instant
#    * the caller renders a few per frame (see PageMap._pump) so the window
#      stays responsive no matter how many pages there are


def _thumb_cache_key(path: Path, page: int, width: int, height: int) -> str:
    """A cache name that changes whenever the source file changes."""
    try:
        stat = path.stat()
        stamp = f"{int(stat.st_mtime)}_{int(stat.st_size)}"
    except OSError:
        stamp = "missing"
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_"
                   for ch in path.name)[:48]
    return f"{safe}-{stamp}-p{page}-{width}x{height}.png"


def render_thumbnail(path: str | Path, page: int, width: int, height: int,
                     cache_dir: Path) -> "tk.PhotoImage | None":
    """Return a small PhotoImage of one page, or ``None`` if it cannot render.

    Never raises.  A missing thumbnail must not stop the page map from working,
    because the printed page number is the part that really matters.
    """
    path = Path(path)
    try:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / _thumb_cache_key(path, page, width, height)
        if cached.is_file():
            return _load_photo(cached)
    except OSError:
        cached = None

    try:
        import pymupdf

        with pymupdf.open(path) as document:
            if not 0 <= page < document.page_count:
                return None
            # Zoom so the *long* edge lands on the tile size; A4 and A5 then
            # look alike instead of one being a tall sliver.
            rect = document[page].rect
            zoom = (width / rect.width) if rect.width >= rect.height \
                else (height / rect.height)
            pixmap = document[page].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                                alpha=False)
            raw = bytes(pixmap.samples)
            size = (pixmap.width, pixmap.height)
    except Exception:                       # unreadable / encrypted / not a PDF
        return None

    photo = None
    try:
        from PIL import Image, ImageTk

        temp = Image.frombytes("RGB", size, raw)
        temp.thumbnail((max(1, width), max(1, height)), Image.LANCZOS)
        photo = ImageTk.PhotoImage(temp)
    except Exception:                       # PIL missing - fall back to raw
        photo = _photo_from_samples(size, raw)
    if photo is None:
        return None
    if cached is not None:
        try:                                # cache for next time
            from PIL import Image

            Image.frombytes("RGB", size, raw).save(cached, format="PNG")
        except Exception:
            pass
    return photo


def _load_photo(path: Path) -> "tk.PhotoImage | None":
    """Read a cached PNG back into a PhotoImage (PIL when available)."""
    try:
        from PIL import Image, ImageTk

        with Image.open(path) as image:
            return ImageTk.PhotoImage(image.convert("RGB"))
    except Exception:
        pass
    try:
        return tk.PhotoImage(file=str(path))
    except tk.TclError:
        return None

    """Read a cached PNG back into a PhotoImage (PIL when available)."""
    try:
        from PIL import Image, ImageTk

        with Image.open(path) as image:
            return ImageTk.PhotoImage(image.convert("RGB"))
    except Exception:
        pass
    try:
        return tk.PhotoImage(file=str(path))
    except tk.TclError:
        return None


def render_page_image(path: str | Path, page: int, width_px: int,
                      cache_dir: Path) -> tuple["tk.PhotoImage | None",
                                              tuple[int, int]]:
    """Render one PDF page at ``width_px``; return it with its pixel size.

    The expensive half (rasterising the page) is cached on disk, so moving the
    seal around re-draws only the small seal image and not the whole page.
    """
    path = Path(path)
    width_px = max(40, int(width_px))
    try:
        import pymupdf
        from PIL import Image

        with pymupdf.open(path) as document:
            if not 0 <= page < document.page_count:
                return None, (0, 0)
            page_obj = document[page]
            rect = page_obj.rect
            zoom = width_px / max(rect.width, 1)
            pixmap = page_obj.get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            base = Image.frombytes("RGB", (pixmap.width, pixmap.height),
                                   bytes(pixmap.samples))
            from PIL import ImageTk

            return ImageTk.PhotoImage(base), base.size
    except Exception:                       # unreadable / encrypted / not a PDF
        return None, (0, 0)


def _photo_from_samples(size: tuple[int, int],
                        raw: bytes) -> "tk.PhotoImage | None":
    """Build a PhotoImage straight from RGB bytes (no PIL involved)."""
    try:
        photo = tk.PhotoImage(width=size[0], height=size[1])
        photo.put("".join(f"#{r:02x}{g:02x}{b:02x}"
                          for r, g, b in zip(raw[0::3], raw[1::3], raw[2::3])))
        return photo
    except (tk.TclError, ValueError):
        return None

# --------------------------------------------------------------------------- #
#  Type scale
# --------------------------------------------------------------------------- #
#  The first pass used 8/9/10 pt almost everywhere, which on a 1080p laptop read
#  as a page of grey confetti.  This scale is larger and has real steps between
#  the levels, so the eye can tell a heading from a hint without straining.
#     DISPLAY  hero band / app name
#     TITLE    card and tab headings
#     HEADING  section heads inside a card
#     BODY     labels, buttons, values - the workhorse
#     CAPTION  hints and secondary facts, used sparingly
DISPLAY = 20
TITLE = 15
HEADING = 12
BODY = 11
CAPTION = 10

#: 1.0 = 100% display scaling.  :mod:`court_ocr.app` overwrites this once the
#: real display scale is known, so fixed pixel paddings grow with the monitor.
SCALE = 1.0

#: Minimum interactive height in logical pixels (WCAG touch-target guidance).
TOUCH_TARGET = 40


def px(value: float) -> int:
    """Scale a design-pixel measurement for the current display."""
    return max(1, int(round(value * SCALE)))


def set_scale(value: float) -> None:
    """Fix the display scale that :func:`px` multiplies by - call it once.

    :mod:`court_ocr.app` calls this from ``main()``, before any window is built,
    so every padding, gap and minimum size in the app grows with the monitor.

    This used to be documented as happening automatically and in fact never did:
    ``SCALE`` stayed at 1.0 on a 150 % display while Tk scaled every font by 1.5
    and the window itself was sized by 1.5, so text was big and the space around
    it was tiny.  That is what made the layout look cramped and "broken" on a
    scaled laptop screen.
    """
    global SCALE
    SCALE = max(1.0, min(float(value), 3.0))


def contrast_foreground(background: str) -> str:
    """Pick black or white text for ``background`` using the WCAG formula."""
    try:
        channels = []
        for index in (1, 3, 5):
            raw = int(background[index:index + 2], 16) / 255
            channels.append(raw / 12.92 if raw <= 0.04045
                            else ((raw + 0.055) / 1.055) ** 2.4)
    except (ValueError, IndexError):
        return "#ffffff"
    luminance = (0.2126 * channels[0] + 0.7152 * channels[1]
                 + 0.0722 * channels[2])
    return "#ffffff" if luminance <= 0.45 else TEXT


# --------------------------------------------------------------------------- #
#  Vector icons
# --------------------------------------------------------------------------- #
#  Emoji were the old icon set.  They render at different sizes per machine,
#  break DPI scaling and clash with the flat palette, so every glyph below is
#  painted on a 24x24 vector grid instead - crisp at any zoom, one visual
#  language, no font dependency.
class Icon(tk.Canvas):
    """A tiny vector icon painted on a canvas (the tkinter stand-in for SVG).

    Usage::

        Icon(parent, "check", size=16, color=OK)
    """

    def __init__(self, master: tk.Misc, name: str = "check", size: int = 16,
                 color: str = MUTED, background: str | None = None,
                 **kw) -> None:
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("borderwidth", 0)
        super().__init__(master, width=px(size), height=px(size),
                         background=background or BG, **kw)
        self._size = size
        self._color = color
        self._background = self["background"]
        self.name = name
        self._paint()

    # ------------------------------------------------------------------ paint
    def set_color(self, color: str) -> None:
        """Repaint in a new colour (used for hover / selected states)."""
        if color != self._color:
            self._color = color
            self._paint()

    def set_name(self, name: str) -> None:
        """Swap the glyph without rebuilding the widget."""
        if name != self.name:
            self.name = name
            self._paint()

    def _paint(self) -> None:
        self.delete("all")
        unit = px(self._size) / 24.0
        handler = getattr(self, f"_draw_{self.name}", None)
        if handler is None:
            handler = self._draw_document
        try:
            handler(unit, self._color)
        except tk.TclError:                       # canvas gone - never crash UI
            pass

    # ---------------------------------------------------------------- helpers
    def _line(self, unit, colour, x1, y1, x2, y2, width=2.0):
        self.create_line(x1 * unit, y1 * unit, x2 * unit, y2 * unit,
                         fill=colour, width=max(1.0, width * unit),
                         capstyle="round", joinstyle="round")

    def _rect(self, unit, colour, x, y, w, h, width=2.0):
        self.create_rectangle(x * unit, y * unit, (x + w) * unit,
                              (y + h) * unit, outline=colour,
                              width=max(1.0, width * unit))

    def _oval(self, unit, colour, x, y, w, h, width=2.0, fill=""):
        self.create_oval(x * unit, y * unit, (x + w) * unit, (y + h) * unit,
                         outline=colour, width=max(1.0, width * unit), fill=fill)

    def _poly(self, unit, colour, points, width=2.0, fill=""):
        flat: list[float] = []
        for x, y in points:
            flat.extend((x * unit, y * unit))
        self.create_polygon(flat, outline=colour,
                            width=max(1.0, width * unit), fill=fill)


    # ------------------------------------------------------------------ glyphs
    def _draw_check(self, unit, colour):
        self._line(unit, colour, 5, 12.5, 10, 17.5, 2.6)
        self._line(unit, colour, 10, 17.5, 19, 6.5, 2.6)

    def _draw_cross(self, unit, colour):
        self._line(unit, colour, 6, 6, 18, 18, 2.4)
        self._line(unit, colour, 18, 6, 6, 18, 2.4)

    def _draw_minus(self, unit, colour):
        self._line(unit, colour, 5, 12, 19, 12, 2.4)

    def _draw_plus(self, unit, colour):
        self._line(unit, colour, 5, 12, 19, 12, 2.4)
        self._line(unit, colour, 12, 5, 12, 19, 2.4)

    def _draw_chevron(self, unit, colour):
        self._line(unit, colour, 9, 5, 16, 12, 2.4)
        self._line(unit, colour, 16, 12, 9, 19, 2.4)

    def _draw_folder(self, unit, colour):
        self._poly(unit, colour, [(3, 20), (3, 6), (10, 6), (12, 9), (21, 9),
                                  (21, 20)], width=2.0)

    def _draw_search(self, unit, colour):
        self._oval(unit, colour, 4, 4, 12, 12, 2.2)
        self._line(unit, colour, 15, 15, 20, 20, 2.4)

    def _draw_launch(self, unit, colour):
        self._line(unit, colour, 4, 14, 9, 19, 2.2)
        self._line(unit, colour, 9, 19, 20, 8, 2.2)
        self._poly(unit, colour, [(12, 8), (20, 8), (20, 16)], width=2.0)

    def _draw_bolt(self, unit, colour):
        self._poly(unit, colour, [(13, 2), (5, 14), (11, 14), (10, 22),
                                  (19, 9), (13, 9)], width=2.0, fill=colour)

    def _draw_book(self, unit, colour):
        self._line(unit, colour, 4, 5, 4, 20, 2.2)
        self._line(unit, colour, 4, 5, 12, 7, 2.2)
        self._line(unit, colour, 12, 7, 20, 5, 2.2)
        self._line(unit, colour, 20, 5, 20, 20, 2.2)
        self._line(unit, colour, 4, 20, 12, 17.5, 2.2)
        self._line(unit, colour, 20, 20, 12, 17.5, 2.2)

    def _draw_package(self, unit, colour):
        self._rect(unit, colour, 3, 7, 18, 13, 2.0)
        self._line(unit, colour, 3, 7, 12, 12.5, 2.0)
        self._line(unit, colour, 21, 7, 12, 12.5, 2.0)
        self._line(unit, colour, 12, 12.5, 12, 20, 2.0)

    def _draw_hash(self, unit, colour):
        self._line(unit, colour, 9, 4, 7, 20, 2.2)
        self._line(unit, colour, 17, 4, 15, 20, 2.2)
        self._line(unit, colour, 4, 9, 20, 9, 2.2)
        self._line(unit, colour, 4, 15, 20, 15, 2.2)

    def _draw_tag(self, unit, colour):
        self._poly(unit, colour, [(4, 4), (13, 4), (20, 11), (11, 20),
                                  (4, 13)], width=2.0)
        self._oval(unit, colour, 7, 7, 3, 3, 1.6)

    def _draw_cut(self, unit, colour):
        self._line(unit, colour, 6, 6, 18, 18, 2.2)
        self._line(unit, colour, 18, 6, 6, 18, 2.2)
        self._oval(unit, colour, 4, 16, 5, 5, 2.0)
        self._oval(unit, colour, 15, 16, 5, 5, 2.0)

    def _draw_shield(self, unit, colour):
        self._poly(unit, colour, [(12, 3), (20, 6), (20, 12), (12, 21),
                                  (4, 12), (4, 6)], width=2.0)

    def _draw_sliders(self, unit, colour):
        self._line(unit, colour, 4, 8, 20, 8, 2.2)
        self._line(unit, colour, 4, 16, 20, 16, 2.2)
        self._oval(unit, colour, 8, 5.5, 5, 5, 2.0, fill=self._background)
        self._oval(unit, colour, 14, 13.5, 5, 5, 2.0, fill=self._background)


    def _draw_document(self, unit, colour):
        self._poly(unit, colour, [(6, 3), (15, 3), (19, 7), (19, 21), (6, 21)],
                   width=2.0)
        self._line(unit, colour, 9, 11, 16, 11, 2.0)
        self._line(unit, colour, 9, 15, 16, 15, 2.0)

    def _draw_image(self, unit, colour):
        self._rect(unit, colour, 3, 5, 18, 14, 2.0)
        self._oval(unit, colour, 7, 8, 4, 4, 1.8)
        self._poly(unit, colour, [(5, 18), (11, 12), (20, 18)], width=2.0)

    def _draw_clock(self, unit, colour):
        self._oval(unit, colour, 3, 3, 18, 18, 2.2)
        self._line(unit, colour, 12, 7, 12, 12, 2.2)
        self._line(unit, colour, 12, 12, 16, 14, 2.2)

    def _draw_copy(self, unit, colour):
        self._rect(unit, colour, 8, 3, 12, 14, 2.0)
        self._line(unit, colour, 4, 7, 4, 21, 2.0)
        self._line(unit, colour, 4, 21, 16, 21, 2.0)
        self._line(unit, colour, 16, 21, 16, 8, 2.0)

    def _draw_eye(self, unit, colour):
        for top in (7, 5):
            self.create_arc(3 * unit, top * unit, 21 * unit, (top + 12) * unit,
                            start=200, extent=140, style="arc", outline=colour,
                            width=max(1.0, 2.0 * unit))
        self._oval(unit, colour, 9, 9, 6, 6, 2.0, fill=colour)

    def _draw_info(self, unit, colour):
        self._oval(unit, colour, 3, 3, 18, 18, 2.2)
        self._line(unit, colour, 12, 11, 12, 17, 2.2)
        self.create_oval(11 * unit, 6.5 * unit, 13 * unit, 8.5 * unit,
                         outline="", fill=colour)

    def _draw_warning(self, unit, colour):
        self._poly(unit, colour, [(12, 3), (22, 20), (2, 20)], width=2.0)
        self._line(unit, colour, 12, 10, 12, 15, 2.2)
        self.create_oval(11 * unit, 16.5 * unit, 13 * unit, 18.5 * unit,
                         outline="", fill=colour)

    def _draw_sparkle(self, unit, colour):
        self._poly(unit, colour, [(12, 3), (14, 10), (21, 12), (14, 14),
                                  (12, 21), (10, 14), (3, 12), (10, 10)],
                   width=1.8, fill=colour)

    def _draw_lock(self, unit, colour):
        self._rect(unit, colour, 5, 11, 14, 10, 2.0)
        self.create_arc(8 * unit, 3 * unit, 16 * unit, 14 * unit, start=0,
                        extent=180, style="arc", outline=colour,
                        width=max(1.0, 2.0 * unit))

    def _draw_sun(self, unit, colour):
        self._oval(unit, colour, 8, 8, 8, 8, 2.0)
        for dx, dy in ((12, 2), (12, 20), (2, 12), (20, 12),
                       (5, 5), (19, 19), (19, 5), (5, 19)):
            self._line(unit, colour, 12 + dx * 0.75, 12 + dy * 0.75,
                       12 + dx, 12 + dy, 2.0)

    def _draw_scale(self, unit, colour):
        self._line(unit, colour, 12, 4, 12, 20, 2.2)
        self._line(unit, colour, 5, 20, 19, 20, 2.2)
        self._line(unit, colour, 4, 9, 20, 9, 2.2)
        self._line(unit, colour, 4, 9, 2, 15, 2.0)
        self._line(unit, colour, 4, 9, 6, 15, 2.0)
        self._line(unit, colour, 20, 9, 18, 15, 2.0)
        self._line(unit, colour, 20, 9, 22, 15, 2.0)

    def _draw_settings(self, unit, colour):
        self._oval(unit, colour, 9, 9, 6, 6, 2.0)
        for dx, dy in ((12, 2), (12, 22), (2, 12), (22, 12),
                       (5, 5), (19, 5), (5, 19), (19, 19)):
            self._line(unit, colour, 12 + dx * 0.72, 12 + dy * 0.72,
                       12 + dx, 12 + dy, 2.0)

    def _draw_tools(self, unit, colour):
        self._line(unit, colour, 5, 19, 15, 9, 2.4)
        self._oval(unit, colour, 13, 3, 8, 8, 2.2)
        self._line(unit, colour, 4, 16, 8, 20, 2.4)



# --------------------------------------------------------------------------- #
#  Segmented control
# --------------------------------------------------------------------------- #
class Segmented(tk.Frame):
    """A row of mutually exclusive buttons that apply the moment they are clicked.

    Per Primer's guidance a segmented control behaves as a *list of buttons*,
    not a radio group: the selection takes effect immediately, so there is never
    a Save button beside it.  Arrow keys move focus, Enter/Space select, and
    the whole control is one tab stop (roving tabindex).

    ``options`` is a sequence of ``(value, label)`` or ``(value, label, hint)``.
    """

    def __init__(self, master: tk.Misc, options: Sequence[tuple], *,
                 variable: tk.Variable | None = None,
                 command: Callable[[str], None] | None = None,
                 fill: bool = True) -> None:
        super().__init__(master, bg=BORDER)
        self.options = [tuple(item) for item in options]
        self.command = command
        self.variable = variable
        self._buttons: list[tk.Label] = []
        self._current = 0

        for index, option in enumerate(self.options):
            value, label = option[0], option[1]
            cell = tk.Frame(self, bg=BORDER, padx=1, pady=1)
            cell.grid(row=0, column=index, sticky="ew" if fill else "nse")
            if fill:
                # weight=1 but *no* uniform: uniform would force every segment
                # to the width of the longest one, and in a narrow column that
                # clipped labels like "Pages without text" in half.  Weight
                # alone shares spare space while still respecting each
                # segment's own natural width.
                self.columnconfigure(index, weight=1, minsize=0)
            button = tk.Label(cell, text=label, bg=SURFACE, fg=TEXT,
                              font=(FONT, CAPTION), padx=px(10), pady=px(8),
                              cursor="hand2", takefocus=1, highlightthickness=0,
                              borderwidth=0, wraplength=px(160))
            button.pack(fill="both", expand=True)
            button.bind("<Button-1>", lambda _e, v=value: self.select(v))
            button.bind("<Return>", lambda _e, v=value: self.select(v))
            button.bind("<KP_Enter>", lambda _e, v=value: self.select(v))
            button.bind("<FocusIn>", lambda _e, i=index: self._focus(i))
            button.bind("<FocusOut>", lambda _e: self._render())
            for sequence, step in (("<Left>", -1), ("<Up>", -1),
                                   ("<Right>", 1), ("<Down>", 1)):
                button.bind(sequence, lambda _e, s=step: self._move(s))
            self._buttons.append(button)

        if variable is not None:
            try:
                for position, option in enumerate(self.options):
                    if str(option[0]) == str(variable.get()):
                        self._current = position
                        break
            except tk.TclError:
                pass
        self._publish()

    # ------------------------------------------------------------------ state
    def value(self) -> str:
        """The currently selected value."""
        return str(self.options[self._current][0])

    def set(self, value, notify: bool = False) -> None:
        """Select ``value`` silently, or with the callback when ``notify``."""
        for index, option in enumerate(self.options):
            if str(option[0]) == str(value):
                changed = index != self._current
                self._current = index
                self._publish()
                if notify and self.command and changed:
                    self.command(self.value())
                return

    def _publish(self) -> None:
        """Repaint and mirror the selection into the bound variable."""
        self._render()
        if self.variable is not None:
            try:
                self.variable.set(self.value())
            except tk.TclError:
                pass

    # ----------------------------------------------------------------- events
    def select(self, value) -> None:
        """Click behaviour: select and immediately notify."""
        self.set(value, notify=True)

    def _move(self, step: int) -> None:
        self._focus(self._current + step)

    def _focus(self, index: int) -> None:
        self._current = index % len(self._buttons)
        self._publish()
        try:
            self._buttons[self._current].focus_set()
        except tk.TclError:
            pass

    def _render(self) -> None:
        """Repaint every segment: colour and weight only, never geometry."""
        for index, button in enumerate(self._buttons):
            selected = index == self._current
            button.configure(bg=ACCENT if selected else SURFACE,
                             fg="#ffffff" if selected else TEXT,
                             font=(FONT, CAPTION, "bold") if selected else (FONT, CAPTION))
            if button.focus_get() is button:
                button.configure(highlightthickness=px(2),
                                 highlightbackground=FOCUS, highlightcolor=FOCUS)
            else:
                button.configure(highlightthickness=0)



# --------------------------------------------------------------------------- #
#  Choice card - the "big button with a reason" control
# --------------------------------------------------------------------------- #
class ChoiceCard(tk.Frame):
    """One large, clickable card: a short title plus a one-line reason.

    Replaces stacked radio buttons.  The title says *what you get* and the hint
    says *why*, so nobody has to guess what a preset will do.  Hover and focus
    change colour only, never geometry, so the row never jumps under the mouse.
    """

    def __init__(self, master: tk.Misc, title: str, hint: str, *,
                 value: str = "", icon: str = "check", badge: str = "",
                 group: "ChoiceGroup | None" = None,
                 background: str = SURFACE, wrap: int = 330) -> None:
        super().__init__(master, bg=background, highlightthickness=px(2),
                         highlightbackground=BORDER, highlightcolor=FOCUS,
                         cursor="hand2", takefocus=1)
        self.value = str(value or title)
        self._group = group
        self._selected = False
        self._enabled = True
        self._background = background

        # A solid bar down the left edge.  It is the strongest "you are here"
        # signal in the whole design and it costs no horizontal space.
        self.bar = tk.Frame(self, bg=background, width=px(5))
        self.bar.pack(side="left", fill="y")

        body = tk.Frame(self, bg=background)
        body.pack(side="left", fill="both", expand=True, padx=(px(3), px(12)),
                  pady=px(10))
        self.body = body
        self.icon = Icon(body, icon, size=20, color=MUTED, background=background)
        self.icon.pack(side="left", padx=(0, px(11)), pady=(px(2), 0))

        text = tk.Frame(body, bg=background)
        text.pack(side="left", fill="x", expand=True)
        self.text = text
        # The badge shares the title row so it can never overlap the hint,
        # however long either string happens to be.
        title_row = tk.Frame(text, bg=background)
        title_row.pack(fill="x")
        self.title_row = title_row
        self.title_label = tk.Label(title_row, text=title, bg=background,
                                    fg=TEXT, font=(FONT, BODY, "bold"),
                                    anchor="w", justify="left")
        self.title_label.pack(side="left")
        if badge:
            self.badge = tk.Label(title_row, text=badge, bg=ACCENT_SOFT,
                                  fg=ACCENT, font=(FONT, CAPTION, "bold"),
                                  padx=7, pady=2)
            self.badge.pack(side="right", padx=(px(8), 0))
        else:
            self.badge = None
        self.hint_label = tk.Label(text, text=hint, bg=background, fg=MUTED,
                                   font=(FONT, CAPTION), anchor="w",
                                   justify="left", wraplength=px(wrap))
        self.hint_label.pack(anchor="w", pady=(3, 0))

        clickable = (self, body, text, title_row, self.title_label,
                      self.hint_label)
        for widget in clickable:
            widget.bind("<Button-1>", lambda _e: self.select())
        self.bind("<Return>", lambda _e: self.select())
        self.bind("<KP_Enter>", lambda _e: self.select())
        self.bind("<FocusIn>", lambda _e: self._paint())
        self.bind("<FocusOut>", lambda _e: self._paint())
        if group is not None:
            group.add(self)

    # ------------------------------------------------------------------ state
    def select(self) -> None:
        """Make this card active and tell the group about it."""
        if not self._enabled:
            return
        if self._group is not None:
            self._group.select(self.value, notify=True)
        else:
            self.set_selected(True)

    def set_selected(self, selected: bool) -> None:
        """Reflect a selection made elsewhere, without re-firing callbacks."""
        self._selected = bool(selected)
        if self.badge is not None:
            self.badge.configure(
                bg=ACCENT if self._selected else ACCENT_SOFT,
                fg="#ffffff" if self._selected else ACCENT)
        self._paint()

    def set_enabled(self, enabled: bool) -> None:
        """Grey the card out and stop it responding to clicks."""
        self._enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()

    def _paint(self) -> None:
        """Repaint the whole card: fill, border, left bar, text, icon.

        An unselected card is a soft tinted surface so it reads as something
        you can click; the selected one gets a tinted fill, a thick accent
        border and a solid accent bar down its left edge.  Three independent
        signals, so the active choice is obvious before a word is read.
        """
        if not self._enabled:
            self._restyle(SURFACE, BORDER)
            self.title_label.configure(fg=MUTED, font=(FONT, BODY, "normal"))
            self.hint_label.configure(fg=MUTED)
            self.icon.set_color(BORDER_STRONG)
            self.bar.configure(bg=SURFACE)
            return

        if self._selected:
            self._restyle(ACCENT_SOFT, ACCENT)
            self.bar.configure(bg=ACCENT)
            self.title_label.configure(fg=ACCENT, font=(FONT, BODY, "bold"))
            self.icon.set_color(ACCENT)
        else:
            self._restyle(SURFACE, BORDER)
            self.bar.configure(bg=SURFACE)
            self.title_label.configure(fg=TEXT, font=(FONT, BODY, "bold"))
            self.icon.set_color(MUTED)
        if self.focus_get() is self:
            self.configure(highlightbackground=FOCUS, highlightthickness=px(3))
        elif self._selected:
            self.configure(highlightbackground=ACCENT, highlightthickness=px(3))
        else:
            self.configure(highlightbackground=BORDER_STRONG,
                           highlightthickness=px(2))

    def _restyle(self, fill: str, border: str) -> None:
        """Paint the card and every child with one background colour."""
        self.configure(bg=fill, highlightbackground=border,
                       highlightthickness=px(2))
        for child in (self.body, self.text, self.title_row, self.hint_label,
                      self.title_label):
            try:
                child.configure(bg=fill)
            except tk.TclError:
                continue
        if self.badge is not None:
            self.badge.configure(
                bg=ACCENT if self._selected else fill,
                fg="#ffffff" if self._selected else MUTED)
        self.icon.configure(background=fill)


class ChoiceGroup:
    """Keeps a set of :class:`ChoiceCard` widgets mutually exclusive."""

    def __init__(self, command: Callable[[str], None] | None = None) -> None:
        self.command = command
        self.cards: list[ChoiceCard] = []
        self.value = ""

    def add(self, card: ChoiceCard) -> None:
        """Register a card; the first one added becomes the default."""
        self.cards.append(card)
        if not self.value:
            self.select(card.value, notify=False)

    def select(self, value: str, notify: bool = True) -> None:
        """Select by value, repainting every card in the group."""
        self.value = str(value)
        for card in self.cards:
            card.set_selected(card.value == self.value)
        if notify and self.command:
            self.command(self.value)

    def get(self) -> str:
        return self.value



# --------------------------------------------------------------------------- #
#  Labelled field
# --------------------------------------------------------------------------- #
class Field(tk.Frame):
    """Label on the left, control on the right, one-line reason underneath.

    Mirrors the court-form pattern "label -> what goes here -> why", so nobody
    has to guess what a control does.  The control column lines up down the
    whole page because every field shares the same grid geometry.

    Pass the control either directly (``control=widget``) or as a ``factory``
    that receives the finished Field and returns the widget.  The factory form
    exists because a control must be *parented to the Field* - handing in a
    widget that was built against some other parent makes Tk lay it out in the
    wrong place, which is how the limit steppers ended up on top of the hint.
    """

    def __init__(self, master: tk.Misc, label: str,
                 control: tk.Widget | None = None, *,
                 hint: str = "", unit: str = "", background: str = CARD,
                 factory: Callable[["Field"], tk.Widget] | None = None) -> None:
        super().__init__(master, bg=background)
        self.columnconfigure(1, weight=1)
        text = tk.Frame(self, bg=background)
        text.grid(row=0, column=0, sticky="w", padx=(0, px(14)), pady=px(3))
        self.label = tk.Label(text, text=label, bg=background, fg=TEXT,
                              font=(FONT, BODY), anchor="w", justify="left")
        self.label.pack(anchor="w")
        if hint:
            self.hint = tk.Label(text, text=hint, bg=background, fg=MUTED,
                                 font=(FONT, CAPTION), anchor="w", justify="left",
                                 wraplength=px(340))
            self.hint.pack(anchor="w", pady=(2, 0))
        else:
            self.hint = None
        if control is None and factory is not None:
            control = factory(self)
        if control is None:
            raise ValueError("Field needs either a control or a factory")
        self.control = control
        control.grid(row=0, column=1, sticky="w", pady=px(3))
        if unit:
            self.unit_label = tk.Label(self, text=unit, bg=background, fg=MUTED,
                                       font=(FONT, CAPTION))
            self.unit_label.grid(row=0, column=2, sticky="w", padx=(px(7), 0))
        else:
            self.unit_label = None


# --------------------------------------------------------------------------- #
#  Switch - an on/off control that says what "on" actually does
# --------------------------------------------------------------------------- #
class Switch(tk.Frame):
    """A labelled on/off toggle: a switch, not a bare check box."""

    def __init__(self, master: tk.Misc, label: str, *, value: bool = False,
                 hint: str = "", variable: tk.Variable | None = None,
                 command: Callable[[bool], None] | None = None,
                 background: str = CARD) -> None:
        super().__init__(master, bg=background, cursor="hand2", takefocus=1)
        self.command = command
        self.variable = variable
        self._value = bool(value)
        self._background = background
        if variable is not None:
            try:
                self._value = bool(variable.get())
            except tk.TclError:
                pass

        self.track = tk.Frame(self, bg=BORDER_STRONG, width=px(40),
                              height=px(22), highlightthickness=0)
        self.track.pack(side="left", padx=(0, px(10)))
        self.knob = tk.Frame(self.track, bg="#ffffff", width=px(16),
                             height=px(16), highlightthickness=0)
        self.knob.place(relx=0.0, rely=0.5, anchor="w", x=px(3))

        text = tk.Frame(self, bg=background)
        text.pack(side="left", fill="x", expand=True)
        self.label = tk.Label(text, text=label, bg=background, fg=TEXT,
                              font=(FONT, BODY), anchor="w", justify="left")
        self.label.pack(anchor="w")
        if hint:
            self.hint = tk.Label(text, text=hint, bg=background, fg=MUTED,
                                 font=(FONT, CAPTION), anchor="w", justify="left",
                                 wraplength=px(340))
            self.hint.pack(anchor="w", pady=(2, 0))
        else:
            self.hint = None

        for widget in (self, self.track, self.knob, text, self.label,
                       self.hint or self):
            widget.bind("<Button-1>", lambda _e: self.toggle())
        self.bind("<Return>", lambda _e: self.toggle())
        self.bind("<KP_Enter>", lambda _e: self.toggle())
        self.bind("<FocusIn>", lambda _e: self._paint())
        self.bind("<FocusOut>", lambda _e: self._paint())
        self._paint()

    # ------------------------------------------------------------------ state
    def get(self) -> bool:
        return self._value

    def set(self, value: bool, notify: bool = False) -> None:
        """Set the switch position, optionally firing the callback."""
        value = bool(value)
        if value == self._value:
            return
        self._value = value
        self._paint()
        if self.variable is not None:
            try:
                self.variable.set(value)
            except tk.TclError:
                pass
        if notify and self.command:
            self.command(value)

    def toggle(self) -> None:
        """Flip the switch."""
        self.set(not self._value, notify=True)

    def _paint(self) -> None:
        self.track.configure(bg=OK if self._value else BORDER_STRONG)
        self.knob.place(relx=1.0 if self._value else 0.0, rely=0.5,
                        anchor="e" if self._value else "w",
                        x=-px(3) if self._value else px(3))
        self.label.configure(fg=TEXT if self._value else MUTED)
        if self.focus_get() is self:
            self.configure(highlightthickness=px(2), highlightbackground=FOCUS,
                           highlightcolor=FOCUS)
        else:
            self.configure(highlightthickness=0)



# --------------------------------------------------------------------------- #
#  Stepper - a number the user never has to type
# --------------------------------------------------------------------------- #
class Stepper(tk.Frame):
    """An editable number with optional arrow-key adjustments and safe bounds."""

    def __init__(self, master: tk.Misc, *, minimum: float, maximum: float,
                 value: float, step: float = 1.0, suffix: str = "",
                 decimals: int = 0, width: int = 7,
                 command: Callable[[float], None] | None = None) -> None:
        super().__init__(master, bg=CARD)
        self.minimum, self.maximum = float(minimum), float(maximum)
        self.step, self.decimals = float(step), decimals
        self.command = command
        self._value = self._clamp(value)
        self.variable = tk.StringVar(master=self, value=self._text())
        self.value_label = ttk.Spinbox(
            self, from_=minimum, to=maximum, increment=step, width=width,
            textvariable=self.variable, command=self._commit,
            font=(FONT, BODY), justify="center", style="Number.TSpinbox")
        self.value_label.pack(side="left", ipady=px(3))
        if suffix:
            tk.Label(self, text=suffix, bg=CARD, fg=MUTED,
                     font=(FONT, CAPTION)).pack(side="left", padx=px(6))
        self.value_label.bind("<Return>", self._commit)
        self.value_label.bind("<FocusOut>", self._commit)
        self.value_label.bind("<Escape>", lambda _e: self.variable.set(self._text()))

    def _clamp(self, value: float) -> float:
        return round(max(self.minimum, min(self.maximum, float(value))), self.decimals)

    def _text(self) -> str:
        return f"{self._value:.{self.decimals}f}"

    def _commit(self, _event=None) -> None:
        import math

        try:
            value = float(self.variable.get().strip())
            if not math.isfinite(value):
                raise ValueError("Enter a finite number")
        except ValueError:
            self.variable.set(self._text())
            self.bell()
            return
        self.set(value, notify=True)

    def get(self) -> float:
        self._commit()
        return self._value

    def set(self, value: float, notify: bool = False) -> None:
        value = self._clamp(value)
        changed = value != self._value
        self._value = value
        self.variable.set(self._text())
        if changed and notify and self.command:
            self.command(value)

    def _bump(self, direction: int) -> None:
        self.set(self.get() + direction * self.step, notify=True)

    def set_range(self, minimum: float, maximum: float) -> None:
        self.minimum = float(minimum)
        self.maximum = max(float(minimum), float(maximum))
        self.value_label.configure(from_=self.minimum, to=self.maximum)
        self.set(self._value)

    def set_maximum(self, maximum: float) -> None:
        self.set_range(self.minimum, maximum)


class Disclosure(tk.Frame):
    """A collapsed-by-default panel with a chevron, per Win32 guidance.

    Advanced knobs live here, so the default screen stays simple while nothing
    is hidden away or lost.  The label states the effect and flips between
    "More options" and "Fewer options".
    """

    def __init__(self, master: tk.Misc, title: str, *,
                 hint: str = "", background: str = CARD,
                 start_open: bool = False) -> None:
        super().__init__(master, bg=background)
        self._open = bool(start_open)
        self.title = title

        self.head = tk.Frame(self, bg=background, cursor="hand2", takefocus=1)
        self.head.pack(fill="x")
        self.chevron = Icon(self.head, "chevron", size=14, color=ACCENT,
                            background=background)
        self.chevron.pack(side="left", padx=(0, px(7)), pady=px(5))
        self.button = tk.Label(self.head, text="", bg=background, fg=ACCENT,
                               font=(FONT, CAPTION, "bold"), cursor="hand2",
                               takefocus=1, anchor="w", pady=px(5))
        self.button.pack(side="left", fill="x", expand=True)

        # The body uses grid so that Field rows (which grid their own children)
        # can be placed inside it without mixing geometry managers.
        self.body = tk.Frame(self, bg=background)
        self._next_row = 0
        if hint:
            self.hint_label = tk.Label(self.body, text=hint, bg=background,
                                       fg=MUTED, font=(FONT, CAPTION), anchor="w",
                                       justify="left", wraplength=px(440))
            self.hint_label.grid(row=0, column=0, columnspan=2, sticky="w",
                                 pady=(0, px(8)))
            self._next_row = 1
        for widget in (self.head, self.button, self.chevron):
            widget.bind("<Button-1>", lambda _e: self.toggle())
            widget.bind("<Return>", lambda _e: self.toggle())
            widget.bind("<KP_Enter>", lambda _e: self.toggle())
            widget.bind("<FocusIn>", lambda _e: self._paint())
            widget.bind("<FocusOut>", lambda _e: self._paint())
        self._render()

    def add_row(self, widget: tk.Widget) -> None:
        """Add a control row below the hint (grid-managed)."""
        widget.grid(row=self._next_row, column=0, sticky="w", pady=px(2))
        self._next_row += 1

    # ------------------------------------------------------------------ state
    @property
    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool) -> None:
        """Show or hide the panel."""
        if bool(open_) != self._open:
            self._open = bool(open_)
            self._render()

    def toggle(self) -> None:
        """Flip the panel open or shut."""
        self.set_open(not self._open)

    def _render(self) -> None:
        self.button.configure(
            text=("Fewer options  ·  " if self._open else "More options  ·  ")
            + self.title)
        if self._open:
            self.body.pack(fill="x", pady=(px(2), px(8)))
        else:
            self.body.pack_forget()
        self._paint()

    def _paint(self) -> None:
        if self.head.focus_get() is self.head:
            self.head.configure(highlightthickness=px(2),
                                highlightbackground=FOCUS,
                                highlightcolor=FOCUS)
        else:
            self.head.configure(highlightthickness=0)



# --------------------------------------------------------------------------- #
#  Page map - "show me the pages so I can choose where to cut"
# --------------------------------------------------------------------------- #
class PageTile(tk.Frame):
    """One page in the map: a thumbnail, a big page number, and its file.

    A court filing is often mostly blank paper, so thumbnails alone look
    identical.  The page number is therefore the loud element and the
    thumbnail is the quiet one - the number is what the user is actually
    deciding on, and the thumbnail is only there to confirm "is this the right
    document?".

    Thumbnails render lazily (see :class:`PageMap`) because a 200 page filing
    must not freeze the window while it draws.
    """

    THUMB_W = 86
    THUMB_H = 112

    def __init__(self, master: tk.Misc, number: int, source: str, *,
                 page_ref: tuple[int, int], on_toggle=None) -> None:
        super().__init__(master, bg=SURFACE, highlightthickness=px(2),
                         highlightbackground=BORDER, cursor="hand2",
                         takefocus=1)
        self.number = number
        self.page_ref = page_ref                 # (file index, page index)
        self._on_toggle = on_toggle
        self._photo: tk.PhotoImage | None = None
        self._selected = False

        # The number sits in its own strip on top, so it stays legible even
        # when the thumbnail is still a blank placeholder.
        self.caption = tk.Label(self, text=str(number), bg=SURFACE, fg=TEXT,
                                font=(FONT, BODY, "bold"))
        self.caption.pack(fill="x", padx=px(4), pady=(px(4), px(2)))
        self.thumb = tk.Label(self, text="...", bg=CARD, fg=BORDER_STRONG,
                              font=(FONT, CAPTION), width=self.THUMB_W // 8,
                              height=self.THUMB_H // 14, relief="flat",
                              highlightthickness=px(1),
                              highlightbackground=BORDER)
        self.thumb.pack(padx=px(4), pady=(0, px(4)))
        for widget in (self, self.thumb, self.caption):
            widget.bind("<Button-1>", lambda _e: self._click())
        self.bind("<Return>", lambda _e: self._click())
        self.bind("<FocusIn>", lambda _e: self._paint())
        self.bind("<FocusOut>", lambda _e: self._paint())

    def _click(self) -> None:
        """Mark or unmark this page as the first page of a new part."""
        if self._on_toggle:
            self._on_toggle(self.page_ref)

    def set_thumbnail(self, photo: tk.PhotoImage | None) -> None:
        """Show a rendered thumbnail (or keep the page number if there is none)."""
        self._photo = photo                      # keep a reference alive
        if photo is not None:
            self.thumb.configure(image=photo, text="")

    def set_selected(self, selected: bool) -> None:
        """Highlight pages that begin a new part."""
        self._selected = bool(selected)
        self._paint()

    def set_boundary(self, is_first: bool) -> None:
        """Show that this page is the first of a *different* file.

        When several PDFs are added the map shows one long run of pages, so a
        heavier edge marks exactly where the next file starts.
        """
        self.configure(highlightthickness=px(3 if is_first else 2),
                       highlightbackground=(ACCENT if is_first else BORDER))
        if is_first:
            self.caption.configure(fg=ACCENT)

    def _paint(self) -> None:
        if self._selected:
            # A cut page is marked green *and* gets a tick, so the meaning does
            # not depend on recognising the colour.
            self.configure(bg=OK_SOFT, highlightbackground=OK,
                           highlightthickness=px(3))
            self.caption.configure(text=f"{self.number}  ✓", bg=OK_SOFT, fg=OK,
                                   font=(FONT, BODY, "bold"))
        elif self.focus_get() is self:
            self.configure(highlightbackground=FOCUS, highlightthickness=px(3))
            self.caption.configure(text=str(self.number), bg=SURFACE, fg=TEXT,
                                   font=(FONT, BODY, "bold"))
        else:
            self.configure(highlightbackground=BORDER, highlightthickness=px(2))
            self.caption.configure(text=str(self.number), bg=SURFACE, fg=TEXT,
                                   font=(FONT, BODY, "bold"))



class PageMap(tk.Frame):
    """A scrollable strip of every page, so the user can see where to cut.

    The problem this solves: "split by pages" with only a *maximum* number asks
    the user to guess.  Here they can scroll through the actual pages, see the
    number printed on each, and click the ones a new part should start at - so
    they get 5 pages, then 8, then 3, exactly as the exhibits need.
    """

    COLUMNS = 8

    #: Fallback for *Split evenly* when no page limit has reached the map yet.
    DEFAULT_PART_SIZE = 50

    def __init__(self, master: tk.Misc, *,
                 on_change: Callable[[list[tuple[int, int]]], None] | None = None,
                 height: int = 340) -> None:
        super().__init__(master, bg=CARD)
        self.on_change = on_change
        self.tiles: list[PageTile] = []
        self.headers: list[tk.Label] = []
        self.sources: list[tuple[str, int]] = []     # (path, page count)
        self.marks: set[tuple[int, int]] = set()     # pages that start a part
        self.part_size = 0                           # for *Split evenly*
        self._queue: list[PageTile] = []
        self._busy = False
        self.cache_dir = Path.home() / ".pdf_helper_cache"

        self.hint = tk.Label(
            self, text="Add the files first - every page will appear here so "
                       "you can choose where each part starts.",
            bg=CARD, fg=MUTED, font=(FONT, BODY))
        self.hint.pack(padx=px(14), pady=px(12))

        body = tk.Frame(self, bg=CARD)
        body.pack(fill="both", expand=True, padx=px(12), pady=(0, px(10)))
        self.body = body
        self.canvas = tk.Canvas(body, bg=SURFACE, highlightthickness=px(2),
                                highlightbackground=BORDER, height=px(height))
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.grid_frame = tk.Frame(self.canvas, bg=SURFACE)
        self._window = self.canvas.create_window((0, 0), window=self.grid_frame,
                                                 anchor="nw")
        self.grid_frame.bind("<Configure>",
                             lambda _e: self.canvas.configure(
                                 scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(
                             self._window, width=e.width))
        self.canvas.bind("<MouseWheel>", self._wheel)
        body.pack_forget()

        self.summary = tk.Label(self, text="", bg=ACCENT_SOFT, fg=ACCENT,
                                font=(FONT, BODY, "bold"), anchor="w",
                                padx=px(12), pady=px(8))
        self.controls = tk.Frame(self, bg=CARD)
        IconButton(self.controls, "Split evenly", self.split_evenly,
                   icon="cut", background=CARD, surface=SURFACE).pack(
            side="left", padx=(px(12), 0))
        IconButton(self.controls, "One single file", self.clear_marks,
                   icon="cross", background=CARD, surface=SURFACE).pack(
            side="left", padx=px(8))
        self.var_count = tk.StringVar(value="")
        tk.Label(self.controls, textvariable=self.var_count, bg=CARD, fg=MUTED,
                 font=(FONT, CAPTION)).pack(side="left", padx=px(12))

    # ----------------------------------------------------------------- build
    def set_sources(self, sources: Sequence[tuple[str, int]]) -> None:
        """Show ``(path, page_count)`` for every file the user picked."""
        self.sources = list(sources)
        for tile in self.tiles:
            tile.destroy()
        self.tiles = []
        for child in self.headers:
            child.destroy()
        self.headers = []
        self.marks = set()
        if not self.sources:
            self.hint.pack(padx=px(14), pady=px(12))
            self.body.pack_forget()
            self.summary.pack_forget()
            self.controls.pack_forget()
            return
        self.hint.pack_forget()
        self.controls.pack(fill="x", side="bottom")
        self.summary.pack(fill="x", side="bottom", before=self.controls)
        self.body.pack(fill="both", expand=True, padx=px(12), pady=(0, px(10)))

        for column in range(self.COLUMNS):
            self.grid_frame.columnconfigure(column, weight=1, uniform="page")

        # Tiles are laid out through a running row/column cursor rather than
        # number//COLUMNS, because a file heading occupies a whole row and so
        # the first page of each file always starts in column 0.
        number = 0
        row = 0
        for file_index, (path, pages) in enumerate(self.sources):
            if not pages:
                continue
            if file_index:
                row = self._add_header(row, file_index, path, pages) + 1
            for page_index in range(pages):
                number += 1
                tile = PageTile(self.grid_frame, number, path,
                                page_ref=(file_index, page_index),
                                on_toggle=self._toggle)
                # row + page_index // COLUMNS, not a single fixed row: the
                # earlier version laid every page of a file into one grid row,
                # so 20 pages stacked on top of each other.
                tile.grid(row=row + page_index // self.COLUMNS,
                          column=page_index % self.COLUMNS,
                          padx=px(4), pady=px(4))
                if page_index == 0 and file_index:
                    tile.set_boundary(True)
                self.tiles.append(tile)
            row += -(-pages // self.COLUMNS)     # next free row after this file
        self._queue = list(self.tiles)
        self._pump()
        self._refresh()

    def _add_header(self, row: int, file_index: int, path: str,
                    pages: int) -> int:
        """A band naming the file whose pages start on the next row."""
        name = Path(path).name
        if len(name) > 52:
            name = name[:49] + "..."
        text = f"File {file_index + 1}:  {name}   ({pages} page"
        text += "s)" if pages != 1 else ")"
        band = tk.Label(self.grid_frame, text=text, bg=ACCENT_SOFT, fg=ACCENT,
                        font=(FONT, CAPTION, "bold"), anchor="w",
                        padx=px(10), pady=px(4))
        band.grid(row=row, column=0, columnspan=self.COLUMNS, sticky="ew",
                  pady=(px(8), 0))
        self.headers.append(band)
        return row

    # ------------------------------------------------------------- rendering
    def _pump(self) -> None:
        """Render a few thumbnails then yield - the window never freezes."""
        if self._busy or not self._queue:
            return
        self._busy = True
        try:
            for tile in self._queue[:3]:
                if tile in self._queue:
                    self._queue.remove(tile)
                # One unreadable page must not stop the rest of the strip, so
                # this is guarded far wider than just TclError.
                try:
                    photo = render_thumbnail(
                        self.sources[tile.page_ref[0]][0], tile.page_ref[1],
                        PageTile.THUMB_W, PageTile.THUMB_H, self.cache_dir)
                except Exception:
                    photo = None
                if photo is not None:
                    tile.set_thumbnail(photo)
        except tk.TclError:                        # window closed mid-render
            self._queue = []
        finally:
            self._busy = False
        if self._queue:
            self.after(1, self._pump)

    def _wheel(self, event) -> None:
        """Scroll the strip under the pointer."""
        self.canvas.yview_scroll(-1 * (event.delta // 120) or
                                 (-1 if event.delta > 0 else 1), "units")

    # ----------------------------------------------------------------- marks
    def _toggle(self, page_ref: tuple[int, int]) -> None:
        """Start a new part at this page, or remove the mark if it is there."""
        if page_ref == (0, 0):                     # page 1 always starts part 1
            return
        if page_ref in self.marks:
            self.marks.discard(page_ref)
        else:
            self.marks.add(page_ref)
        self._refresh()

    def clear_marks(self) -> None:
        """One single file - no cuts at all."""
        self.marks.clear()
        self._refresh()

    def split_evenly(self) -> None:
        """Cut into equal parts, honouring the chosen maximum page count."""
        self.marks.clear()
        total = len(self.tiles)
        if total < 2:
            self._refresh()
            return
        # ``part_size`` used to default to 0 and was only filled in at RUN
        # time, so pressing this button before running cut the file into a
        # 370-page part and a 1-page part - the "40" the user had typed was
        # simply not there yet.  Fall back to a sane default rather than
        # dividing by "the whole document".
        size = self.part_size if self.part_size and self.part_size > 1 else DEFAULT_PART_SIZE
        size = max(1, min(size, total))
        for number in range(size, total, size):
            ref = self.tiles[number].page_ref
            if ref != (0, 0):
                self.marks.add(ref)
        self._refresh()

    def set_part_size(self, pages: int) -> None:
        """Tell *Split evenly* the maximum pages the user chose."""
        self.part_size = max(1, int(pages or 1))

    # ---------------------------------------------------------------- output
    def cuts(self) -> list[tuple[int, int]]:
        """Sorted list of pages that begin a new part (never includes page 1)."""
        return sorted(self.marks)

    def groups(self) -> list[tuple[int, int, int, int]]:
        """``(file_index, first_page, last_page, count)`` for every part."""
        tiles = self.tiles
        if not tiles:
            return []
        cut_positions = [0] + [self.tiles.index(tile)
                               for tile in tiles if tile.page_ref in self.marks]
        out = []
        for position, begin in enumerate(cut_positions):
            end = (cut_positions[position + 1] - 1
                   if position + 1 < len(cut_positions) else len(tiles) - 1)
            file_index, first = tiles[begin].page_ref
            _file, last = tiles[end].page_ref
            out.append((file_index, first, last, end - begin + 1))
        return out

    def _refresh(self) -> None:
        """Repaint the marks and rewrite the one-line summary."""
        marked = set(self.cuts())
        for tile in self.tiles:
            tile.set_selected(tile.page_ref in marked)
        parts = self.groups()
        if not parts:
            self.summary.configure(text="Add some files to see the pages.")
            self.var_count.set("")
            return
        if len(parts) == 1:
            self.summary.configure(
                text=f"One file of {parts[0][3]} pages — click any page to "
                     f"start a new file there.")
            self.var_count.set("")
        else:
            chips = [f"Part {index}: {count} page"
                     f"{'s' if count != 1 else ''}"
                     for index, (_f, _a, _b, count) in enumerate(parts, 1)]
            self.summary.configure(text="   ".join(chips))
            self.var_count.set(f"{len(parts)} files will be created")
        if self.on_change:
            self.on_change(self.cuts())


# --------------------------------------------------------------------------- #
#  Seal preview - "show me where the seal will land, before I commit"
# --------------------------------------------------------------------------- #
class SealPreview(tk.Frame):
    """A live picture of one page with the seal drawn where it will really go.

    Position, size, strength and the fine nudge are all drawn through
    :func:`court_ocr.stamp.seal_rect`, the *same* function the stamper uses, so
    the preview cannot quietly disagree with the output.  Re-rendering is
    debounced and the page itself is rasterised only when the document or the
    page number changes, so dragging the arrows stays smooth.
    """

    PAGE_W = 250                     # preview width in logical pixels
    PAGE_H = 300                     # preview height cap (A4 proportions)
    NUDGE_STEP = 1.0                 # mm per arrow press

    #: Plain words for each anchor, used in the read-out under the picture.
    NAMES = {"bottom-right": "bottom right", "bottom-left": "bottom left",
             "bottom-center": "bottom centre", "top-right": "top right",
             "top-left": "top left", "top-center": "top centre",
             "center": "the centre"}

    def __init__(self, master: tk.Misc, *, on_change=None, on_slider=None,
                 slider_host: tk.Misc | None = None,
                 width: int = PAGE_W) -> None:
        super().__init__(master, bg=CARD)
        self.on_change = on_change
        self.on_slider = on_slider
        # The sliders are built somewhere else (normally the wider controls
        # column) but stay owned by this object, so the value in, the picture
        # out, and the sync logic all live in one place.
        self.slider_host = slider_host if slider_host is not None else self
        self.doc_path: Path | None = None
        self.page_count = 0
        self.page = 0
        self.seal_path: Path | None = None
        self.position = "bottom-right"
        self.width_percent = 20.0
        self.opacity = 0.9
        self.nudge_x = 0.0
        self.nudge_y = 0.0
        self._after_id: str | None = None
        self._base = None
        self._base_key: tuple | None = None
        self._page_points = (1.0, 1.0)
        self._page_ratio = 1.414            # A4 until a real page is read
        self._photo = None
        self._scale = 1.0

        self.canvas = tk.Canvas(self, bg=SURFACE, highlightthickness=px(2),
                                highlightbackground=BORDER,
                                width=px(width), height=px(int(width * 1.3)))
        self.canvas.pack()
        # The picture is scaled to the canvas width, so cap the drawn height:
        # a full A4 page plus the arrows was taller than the tool page and
        # pushed the RUN button off the bottom of a 780 px window.
        self.max_height = px(self.PAGE_H)
        self.hint = tk.Label(self, text="", bg=CARD, fg=MUTED,
                             font=(FONT, CAPTION), wraplength=px(width))
        self.hint.pack(pady=(px(6), 0))

        self._build_nudges()
        self.show_message("Add a PDF and a seal image to see the result here.")

    def build_sliders(self) -> None:
        """Create the sliders.

        Called by the tool page *after* its own controls, so the sliders land
        in the right place in the column instead of jumping to the top.
        """
        self._build_sliders()

    # ---------------------------------------------------------------- sliders
    def _build_sliders(self) -> None:
        """Two horizontal volume-bar style sliders: size and strength.

        A slider beats a number box here because you watch the picture change
        as you drag, and because you cannot type a silly value like 300%.
        The resulting number stays printed at the end of each track, so the
        control is still honest about exactly what the seal will be.
        """
        self._sliders: dict[str, tk.Scale] = {}
        self._vars: dict[str, tk.DoubleVar] = {}
        self._readouts: dict[str, tk.Label] = {}
        self._slider_guard = False

        specs = (("width_percent", "How big", 5, 60, self.width_percent,
                  " of page width", 1),
                 ("opacity", "How strong", 10, 100, self.opacity * 100,
                  "% solid", 5))
        for key, label, low, high, value, suffix, step in specs:
            row = tk.Frame(self.slider_host, bg=CARD)
            row.pack(fill="x", pady=(px(10), 0))
            tk.Label(row, text=label, bg=CARD, fg=MUTED, width=10,
                     anchor="w", font=(FONT, CAPTION)).pack(side="left")

            var = tk.DoubleVar(value=value)
            # ``bg`` is what draws the little handle, so it has to contrast
            # with the trough: left at the card colour the handle vanished into
            # the track and the slider just looked like a broken bar.
            scale = tk.Scale(
                row, from_=low, to=high, orient="horizontal", variable=var,
                showvalue=False, length=px(140), sliderrelief="raised",
                sliderlength=px(18), bd=px(2), relief="flat", bg=ACCENT,
                troughcolor=SURFACE, fg=MUTED, activebackground=ACCENT_HOVER,
                highlightthickness=0, font=(FONT, CAPTION))
            scale.pack(side="left", fill="x", expand=True, padx=(px(6), px(8)))

            read = tk.Label(row, text="", bg=CARD, fg=ACCENT, width=13,
                            anchor="w", font=(FONT, CAPTION))
            read.pack(side="left")

            var.trace_add(
                "write",
                lambda *_a, k=key, v=var, s=step, r=read, sfx=suffix:
                    self._slider_moved(k, v, s, r, sfx))
            self._sliders[key] = scale
            self._vars[key] = var
            self._readouts[key] = read
            read.configure(text=f"{value:.0f}{suffix}")

    def _slider_moved(self, key: str, var: tk.DoubleVar, step: float,
                      read: tk.Label, suffix: str) -> None:
        """A slider moved: snap to a round number, then redraw the page."""
        if self._slider_guard:
            return
        raw = var.get()
        snapped = round(raw / step) * step
        if abs(snapped - raw) > 1e-9:
            # Writing back re-enters this trace, so raise the guard first.
            self._slider_guard = True
            var.set(snapped)
            self._slider_guard = False
            raw = snapped
        if key == "width_percent":
            self.width_percent = float(raw)
        else:
            self.opacity = max(0.0, min(1.0, raw / 100.0))
        read.configure(text=f"{raw:.0f}{suffix}")
        self._request()
        if self.on_slider:
            self.on_slider()

    def _sync_sliders(self) -> None:
        """Move the sliders when the value was changed somewhere else.

        The same trace drives the picture, so the guard is what stops the two
        from shoving the value back and forth forever.  Before ``build_sliders``
        runs there is nothing to move, which is not an error.
        """
        if not getattr(self, "_vars", None):
            return
        self._slider_guard = True
        try:
            for key, var in self._vars.items():
                target = (self.width_percent if key == "width_percent"
                          else self.opacity * 100)
                if abs(var.get() - target) > 0.5:
                    var.set(round(target))
        finally:
            self._slider_guard = False
        for key, var in self._vars.items():
            suffix = (" of page width" if key == "width_percent" else "% solid")
            self._readouts[key].configure(text=f"{var.get():.0f}{suffix}")

    # ------------------------------------------------------------------ setup
    def _build_nudges(self) -> None:
        """The arrow pad, plus a reset: fine placement without typing numbers.

        Kept deliberately narrow.  This row used to be ~670 px wide, which
        dragged the whole picture column out to 669 px and squeezed the
        controls column - including the "Choose..." button - down to 229 px,
        so the button was drawn off the edge of its own frame.
        """
        row = tk.Frame(self, bg=CARD)
        row.pack(pady=(px(8), 0))
        pad = tk.Frame(row, bg=CARD)
        pad.pack(side="left")
        # A plus shape: up/down in the middle column, left/right either side.
        self.arrow_buttons: dict[str, tk.Label] = {}
        for name, r, c in (("up", 0, 1), ("left", 1, 0), ("right", 1, 2),
                           ("down", 2, 1)):
            self._arrow(pad, name, r, c)
        IconButton(row, "Reset", self.reset_nudge, icon="cross",
                   background=CARD, surface=SURFACE).pack(
            side="left", padx=(px(8), 0))

    #: Which way each arrow actually moves, in plain screen terms (y grows
    #: downwards on screen, so "up" is negative).  Kept in one table so the
    #: glyph and its direction cannot drift apart.
    ARROWS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    def _arrow(self, parent: tk.Misc, name: str, row: int, col: int) -> None:
        glyph = {"up": "\u25b2", "down": "\u25bc",
                 "left": "\u25c0", "right": "\u25b6"}[name]
        button = tk.Label(parent, text=glyph, bg=SURFACE, fg=ACCENT,
                          font=(FONT, BODY, "bold"), width=3, pady=px(3),
                          cursor="hand2", takefocus=1, highlightthickness=px(1),
                          highlightbackground=BORDER)
        button.grid(row=row, column=col, padx=px(1), pady=px(1))
        button.bind("<Button-1>", lambda _e, n=name: self.nudge_arrow(n))
        button.bind("<Return>", lambda _e, n=name: self.nudge_arrow(n))
        self.arrow_buttons[name] = button

    def nudge_arrow(self, name: str) -> None:
        """Press one of the four arrows, by name."""
        dx, dy = self.ARROWS.get(name, (0, 0))
        self.nudge(dx * self.NUDGE_STEP, dy * self.NUDGE_STEP)

    # ------------------------------------------------------------------ inputs
    def set_document(self, path: str | Path | None) -> None:
        """Show this PDF, defaulting to its first page."""
        self.doc_path = Path(path) if path else None
        self._base_key = None
        self.page = 0
        self._count_pages()
        self._request()

    def set_page(self, page: int) -> None:
        self.page = max(0, int(page))
        self._request()

    def set_seal(self, path: str | Path | None) -> None:
        self.seal_path = Path(path) if path else None
        self._request()

    def set_placement(self, *, position: str | None = None,
                      width_percent: float | None = None,
                      opacity: float | None = None) -> None:
        """Follow a control change; ``None`` means "leave that one alone"."""
        if position is not None:
            self.position = str(position)
        if width_percent is not None:
            try:
                self.width_percent = float(width_percent)
            except (TypeError, ValueError):
                pass
        if opacity is not None:
            try:
                self.opacity = max(0.0, min(1.0, float(opacity)))
            except (TypeError, ValueError):
                pass
        self._sync_sliders()
        self._request()

    def nudge(self, dx_mm: float, dy_mm: float) -> None:
        """Move the seal a little.  Right is right, down is down, always."""
        self.nudge_x = round(self.nudge_x + dx_mm, 2)
        self.nudge_y = round(self.nudge_y + dy_mm, 2)
        self._request()
        if self.on_change:
            self.on_change(self.nudge_x, self.nudge_y)

    def reset_nudge(self) -> None:
        """Go back to the plain corner/edge position."""
        self.nudge_x = self.nudge_y = 0.0
        self._request()
        if self.on_change:
            self.on_change(0.0, 0.0)

    def _count_pages(self) -> None:
        self.page_count = 0
        if not self.doc_path:
            return
        try:
            import pymupdf

            with pymupdf.open(self.doc_path) as document:
                self.page_count = document.page_count
        except Exception:
            self.page_count = 0

    # ---------------------------------------------------------------- drawing
    def show_message(self, text: str) -> None:
        """Replace the picture with a sentence, so the panel is never blank."""
        self.canvas.delete("all")
        self._photo = None
        self.canvas.create_text(px(self.PAGE_W) / 2, px(70), text=text,
                                width=px(self.PAGE_W) - px(24), fill=MUTED,
                                font=(FONT, CAPTION), justify="center")
        self.hint.configure(text="")
        self._position_text = ""

    def _request(self) -> None:
        """Redraw, collapsing a burst of changes into one repaint.

        The very first picture is drawn straight away instead of after the
        debounce: there is nothing to smooth out on the first paint, and
        waiting 90 ms just makes the panel look empty when a file is added.
        """
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except (ValueError, tk.TclError):
                pass
            self._after_id = None
        if self._photo is None:
            self._draw()
        else:
            self._after_id = self.after(90, self._draw)

    def destroy(self) -> None:
        """Cancel a debounced preview repaint before this widget is removed."""
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except (ValueError, tk.TclError):
                pass
            self._after_id = None
        super().destroy()

    def _draw(self) -> None:
        self._after_id = None
        if not self.doc_path or not self.page_count:
            self.show_message("Add a PDF and a seal image to see the result "
                              "here.")
            return
        if not self.seal_path or not Path(self.seal_path).is_file():
            self.show_message("Now choose your seal image. A transparent PNG "
                              "looks best.")
            return
        try:
            self._draw_page()
        except Exception:               # a preview must never break the tool
            self.show_message("This file could not be drawn here. The seal "
                              "will still be applied when you run.")

    def _peek_ratio(self) -> float:
        """Height / width of the page about to be drawn (A4 if unreadable).

        Read separately from the render so the picture can be scaled to fit
        *before* rasterising, instead of drawing at full size and only then
        discovering it does not fit.
        """
        if not self.doc_path:
            return 1.414
        try:
            import pymupdf

            with pymupdf.open(self.doc_path) as document:
                if not 0 <= self.page < document.page_count:
                    return 1.414
                rect = document[self.page].rect
                return (rect.height / rect.width) if rect.width else 1.414
        except Exception:
            return 1.414

    def _seal_aspect(self) -> float:
        try:
            from PIL import Image

            with Image.open(self.seal_path) as img:
                return (img.height / img.width) if img.width else 1.0
        except Exception:
            return 1.0

    def _draw_page(self) -> None:
        """Rasterise the page if needed, then composite the seal onto it.

        The page is only redrawn when the document, the page number or the
        panel width actually change - moving the seal just pastes the small
        image again, which is what keeps the arrows feeling instant.
        """
        from PIL import Image, ImageTk

        import pymupdf

        from .stamp import seal_rect

        # A withdrawn window reports a canvas width of 1, which would render a
        # 1-pixel preview, so fall back to the designed width until the panel
        # has been laid out for real.
        measured = self.canvas.winfo_width()
        width_px = measured if measured >= 40 else px(self.PAGE_W)
        # Cap the drawn size so a tall page cannot push the RUN button off the
        # bottom of the window.  The page ratio has to be known first, so peek
        # at it before deciding how wide to render.
        ratio = self._peek_ratio()
        cap_w, cap_h = px(self.PAGE_W), self.max_height
        if ratio > cap_h / max(cap_w, 1):
            width_px = int(cap_h / max(ratio, 0.01))
        else:
            width_px = min(int(width_px), cap_w)
        width_px = max(80, width_px)
        key = (str(self.doc_path), self.page, width_px)
        if key != self._base_key:
            with pymupdf.open(self.doc_path) as document:
                if not 0 <= self.page < document.page_count:
                    self.show_message("That page is not in this file.")
                    return
                page = document[self.page]
                self._page_ratio = ratio
                zoom = width_px / max(page.rect.width, 1)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                                         alpha=False)
                self._base = Image.frombytes("RGB", (pixmap.width, pixmap.height),
                                             bytes(pixmap.samples))
                self._page_points = (page.rect.width, page.rect.height)
            self._base_key = key
        base = self._base
        scale = base.width / max(self._page_points[0], 1)
        self._scale = scale

        with pymupdf.open(self.doc_path) as document:
            target = seal_rect(document[self.page], self.width_percent,
                               self._seal_aspect(), self.position, 12.0, 12.0,
                               self.nudge_x, self.nudge_y)

        picture = base.convert("RGBA")
        with Image.open(self.seal_path) as raw:
            seal = raw.convert("RGBA")
            seal = seal.resize(
                (max(1, int(target.width * scale)),
                 max(1, int(target.height * scale))), Image.LANCZOS)
            if self.opacity < 0.999:
                seal.putalpha(seal.getchannel("A").point(
                    lambda v: int(v * self.opacity)))
            picture.alpha_composite(seal, (int(target.x0 * scale),
                                           int(target.y0 * scale)))
        # master=self.canvas matters: the seal page lives in a window that can
        # be a different Tk interpreter from the default root (the tests build
        # two), and an image created on the wrong one does not exist as far as
        # this canvas is concerned.
        self._photo = ImageTk.PhotoImage(picture.convert("RGB"),
                                         master=self.canvas)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.configure(width=base.width, height=base.height)
        self._describe(target, base)

    def _describe(self, target, base) -> None:
        """Say where the seal is in words, so the picture is not the only clue."""
        from .util import mm2pt

        left = mm2pt(max(0.0, target.x0))
        bottom = mm2pt(max(0.0, base.height / self._scale - target.y1))
        where = self.NAMES.get(self.position, self.position)
        if self.nudge_x or self.nudge_y:
            # The nudge goes in the read-out too: without it the mm figures
            # describe the final spot but never say how far you pushed it.
            self.hint.configure(
                text=(f"Moved off the {where} by "
                      f"{self.nudge_x:+.0f}, {self.nudge_y:+.0f} mm - "
                      f"{left:.0f} mm from the left edge, "
                      f"{bottom:.0f} mm up from the bottom"))
        else:
            self.hint.configure(
                text=f"Fixed to the {where} - use the arrows to fine-tune")


class PageNumberPreview(tk.Frame):
    """Show the page-number settings on a sample page before saving them."""

    PAGE_W = 250
    PAGE_H = 300

    def __init__(self, master: tk.Misc, **_kwargs) -> None:
        super().__init__(master, bg=CARD)
        self.format = "{page}"
        self.position = "bottom-right"
        self.font_size = 9.0
        self.font = "helv"
        self.style = "arabic"
        self.start_number = 1
        self.enabled = True
        self.skip_first = 0
        self._photo = None
        self.canvas = tk.Canvas(self, bg=SURFACE, highlightthickness=px(2),
                                highlightbackground=BORDER,
                                width=px(self.PAGE_W), height=px(self.PAGE_H))
        self.canvas.pack()
        self.hint = tk.Label(self, text="", bg=CARD, fg=MUTED,
                             font=(FONT, CAPTION), wraplength=px(self.PAGE_W))
        self.hint.pack(pady=(px(6), 0))
        self.redraw()

    def set_values(self, *, format: str | None = None,
                   position: str | None = None, font_size: float | None = None,
                   enabled: bool | None = None,
                   skip_first: int | None = None,
                   start_number: int | None = None,
                   font: str | None = None,
                   font_file: str | None = None,
                   style: str | None = None) -> None:
        if format is not None:
            self.format = str(format)
        if position is not None:
            self.position = str(position)
        if font_size is not None:
            self.font_size = max(6.0, float(font_size))
        if enabled is not None:
            self.enabled = bool(enabled)
        if skip_first is not None:
            self.skip_first = max(0, int(skip_first))
        if start_number is not None:
            self.start_number = max(1, int(start_number))
        if font is not None:
            self.font = str(font)
        if font_file is not None:
            self.font_file = str(font_file or "").strip()
        if style is not None:
            self.style = str(style)
        self.redraw()

    def redraw(self) -> None:
        import pymupdf
        from PIL import Image, ImageTk

        from .pdf_ops import _label, anchor_rect, draw_text

        doc = pymupdf.open()
        page = doc.new_page(width=595.276, height=841.89)
        page.draw_rect(page.rect, color=(0.78, 0.82, 0.89), width=1)
        page.insert_text((42, 58), "COURT FILING", fontname="hebo",
                         fontsize=13, color=(0.07, 0.16, 0.29))
        for row in range(10):
            y = 108 + row * 43
            page.draw_line((42, y), (553, y), color=(0.86, 0.88, 0.92), width=0.7)
        page.insert_text((42, 790), "Sample document page", fontsize=9,
                         color=(0.36, 0.42, 0.51))

        if self.enabled:
            number = self.start_number + self.skip_first
            total = max(1, 20 - self.skip_first)
            try:
                text = self.format.format(
                    page=_label(number, self.style),
                    total=_label(total, self.style),
                    roman=_label(number, "ROMAN"),
                    # Same placeholders as the real stamper, so a layout that
                    # previews correctly here cannot fail on the finished file.
                    roman_total=_label(total, "ROMAN"),
                )
            except (KeyError, ValueError, IndexError):
                text = "Page 1 of 20"
            # A .ttf/.otf wins over the built-in name; register it into the
            # preview document so the render shows the real face.
            font_path = Path(getattr(self, "font_file", "") or "")
            if font_path.is_file() and font_path.suffix.lower() in (
                    ".ttf", ".otf", ".ttc"):
                used_font = "paginpreview"
                try:
                    page.insert_font(fontname=used_font, fontfile=str(font_path))
                    font_obj = pymupdf.Font(fontfile=str(font_path))
                except Exception:
                    used_font, font_obj = self.font, None
            else:
                used_font, font_obj = self.font, None
            if font_obj is not None:
                width = font_obj.text_length(text, fontsize=self.font_size) + 6
            else:
                width = pymupdf.get_text_length(text, fontname=used_font,
                                                fontsize=self.font_size) + 6
            height = max(self.font_size * 1.5, 14.0)
            rect = anchor_rect(page, width, height, self.position,
                               8 * 72 / 25.4, 4 * 72 / 25.4)
            draw_text(page, rect, text, font=used_font, font_size=self.font_size,
                      color=(0, 0, 0), align=1)
            face = font_path.name if font_obj is not None else {
                "helv": "Helvetica", "hebo": "Helvetica Bold",
                "tiro": "Times Roman", "tibo": "Times Bold",
                "cour": "Courier"}.get(used_font, used_font)
            hint = f"Preview: {text} - {face} - {self.position.replace('-', ' ')}"
        else:
            hint = "Preview: page numbers are turned off"

        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(
            px(self.PAGE_W) / page.rect.width,
            px(self.PAGE_W) / page.rect.width), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height),
                                bytes(pixmap.samples))
        self._photo = ImageTk.PhotoImage(image, master=self.canvas)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.configure(width=image.width, height=image.height)
        self.hint.configure(text=hint)
        doc.close()

class Chip(tk.Label):
    """A compact pill summarising a fact ("4 PDFs", "95% smaller").

    Colour is never the only signal: every chip also carries text, so it still
    reads correctly for colour-blind users and in a greyscale printout.
    """

    TONES = {"ok": (OK_SOFT, OK), "warn": (WARN_SOFT, WARN_TEXT),
             "error": (ERR_SOFT, ERR), "info": (ACCENT_SOFT, ACCENT),
             "muted": (SURFACE, MUTED)}

    def __init__(self, master: tk.Misc, text: str, tone: str = "muted",
                 **kw) -> None:
        fill, ink = self.TONES.get(tone, self.TONES["muted"])
        kw.setdefault("font", (FONT, CAPTION))
        kw.setdefault("padx", 8)
        kw.setdefault("pady", 3)
        super().__init__(master, text=text, bg=fill, fg=ink, **kw)
        self.tone = tone

    def set_tone(self, tone: str) -> None:
        """Restyle the chip."""
        fill, ink = self.TONES.get(tone, self.TONES["muted"])
        self.configure(bg=fill, fg=ink)
        self.tone = tone

    def set_text(self, text: str) -> None:
        """Change the wording without rebuilding the widget."""
        self.configure(text=text)


class ChipRow(tk.Frame):
    """A row of chips that can be repopulated in one call."""

    def __init__(self, master: tk.Misc, background: str = CARD, **kw) -> None:
        super().__init__(master, bg=background, **kw)
        self._background = background
        self._chips: list[Chip] = []

    def show(self, facts: Sequence[tuple[str, str]]) -> None:
        """Replace the contents with ``(text, tone)`` pairs."""
        for chip in self._chips:
            chip.destroy()
        self._chips = []
        for text, tone in facts:
            chip = Chip(self, text, tone)
            chip.pack(side="left", padx=(0, px(6)), pady=px(2))
            self._chips.append(chip)


# --------------------------------------------------------------------------- #
#  Button with an icon
# --------------------------------------------------------------------------- #
class IconButton(tk.Frame):
    """A flat button carrying a vector icon and a label.

    One primary action per screen is the rule; this widget makes the visual
    weight explicit (``primary=True``) so the RUN button stays the only bold
    thing on the page.
    """

    def __init__(self, master: tk.Misc, text: str, command: Callable[[], None],
                 *, icon: str = "", primary: bool = False,
                 background: str = CARD, surface: str = CARD) -> None:
        super().__init__(master, bg=background, cursor="hand2", takefocus=1)
        self.command = command
        self.primary = primary
        self._surface = surface
        self.enabled = True

        self.icon = Icon(self, icon or "check", 15,
                         "#ffffff" if primary else MUTED, background=surface)
        if icon:
            self.icon.pack(side="left", padx=(px(11), px(7)))
        self.label = tk.Label(self, text=text, bg=surface,
                              fg="#ffffff" if primary else TEXT,
                              font=(FONT, BODY, "bold" if primary else "normal"),
                              padx=px(11 if icon else 11), pady=px(7))
        self.label.pack(side="left", padx=(0 if icon else px(1), px(11)))
        for widget in (self, self.label, self.icon):
            widget.bind("<Button-1>", lambda _e: self._fire())
            widget.bind("<Return>", lambda _e: self._fire())
            widget.bind("<KP_Enter>", lambda _e: self._fire())
            widget.bind("<FocusIn>", lambda _e: self._paint())
            widget.bind("<FocusOut>", lambda _e: self._paint())
        self._paint()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the button and restyle it."""
        self.enabled = bool(enabled)
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()

    def set_text(self, text: str) -> None:
        self.label.configure(text=text)

    def _fire(self) -> None:
        if self.enabled and self.command:
            self.command()

    def _paint(self) -> None:
        if not self.enabled:
            fill, ink = BORDER, MUTED
        elif self.primary:
            fill, ink = ACCENT, "#ffffff"
        else:
            fill, ink = SURFACE, TEXT
        if self.focus_get() is self:
            self.configure(highlightthickness=px(2), highlightbackground=FOCUS,
                           highlightcolor=FOCUS)
        else:
            self.configure(highlightthickness=0)
        self.label.configure(bg=fill, fg=ink)
        self.icon.set_color(ink)
        self.icon.configure(background=fill)

