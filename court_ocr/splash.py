"""A short, good-looking loading screen for the desktop app.

Why this file exists
--------------------
Starting :mod:`court_ocr.app` is not instant.  Measured on a normal office PC,
:class:`court_ocr.app.App` spends about **1.9 seconds** building ~50 widgets and
asking Windows whether the OCR engine is installed - and all of that happens
*before* Tk is ever allowed to run its event loop.  During those seconds the
window already exists, so the user is staring at a blank, unresponsive rectangle
that Windows may even label "(Not Responding)".  It reads as a crash, and it was
reported as exactly that ("the loading looks broken, sometimes frozen").

The splash gives those seconds a face:

* it is one ``Toplevel`` with a dozen canvas items, so it appears in about a
  tenth of a second;
* it animates continuously, so the app never looks frozen even while the main
  thread is busy building;
* it says what is actually happening, in plain language;
* it hands over to a window that is *already fully built*, so the user never
  sees the half-assembled layout or the jump from the default geometry to the
  final geometry.

Design language
---------------
The wordmark's navy, a deep vertical gradient, one white card holding the brand
mark, and a single accent progress bar with a highlight that sweeps along it.
No rounded-corner trickery on ttk widgets (they cannot do it - the rounded
shapes are painted on a canvas instead), no emoji (they render at a different
size on every machine), and no dependency beyond Pillow, which the app already
uses for its header logo.

Safety
------
Every drawing call is defensive and :func:`make_splash` swallows all errors.  A
cosmetic screen must never be the reason a staff member cannot open their work:
if anything at all goes wrong, the app simply starts without a splash.
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path

from .ui_kit import ACCENT, FONT, px

#: Card geometry in *logical* pixels; :func:`court_ocr.ui_kit.px` scales them.
WIDTH = 430
HEIGHT = 268

#: Gradient endpoints - the wordmark's navy down to a friendlier blue.
TOP = "#071c3d"
BOTTOM = "#14417f"

TRACK = "#12305e"            # the unfilled part of the progress bar
TRACK_EDGE = "#1d4a8a"


def _lerp(start: str, end: str, ratio: float) -> str:
    """Blend two ``#rrggbb`` colours; ``ratio`` 0 -> start, 1 -> end."""
    ratio = 0.0 if ratio < 0 else (1.0 if ratio > 1 else ratio)
    out = ["#"]
    for index in (1, 3, 5):
        a = int(start[index:index + 2], 16)
        b = int(end[index:index + 2], 16)
        out.append(f"{int(round(a + (b - a) * ratio)):02x}")
    return "".join(out)


def _round_rect_points(x0: float, y0: float, x1: float, y1: float,
                       radius: float) -> list[float]:
    """Polygon points of a rounded rectangle that is ``len`` px wide or taller.

    Kept in one place so the static shapes drawn by :meth:`Splash._draw_progress`
    and the moving ones repositioned by :meth:`Splash._animation` always agree on
    the outline.  A ``smooth=True`` polygon through these points renders the
    corners; a rectangle would look blocky next to the rest of the palette.
    """
    if x1 - x0 < 2:
        x1 = x0 + 2
    radius = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    return [
        x0 + radius, y0, x1 - radius, y0, x1, y0, x1, y0 + radius,
        x1, y1 - radius, x1, y1, x1 - radius, y1,
        x0 + radius, y1, x0, y1, x0, y1 - radius,
        x0, y0 + radius, x0, y0,
    ]


def _round_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
                radius: float, **kw) -> int:
    """Draw a rounded rectangle and return its canvas id."""
    return canvas.create_polygon(
        _round_rect_points(x0, y0, x1, y1, radius), smooth=True, **kw)


