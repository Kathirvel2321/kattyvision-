# 🎨 UI_MAKEOVER.md — desktop app redesign ("feel good" pass)

> Goal: a window that looks like a modern desktop tool (and stays fast, offline,
> plain tkinter/ttkbootstrap — no new heavy dependencies).

## 1. What is wrong today (confirmed by measurement)

| Symptom | Root cause | Status |
|---|---|---|
| Text looked soft/"old" | process was **not DPI aware**; Windows bitmap-stretched everything on the 150 % display | ✅ **fixed** — `enable_dpi_awareness()` + `tk scaling` (`app.py`) |
| Bottom of the window (RUN, progress, step list) unreachable | hard-coded `1020x780` = **1530×1170 physical px on a 1080-screen**, and `_toggle_details` grew it further | ✅ **fixed** — `App._fit_to_screen()` clamps + centres |
| Mouse wheel did nothing | wheel bound only between the inner frame's `<Enter>`/`<Leave>`; Windows sends wheel events to the widget under the pointer | ✅ **fixed** — `bind_all` + `winfo_containing` hit test |
| Window could look like plain tkinter | `ttkbootstrap` was not in `requirements.txt` / `SETUP.ps1` | ✅ **fixed** — declared + installed |
| Still to do | preview, dark mode, drag & drop, English-only | ⬜ phases 2–3 below |

## 2. Design language (phase 1 — the "feels modern" pass)

* **Palette**: keep the navy accent, add soft surfaces — window `#f4f6fb`,
  cards `#ffffff` with a 1 px `#dfe5f0` border, accent `#0b2a5b`, success
  `#1a7f45`, warn `#b26a00`, error `#c0392b`, muted text `#5d6b82`.
* **Typography**: Segoe UI; screen title 20 pt bold, section heads 11 pt bold,
  body 10 pt, hints 9 pt muted — all in **points** so `tk scaling` keeps them crisp.
* **Spacing**: 16 px page padding, 12–14 px inside cards, 8 px between rows;
  today the layout is tight and reads as "engineered", not designed.
* **Components**: section card = bordered `Frame` with a bold caption row instead
  of `LabelFrame` (ttk cannot round corners; use spacing + a hairline border
  rather than faking it).
* **Buttons**: exactly one primary action per screen (navy, 12 pt bold, generous
  padding); secondary actions flat/outline; destructive ones red + confirmed.
* **State colours** on step rows: waiting grey `[ ]`, running amber `›››`,
  done green `✔`, needs-a-look orange `!` (today only `[ ]` and words).
* **Icons**: keep `assets/logo_64.png`; add 16 px state glyphs (✔ ✖ ⟳ 📂 🔍) from
  the existing `tools/make_icon.py` pipeline — no new dependency.

## 3. Screens & interactions

### 3.1 Automation page (the main one)
1. **Header**: logo + "PDF Helper" + a one-line live status ("Ready" /
   "Working… step 3 of 6").
2. **Case card**: case name, documents folder, save-to, `Check the folder` →
   report as chips ("4 PDFs · 3 scans · 1 encrypted") instead of one grey line.
3. **Preset card**: 4 radio cards with a one-line description each (already close);
   the "portal accepts at most …" row becomes its own card with **court presets**
   (Raigad e-filing 4.5 MB / 42 pages, generic 2 MB / 30 pages).
4. **Run card**: big RUN, live **progress bar + elapsed + ETA**, **Cancel**,
   per-step list with icons and notes, and (new) a **result card** afterwards:
   `7 pages · 1.2 MB → 0.32 MB (−73 %) · seal ✔ · searchable ✔ · fits portal ✔`
   with **Open PDF**, **Open folder**, **Copy log** buttons.
5. **Advanced details** toggle stays, but the log box gets a small dark header bar
   with "Copy" and "Open log file".

### 3.2 PDF Tools tab (6 tools) — same pattern plus:
* File list becomes a card with **reorder (↑ ↓) and remove (✖)** buttons —
  order matters when merging.
* **Drag & drop** files onto the list (`tkinterdnd2` optional; without it keep the
  current Add button and say so in one muted line, never fail).
* Each tool remembers its own last options (`ui.tool.<key>` in `config.json`).

### 3.3 Settings
* New **Quality** section: preset chooser (`archival` / `high` / `balanced` /
  `small` / `portal`) with a live estimate ("scanned page ≈ 40 KB at 400 dpi").
* New **Appearance** section: light/dark toggle (ttkbootstrap `flatly` / `darkly`),
  remembered as `ui.theme`.
