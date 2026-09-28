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

## 7. Rules for whoever builds it

1. Everything must run **offline**; no new heavy dependency (`tkinterdnd2` is the
   only optional one and the app must work without it).
2. New widgets go inside the existing `ScrollFrame` pattern so tall content can
   never be cut off again.
3. Never re-introduce fixed pixel geometry — use `_fit_to_screen()`.
4. Window screenshots (before/after) go into `brain/` as proof of the change.