def _brand_mark(master: tk.Misc, size: int) -> "tk.PhotoImage | None":
    """The wordmark's eye as a square ``PhotoImage``, or ``None`` if unreadable.

    ``master`` must be the window the picture is shown on: a ``PhotoImage``
    belongs to the Tk interpreter that created it, and handing it to a different
    one is what makes images disappear at random.
    """
    root = Path(__file__).resolve().parent.parent
    try:
        from PIL import Image, ImageTk
    except ImportError:
        return None
    for candidate in ("brand/kattyvision.png", "assets/logo.png",
                      "assets/logo_64.png"):
        path = root / candidate
        if not path.is_file():
            continue
        try:
            with Image.open(path) as raw:
                art = raw.convert("RGBA")
            # Trim the export margins so the mark fills its badge evenly.
            box = art.getchannel("A").point(
                lambda a: 255 if a > 10 else 0).getbbox()
            if box:
                art = art.crop(box)
            art.thumbnail((size, size), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(art, master=master)
        except Exception:
            continue
    return None


class Splash:
    """The loading card.  Cheap to build, safe to throw away.

    Typical use from :class:`court_ocr.app.App`::

        splash = make_splash(self)
        splash.step(0.15, "Reading your settings...")
        ...do something slow...
        splash.pump()                     # keeps the animation alive
        splash.close()                    # fades out, destroys itself

    ``step`` records progress and ``pump`` draws one frame *and* drains the Tk
    event queue - that is the whole trick.  The main thread can be busy for a
    second at a time and the bar still moves smoothly, because control is handed
    back to Tk between the phases of the build.
    """

    FRAME_MS = 16            # ~60 fps target for the self-scheduling heartbeat
    MIN_FRAME = 0.030        # never redraw the card more often than ~33 fps
    FADE_IN = 0.18           # seconds, so the card eases in instead of popping
    FADE_OUT = 0.20
    SWEEP = 1.05             # seconds for one pass of the highlight
    EASE = 0.22              # how fast the drawn bar chases the real value

    def __init__(self, master: tk.Misc) -> None:
        self.master = master
        self.ok = False
        self._target = 0.0
        self._shown = 0.0
        self._born = time.perf_counter()
        self._closed = False
        self._busy = False                # re-entrancy guard for pump()
        self._message = "Starting up..."
        self._photo: "tk.PhotoImage | None" = None
        self._alpha = -1.0
        # -1, not 0.0: it means "the window attribute has never been set yet".
        # The window is created at alpha 0 so the card can fade in, so the very
        # first frame MUST write the attribute even though its computed alpha is
        # ~0 - otherwise the card never becomes visible at all.
        self._last_pump = 0.0

        self.width, self.height = px(WIDTH), px(HEIGHT)

        self.window = tk.Toplevel(master)
        self.window.withdraw()                    # draw first, then reveal
        self.window.overrideredirect(True)        # no title bar: it is a card
        self.window.configure(background=TOP)
        try:
            self.window.attributes("-topmost", True)
            self.window.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(self.window, width=self.width,
                                height=self.height, highlightthickness=0,
                                borderwidth=0, background=TOP)
        self.canvas.pack(fill="both", expand=True)

        self._draw_background()
        self._draw_card()
        self._draw_words()
        self._draw_progress()
        self._place()

        self.ok = True
        try:
            self.window.deiconify()
            self.window.lift()
        except tk.TclError:
            self.ok = False
            return

        # First paint right now: the card has to be on screen *before* the slow
        # part of the build starts, otherwise it has solved nothing.
        self._animation()
        self.master.update_idletasks()


    # --------------------------------------------------------------- drawing
    def _draw_background(self) -> None:
        """A soft vertical gradient, painted one scanline at a time.

        A canvas has no gradient primitive.  ~270 one-pixel rectangles cost one
        cheap pass at start-up and never need repainting, unlike a generated
        ``PhotoImage``, which would have to be filled pixel by pixel.
        """
        steps = max(2, self.height)
        for row in range(steps):
            colour = _lerp(TOP, BOTTOM, row / (steps - 1))
            self.canvas.create_rectangle(
                0, row, self.width, row + 1, fill=colour, outline=colour)

        # A brighter hairline along the top edge, so the card reads as a lit
        # surface rather than a flat rectangle.
        self.canvas.create_rectangle(
            0, 0, self.width, max(1, px(1)),
            fill=_lerp(TOP, "#ffffff", 0.10), outline="")

    def _draw_card(self) -> None:
        """The soft badge that holds the brand mark."""
        size = px(64)
        left, top = px(30), px(34)
        # Rounded and very slightly off-white: a hard white square against a
        # gradient reads as a sticker, a rounded one reads as a lens.
        _round_rect(self.canvas, left, top, left + size, top + size, px(15),
                    fill="#f7fafe", outline="#b9cdea", width=1)
        self._photo = _brand_mark(self.window, int(size * 0.70))
        if self._photo is not None:
            self.canvas.create_image(left + size / 2, top + size / 2,
                                     image=self._photo)

    def _draw_words(self) -> None:
        """Wordmark, tagline, status line and signature, on one left grid."""
        text_left = px(30) + px(64) + px(18)
        self.canvas.create_text(text_left, px(46), anchor="nw",
                                text="KattyVision", fill="#ffffff",
                                font=(FONT, 21, "bold"))
        self.canvas.create_text(text_left, px(80), anchor="nw",
                                text="Less paperwork. More peace of mind.",
                                fill=_lerp(BOTTOM, "#ffffff", 0.74),
                                font=(FONT, 10))
        self.canvas.create_text(px(30), px(120), anchor="nw",
                                text=self._message,
                                fill=_lerp(BOTTOM, "#ffffff", 0.88),
                                font=(FONT, 10), tags="message")
        # The quiet line that fills the gap between the status and the bar.
        # It is not decoration: "nothing leaves this computer" is the single
        # thing a court-filing user most wants to be sure of, and the loading
        # screen is the one moment they are guaranteed to be looking.
        self.canvas.create_text(px(30), px(160), anchor="nw",
                                text="Works offline - your documents never "
                                     "leave this computer.",
                                fill=_lerp(BOTTOM, "#ffffff", 0.42),
                                font=(FONT, 9))
        self.canvas.create_text(self.width - px(30), self.height - px(18),
                                anchor="se",
                                text="Made with care by Kathirvel",
                                fill=_lerp(BOTTOM, "#ffffff", 0.28),
                                font=(FONT, 8))

    def _draw_progress(self) -> None:
        """The track, the fill and the highlight that sweeps along it."""
        span = self.width - px(60)
        height = px(7)
        left = px(30)
        top = self.height - px(60)
        self._bar = (left, top, left + span, top + height)
        radius = height / 2
        _round_rect(self.canvas, left, top, left + span, top + height, radius,
                    fill=TRACK, outline=TRACK_EDGE, width=1)
        self.fill_id = _round_rect(self.canvas, left, top, left + 2,
                                   top + height, radius,
                                   fill=_lerp(ACCENT, "#8fc4ff", 0.55),
                                   outline="")
        self.sheen_id = _round_rect(self.canvas, left, top, left + 2,
                                    top + height, radius,
                                    fill=_lerp(ACCENT, "#ffffff", 0.55),
                                    outline="")

    def _place(self) -> None:
        """Centre the card on the screen, a little above true centre."""
        try:
            screen_w = self.master.winfo_screenwidth()
            screen_h = self.master.winfo_screenheight()
        except tk.TclError:
            return
        x = max(0, (screen_w - self.width) // 2)
        y = max(0, int((screen_h - self.height) * 0.42))
        try:
            self.window.geometry(f"{self.width}x{self.height}+{x}+{y}")
        except tk.TclError:
            pass


    # ------------------------------------------------------------- animation
    def _tick(self) -> None:
        """Self-scheduling heartbeat, for the case where nobody is pumping.

        During start-up :meth:`pump` drives every frame, so this exists only so
        that a splash shown on its own still animates.  It is never allowed to
        raise into the caller.
        """
        if self._closed or not self.ok:
            return
        self._animation()
        try:
            self.window.after(self.FRAME_MS, self._tick)
        except tk.TclError:
            self.ok = False

    def _animation(self) -> None:
        """Draw one frame.  Deliberately cheap - it moves three shapes."""
        elapsed = time.perf_counter() - self._born

        # 1. Ease the bar towards its target, so a jump from 20 % to 80 % is
        #    drawn as a fast slide rather than a teleport.  Big steps that land
        #    instantly read as a glitch, which is exactly what we are here to
        #    avoid on a slow machine.
        self._shown += (self._target - self._shown) * self.EASE
        if abs(self._target - self._shown) < 0.002:
            self._shown = self._target

        left, top, right, bottom = self._bar
        radius = (bottom - top) / 2
        filled = max(radius * 2, (right - left) * self._shown)
        self.canvas.coords(self.fill_id,
                           *_round_rect_points(left, top, left + filled,
                                               bottom, radius))

        # 2. A highlight sweeps the whole track, forever.  Even while the real
        #    progress sits still (one slow step) the card still looks alive -
        #    that is what stops it reading as "frozen".
        span = right - left
        head = ((elapsed % self.SWEEP) / self.SWEEP) * (span + px(80)) - px(80)
        start, end = left + head, left + head + px(56)
        if end <= left or start >= right:
            self.canvas.itemconfigure(self.sheen_id, state="hidden")
        else:
            self.canvas.itemconfigure(self.sheen_id, state="normal")
            self.canvas.coords(self.sheen_id,
                               *_round_rect_points(max(left, start), top,
                                                   min(right, end), bottom,
                                                   radius))

        # 3. Fade in over the first frames instead of popping onto the screen.
        #    Setting a window attribute is a Win32 call, so it is only done when
        #    the value actually changes.
        alpha = elapsed / self.FADE_IN if elapsed < self.FADE_IN else 1.0
        if abs(alpha - self._alpha) > 0.02:
            self._alpha = alpha
            try:
                self.window.attributes("-alpha", alpha)
            except tk.TclError:
                pass

    # ------------------------------------------------------------------- API
    def step(self, fraction: float, message: str = "") -> None:
        """Record real progress (0..1) and what is happening right now."""
        self._target = max(0.0, min(1.0, float(fraction)))
        if message:
            self._message = message
        self.pump()

    def pump(self) -> None:
        """Draw a frame and flush it to the screen.  Safe to call in a loop.

        ``update_idletasks`` - *not* ``update`` - is the whole trick.  ``update``
        drains the entire event queue, which during start-up means re-running
        Tk's layout and redraw work for every half-built widget; measured, that
        cost more than the build it was decorating.  Idle tasks are just "redraw
        what changed", which is all the card needs, because the frame itself is
        drawn explicitly by :meth:`_animation` rather than by a timer.

        The redraw is also capped at ~33 frames a second: the callers pump in
        tight loops while they work, and redrawing faster than the eye can see
        only makes the wait longer.
        """
        if self._closed or not self.ok or self._busy:
            return
        now = time.perf_counter()
        if now - self._last_pump < self.MIN_FRAME:
            return
        self._last_pump = now
        self._busy = True
        try:
            self.canvas.itemconfigure("message", text=self._message)
            self._animation()
            self.master.update_idletasks()
        except tk.TclError:
            self.ok = False
        finally:
            self._busy = False

    def close(self) -> None:
        """Fade out, then remove the card.  Never raises.

        Short on purpose: it is the last thing between the user and their work,
        so six steps (~0.1 s) is enough to read as a dissolve without becoming a
        delay of its own.
        """
        if self._closed or not self.ok:
            self.destroy()
            return
        self._closed = True
        try:
            steps = 6
            for index in range(steps, -1, -1):
                self.window.attributes("-alpha", index / steps)
                self.master.update_idletasks()
                time.sleep(self.FADE_OUT / steps)
        except Exception:
            pass
        self.destroy()

    def destroy(self) -> None:
        """Remove the card immediately, without the fade."""
        self._closed = True
        self.ok = False
        try:
            self.window.destroy()
        except Exception:
            pass


class _NullSplash:
    """Stand-in used when a splash cannot be drawn (no display, no Tk, ...).

    It answers to the same ``step`` / ``pump`` / ``close`` calls, so the boot
    sequence in :mod:`court_ocr.app` needs no special cases of its own.
    """

    ok = False
    root = None

    def step(self, fraction: float, message: str = "") -> None:
        pass

    def pump(self) -> None:
        pass

    def close(self, *args, **kwargs) -> None:
        pass


def make_splash(master: tk.Misc) -> "Splash | _NullSplash":
    """Build a splash - or a harmless stub if anything at all goes wrong.

    Both results have the same interface, so callers never need a guard of their
    own.  A missing loading screen must never be the reason the app will not
    open.
    """
    try:
        return Splash(master)
    except Exception:
        return _NullSplash()