* Everything else stays in the Advanced dialog, with the same visual language.

### 3.4 NEW: Preview panel (the "wow" feature, 100 % local)
* Result view: **page thumbnail strip**; click a thumbnail → large preview
  (PyMuPDF render, cached under `work\`).
* **Before/after compare slider** per page: the same page from the merged source
  and from the final file at equal zoom → "did compression hurt my scan?"
  answered visually, inside the app.
* Under the preview, that page's facts: class (text scan / colour / born-digital),
  dpi, encoding, KB — the same data `tools/quality_report.py` prints.

## 4. Polish backlog (small, high feel-per-effort)

* Empty states with a friendly line + a button ("No files yet — Add PDFs, or try
  the practice case").
* Friendly validation instead of tracebacks ("A case name cannot contain \ / :").
* Keyboard: `Ctrl+O` add files, `F5` check folder, `Enter` run, `Esc` close dialog,
  `F1` help; tooltips on every option.
* Remember window position/size and the last tab (`ui.geometry`, `ui.tab`), always
  clamped by `_fit_to_screen()`.
* Taskbar progress (`ITaskbarList3` via ctypes) during long OCR runs — free polish.
* Flash the taskbar button when a long run finishes (`FlashWindowEx`).
* Single-instance guard (named mutex): a second double-click focuses the existing
  window instead of starting a second copy.

## 5. Accessibility & language

* Mnemonics, sane tab order, contrast-safe colours, text that scales with DPI
  (points, not pixels).
* Optional Marathi UI strings (`ui.language` = `en` / `mr`) as a plain dictionary —
  after the quality work, no framework needed.
* No marketing wording anywhere; labels stay short and literal, errors say what to
  do next.

## 6. Phases

| Phase | Content | Why first |
|---|---|---|
| **1** | result card, progress + ETA + Cancel, icon/colour polish, empty states, tooltips, geometry memory, single instance | biggest "looks finished" jump, least risk |
| **2** | preview panel + before/after compare + per-page facts | makes it feel like a real PDF application |
| **3** | dark mode, drag & drop, Quality/Appearance sections, taskbar progress, Marathi | polish |

## 8. Build log — phase 1 COMPLETE (2026-09-28)

| Item from phase 1 | Status |
|---|---|
| Result card (chips: files · MB · seconds · per-step ✔/⚠) + Open PDF / Open folder / Copy log | ✅ `_build_step3` + `_finish` — replaced the old popup messagebox |
| Progress + elapsed + **Cancel** | ✅ Cancel button on the run card and every tool page; `JobCancelled` raised from the progress callback; `"cancelled"`/`"tool_cancelled"` events → `_cancelled()` handlers; "12s elapsed" ticker via `_tick_run()` |
| State-colour glyphs | ✅ step rows now `[›]` amber running, `[✔]` green done, `[✖]` red failed |
| Empty states | ✅ every tool file list starts with "No files yet — use 'Add PDFs / images...' or 'Add ALL from a folder...'." |
| Tooltips | ✅ `Tooltip` class + `TIP_TEXT` plain-language map attached to every tool option label (hover ½ s) |
| Geometry + last-tab memory | ✅ `ui.geometry` / `ui.tab` saved in `_on_close`, restored (regex-validated) in `App.__init__` |
| Single-instance guard | ✅ named mutex in `main()`; second double-click restores + focuses the running window |
| Keyboard | ✅ F1 help · F5 re-check folder · Ctrl+O add files on the open tool page |
| Taskbar flash | ✅ `flash_window()` (FlashWindowEx) fired from `celebrate()` |

**Verified:** real pipeline run (5 docs, OCR + compress + stamp) → `finished: True`, result card
mapped with chips, Open PDF enabled, glyphs `[✔]`/`[✖]` confirmed by code-point check, cancel
path responds (`_cancel()` sets the flag, status shows "Cancelling...").

**Remaining (phase 2+):** Marathi strings.

## 9. Build log — phase 2 COMPLETE (2026-09-28)

| Item from phase 2 | Status |
|---|---|
| **Preview panel** (new 👀 Preview tab) | ✅ `PreviewPanel`: thumbnail strip (lazy 20-page batches so big PDFs never freeze the UI), big page view, ◀/▶ + "Page X of Y", per-page facts line (born-digital/scan/mixed · mm size · KB share), "📂 Open PDF...", "⚡ Latest result", "Open externally". Renders via PyMuPDF, PNGs cached under `work/preview_cache/` (hash of path+mtime+page+zoom) so re-opening is instant. |
| **Drag & drop** | ✅ optional `tkinterdnd2` loaded with `TkinterDnD._require(root)`; drop targets registered via `tk.call("tkdnd::drop_target", ...)` so it works on plain tk.Tk/ttkbootstrap roots (the mixin methods only exist on tkinterdnd2's own Tk subclass — found and worked around in testing). Files dropped onto any PDF Tools page fill the list (`parse_dropped` handles tkdnd's `{path with spaces}` syntax); a dropped folder adds every usable file inside it. A folder dropped on the Automation tab becomes the documents folder + auto-fills the case name. Graceful without the engine (one muted log line). |
| Result card → Preview | ✅ new "👀 Preview" button on the result card jumps to the tab with the finished filing open. |
| Dependency | ✅ `tkinterdnd2>=0.4.0` added to requirements.txt and installed in the Python 3.11 env. |

**Verified:** real end-to-end — preview opened the sample filing (8 pages, thumbnails
rendered, facts line correct, page navigation works); simulated tkdnd drops confirmed:
file drop → "1 file(s) added by drag & drop", folder drop on Automation → input + case name
filled; `tkdnd::drop_target info` confirms registrations.

**Remaining (phase 3):** dark mode, before/after compare slider, taskbar progress,
Marathi strings.

## 7. Start-up pass: the loading card, the taskbar, and the scale (2026-09-29)

Complaints received: *"the loading looks broken and sometimes looks frozen"*,
*"my logo should be visible in the taskbar too"*, and *"the project name was
kattyvision but it still shows court filing"*. All three were real. Everything
below was **measured on the development machine**, not guessed.

### 7.1 Why the loading screen looked broken

| Evidence | Number |
|---|---|
| `App()` total - the period the main thread is busy | **1.86 s** |
| of which `_build_layout()` | 0.54 s |
| of which `_load_from_config()` | 0.74 s |
| of which `_report_tools()` → `diagnose()` (runs `tesseract --version` as a subprocess, on the UI thread) | 0.14 s |
| time to a *usable* window | 2.9 s |

`App.__init__` builds every widget and asks Windows about the OCR engine
**before Tk is allowed to run its event loop**, and the window is painted at
Tk's default geometry until `_fit_to_screen()` runs near the very end. So the
user saw, in order: a blank window → nothing happening for ~2 s (Windows paints
"(Not Responding)" past 5 s on a slower PC) → a jump to the real size. That is
what "broken" and "frozen" mean here; nothing had actually crashed.

### 7.2 The fix

* **`court_ocr/splash.py` (new)** - a borderless loading card: the wordmark's
  navy as a painted vertical gradient, the brand mark on a soft rounded badge,
  the name, the tagline, a live status line, the offline reassurance line, and a
  progress bar whose fill eases towards the real value while a highlight sweeps
  the whole track forever. Fade in, fade out, centred slightly above middle.
  * Measured cost: **~220 ms** to appear - it must be on screen *before* the
    slow part starts, or it has solved nothing.
  * The sweep is the important part. Progress that pauses still looks alive,
    which is the difference between "loading" and "frozen".
  * `update_idletasks()`, never `update()`: draining the whole queue during
    start-up re-runs Tk layout for every half-built widget and cost *more than
    the build it was decorating*. Redraws are also capped at ~33 fps.
  * `make_splash()` swallows every error and returns `_NullSplash` (same
    interface). A cosmetic screen must never be why the app will not open.
* **`App.__init__` is now a sequence of boot phases.** `withdraw()` the window,
  show the card, `splash.step()` + `splash.pump()` between the four tabs and
  between the six tool pages, then `deiconify()` at the final geometry. The
  half-built window is now **never visible at all** - no blank rectangle, no
  size jump.
* **`_report_tools()` → `_check_tools_async()`.** `diagnose()` moved to a
  worker thread; the result arrives through the existing `events` queue and is
  applied by `_process_events()` on the UI thread (Tk widgets may only be
  touched from the main thread). Start-up gives it a **bounded 0.9 s** so the
  usual fast case is already applied before the window appears - a slow machine
  is never made to wait for it.
* `_process_events()` split out of `_drain_events()` so start-up can flush the
  queue directly.

Result: card on screen at **0.2-0.6 s**, finished window at ~2.2 s, and the
process is never blocked on a subprocess.

### 7.3 Why the taskbar said "Court Filing"

Windows names a taskbar button after **the shortcut that launched the process**.
This machine had **three** shortcuts for the identical command
(`C:\Python314\pythonw.exe -m court_ocr.app`):

```
Court Filing Automation.lnk   <- old install
PDF Helper.lnk                <- older install
KattyVision.lnk               <- current
```

plus `Court Filing - Documents.lnk` on the Desktop, and `APP_ID` was itself
`"KattyVision.CourtFiling.1"`. Whichever one was clicked decided the name.

* `APP_ID` → **`KattyVision.Desktop.1`** - the identity string no longer carries
  a working name.
* `set_taskbar_identity()` moved into `main()`, **before any window exists**.
  Windows fixes a process's taskbar identity when its *first* window appears;
  calling it from `App.__init__` (i.e. after `super().__init__()`) was too late,
  so the button was grouped under `pythonw.exe` and showed the Python icon.
* `INSTALL.ps1` now stamps **`PKEY_AppUserModel_ID` (`KattyVision.Desktop.1`)** on
  the Desktop and Start Menu shortcuts through `IPropertyStore` - the documented
  way to own a taskbar identity. `WScript.Shell` has no property for it, which
  is why no shortcut ever carried one.
* `INSTALL.ps1` **deletes the stale shortcuts** (Desktop, Start Menu *and* the
  pinned-to-taskbar folder) and the `Court Filing Automation` / `PDF Helper`
  Start Menu folders. This is the part that actually makes the rename stick.
* `INSTALL.ps1` built `assets/app.ico` with **`tools/make_icon.py`** - the *old
  generic document-and-seal glyph* - on a fresh machine. `load_icon()` prefers
  `app.ico`, and `app.ico` is the single file Windows takes the taskbar, window
  and shortcut icon from, so a new install showed the wrong picture while the
  app header showed the real brand mark. Now it uses **`make_brand_icon.py`**.
* Documents folder link renamed `Court Filing - Documents` → `KattyVision - Documents`;
  `UNINSTALL.ps1` updated to cover the new name.
* `Run Court OCR.bat` → **`KattyVision.bat`** (its `title` command was putting
  *Court Filing Automation* in the taskbar too). The old file is kept as a
  three-line forwarder so an existing shortcut still works.
* Console titles in `Install App.bat` and `SETUP.ps1` aligned with the brand.

### 7.4 Two more bugs found while measuring

* **`ui_kit.SCALE` was never set.** It is documented as *"app.py overwrites this
  once the real display scale is known"* - it never did, so `SCALE` stayed `1.0`
  while `tk scaling` and the window size both used the real `1.5`. On this
  machine (150 %) every font was 1.5x but every padding stayed 1x, which is what
  made the layout look cramped and broken. `ui_kit.set_scale()` now exists and is
  called before the first widget is created (font metrics are frozen at creation,
  so setting it later is not enough).
  Proof: `brain/screenshot_before_hiDPI.png` vs `brain/screenshot_after_hiDPI.png`.
* **The remembered window geometry was restored unclamped.** This machine had
  `ui.geometry = "1920x974+322+35"` - Tk records a *maximised* window's
  rectangle, so a 1920-wide window was being restored 322 px to the right of a
  1920-wide screen: **the right-hand third of the interface off-screen on every
  launch.** `_restore_geometry()` now clamps the size and position to the
  current screen, refuses anything that looks maximised, and `_on_close()` no
  longer saves a size while the window is zoomed.

### 7.5 Checks

`tools/splash_tests.py` (new, 27 checks): the card appears and is mapped, is
cheap, animates while the caller is busy, closes cleanly and twice safely,
falls back to a no-op stub if it cannot be drawn, the OCR check is off the UI
thread, the scale is correct, and a remembered geometry cannot reopen off-screen.

All suites re-run green: `selftest` 12/12 - `ui_tests` - `compress` 32/32 -
`split` 22/22 - `ocr_safety_check` - `brand_flow_check` - `brand_controls_check`
- `splash_tests` 27/27.

## 8. Rules for whoever builds it

1. Everything must run **offline**; no new heavy dependency (`tkinterdnd2` is the
   only optional one and the app must work without it).
2. New widgets go inside the existing `ScrollFrame` pattern so tall content can
   never be cut off again.
3. Never re-introduce fixed pixel geometry — use `_fit_to_screen()`, and any
   geometry that comes back from storage must go through `_restore_geometry()`.
4. Window screenshots (before/after) go into `brain/` as proof of the change.
5. **Never do slow work in `App.__init__` without pumping the loading card.**
   If a step is added to the boot, it gets a `splash.step()` before it.
6. Keep the name in one place: `APP_NAME` for the window, `APP_ID` for the
   taskbar, `$AppName` in `INSTALL.ps1`. Do not reintroduce working names
   ("Court Filing…", "PDF Helper…") into any of them.
