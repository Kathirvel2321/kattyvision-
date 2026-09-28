# 🧠 BRAIN — Project Memory

> Read this first. It records **what was done, how, and why** so nobody has to
> reverse-engineer the workflow from scratch each session. Append a new entry
> at the top of the "Session log" for every change.

## Project in one paragraph
Local, offline Windows app that prepares bank/court case PDFs for the Raigad
court e-filing portal: merge → cover → OCR → compress → paginate → stamp →
split. Python 3.11 + tkinter/ttkbootstrap GUI (`court_ocr/app.py`, app name
"PDF Helper"), pipeline in `court_ocr/pipeline.py`, CLI in `court_ocr/cli.py`.
Installed via `INSTALL.ps1` (Desktop/Start-Menu shortcuts launching
`pythonw -m court_ocr.app`), removed via `UNINSTALL.ps1`.

## Key facts (do not re-discover these)
- Python lives at `C:\Users\ipvis\AppData\Local\Programs\Python\Python311\python.exe`
  (NOT on PATH; `python` alone fails in a fresh shell). No `py` launcher.
  `pythonw.exe` sits next to it — used by the shortcuts so no console shows.
- ttkbootstrap is NOT installed in that env; the app falls back to plain tkinter.
- Start-up crashes go to `logs\startup_error.log` (icon has no console).
- Shortcut internals: `Target` must be `pythonw.exe`, `Arguments` must be
  `-m court_ocr.app`. INSTALL.ps1's shortcut function deliberately avoids a
  parameter named `$Args` (PowerShell clobbers it).

## Architecture map
```
court_ocr/app.py       GUI (class App, ~1100 lines) + entry point with
                       crash-report fallback (_run → _report_startup_failure)
court_ocr/pipeline.py  runs the 8 steps, writes CSV report
court_ocr/pdf_ops.py   merge / cover / compress / paginate / split (PyMuPDF)
court_ocr/ocr_engine.py parallel Tesseract OCR, skip_text mode
court_ocr/stamp.py     seal + date text
court_ocr/config.py    Config.load / cfg.get / cfg.resolve / --set overrides
INSTALL.ps1 / UNINSTALL.ps1 / SETUP.ps1    install, uninstall, deps
tools/selftest.py, tools/make_sample_pdf.py
```

## Session log
### 2026-09-28 — OCR quality + speed measured on this machine (court-safety proof)
**Question asked:** "will OCR give correct output for court, never incomplete or blurry,
and how long does a 300-page PDF take?"

**Measured with `tools/ocr_quality_speed.py` (real Tesseract, real scanned pages):**

| scan type | key words read | pages kept | size kept | failures |
|---|---|---|---|---|
| clean 200 dpi | **31/31 (100 %)** | 3/3 | same | none |
| faded photocopy | **31/31 (100 %)** | 3/3 | same | none |
| noisy scan | **31/31 (100 %)** | 3/3 | same | none |

**Not blurry - it gets sharper:** a 200 dpi scan comes in at 1654x2338 px and
comes out of OCR as **2480x3509 px (300 dpi)** - the text layer is built from a
finer render than the scan.  The visible page image is copied through, so the
appearance cannot soften.

**300-page timing (this PC, 8 cores):** 0.72 s/page with the process pool
(~3.6 min for 300 scanned pages); 1.41 s/page single-threaded.  A mixed filing
is much faster because `skip_text` (the default) copies typed pages through
untouched - only real scans pay the cost.

**Court-safety proof: `tools/ocr_safety_check.py`, all pass.**  Non-negotiable
properties now locked down by tests: no page ever lost (count in == count out,
order kept); a born-digital page is **passed through, never re-rendered** (its
exact original text survives, it is counted in `pages_passed_through`, not
`pages_ocrd`); a failed chunk degrades to a **copy of the original pages**
(`out.insert_pdf` from the source) instead of vanishing; and the source PDF is
never modified - OCR always writes a new `_OCR.pdf` next to it.

### 2026-09-28 — Full KattyVision branding + "what next?" output flow
**Requested:** use the logos in `brand/` everywhere (including the taskbar icon),
production-grade look, sign the app "Kathirvel", and after a run offer
"download" vs "go to the next step" instead of one download button.

**Changes:**
- `tools/make_brand_icon.py` (new): builds `assets/app.ico` (16-256 px) +
  `logo.png`/`logo_64.png` from `brand/kattyvision.png` - the real brand mark,
  never recoloured. This replaced the old generic document-drawing icon.
- `INSTALL.ps1`/`UNINSTALL.ps1`: app name is now "KattyVision" (shortcuts,
  Start-Menu folder, pinned-taskbar cleanup, old "PDF Helper" entries removed too).
- `app.py`: mutex renamed to `KattyVision.SingleInstance.Mutex`; docstrings
  updated; `load_icon` prefers `assets/app.ico` then the brand PNGs.
- **Next-step flow**: `TOOL_HANDOFF` table defines what logically follows each
  tool (merge→ocr→compress→paginate→stamp→split).  New `App._hand_off_to_tool()`
  loads a finished result straight into another tool page (files loaded, sub-tab
  selected, "press RUN" status).  New `App.build_handoff_row()` +
  `ToolPage._show_next_steps()` build a "What next? / Use this result in:" bar
  with *Open the PDF* (primary) + one button per follow-up tool + Preview +
  Open folder.  `ToolPage._finish` NO LONGER auto-opens Explorer after a run.
- Automation result card: "Download PDF"→"Open the PDF", "Next step" button
  replaced by the same hand-off row (`self.result_next`, row 5).
- Tool file lists gained **↑ Move up / ↓ Move down** buttons
  (`ToolPage._move_selected`), because merge/split order matters.
- Theme polish: notebook tabs selected=card/active=surface with accent text;
  progress bars restyled as a solid 9 px ribbon; combobox padding.
- `tools/brand_flow_check.py` (new): 12 checks over all of the above.

**Verified:** brand_flow_check 12/12; ui_tests, selftest, split_tests,
compress_tests (after updating its producer-name check to KattyVision) all pass.

### 2026-09-28 — Compress reviewed end to end: the engine is sound, the Compress *tool page* is not
**Tested with `tools/compress_tests.py` (26 checks, all pass)** on a realistic mixed
case (1 born-digital + 1 colour + 4 scans at 200 dpi, 1.40 MB).

Proven about the adaptive engine (`optimize.py`, what the Automation tab runs):
- 1.40 -> 0.56 MB (-60 %); `'small'` preset -71 %.
- Per page: scans 253 KB -> 44 KB, colour 405 -> 333 KB, born-digital untouched.
- **Sharper, not blurrier**: a 200 dpi scan comes back 1654x2339 -> **2481x3508 (300 dpi)**
  1-bit.  Resolution goes UP while the bytes go down.
- **The searchable text layer survives**: after OCR, `search_for("POSSESSION")` gives 104
  hits before compressing and **104 after**, 8,976 characters of hidden text kept.
- **OCR quality is unchanged**: OCR read of the original vs the compressed file matches
  **100.0 %** (8,981 characters), no word lost.  This is the check that matters - a
  smaller file that the court can still read.
- The bank seal is never re-encoded (900x900, 18 KB before and after), so it stays crisp.
- A second pass never makes the file worse (0.56 -> 0.56 MB).
- Size targets are honest: reachable target -> `ok=True`; impossible one -> `ok=False`
  with "still above the ... target" in the detail instead of pretending.
- Metadata: author blanked, producer/creator become "PDF Helper 1.0.0 (adaptive optimizer)"
  (QUALITY.md section 2.5 says "blank" - the code renames instead; the privacy goal is met,
  the wording should be corrected).
- Speed: 0.28 s per page, **single-threaded** - this is now the slowest step on a big
  scanned filing (~30 s for 24 pages).  Parallelising it is the biggest speed win left.

**The one real problem (now FIXED):** the **PDF Tools -> Compress page** called the old
`pdf_ops.compress_pdf` levels engine, not the adaptive one.  On the same file:

| engine | saving |
|---|---|
| level 1 (lossless) | 1 % |
| level 2 (default, "downsample to 150 dpi") | 1 % |
| level 3 (strongest, no Ghostscript) | 35 % |
| **adaptive balanced** | **60 %** |
| adaptive small | 71 % |

So a user who compressed a scanned filing from the Compress page got almost nothing,
while the same file run through the Automation tab lost 60-95 %.  **Changed:**
`_tool_compress` now calls `optimize.optimize_pdf` (preset + target), the tool page's
"Strength 1/2/3" dropdown became a **Quality** dropdown (archival / high / balanced /
small) with a matching tooltip, and a missing size target is reported as a plain sentence
instead of a fake failure.  The old engine is still reachable from Settings -> "Which
shrinking method" (`compress.engine = levels`, the only path that uses Ghostscript).
`compress_tests.py` section 9 now fails if the tool ever goes back to the weak engine.

**Verified after the change:** `tools/compress_tests.py` 30/30, `tools/split_tests.py` 22/22,
`tools/selftest.py` 12/12, `tools/ui_tests.py` all pass.

### 2026-09-28 — Split rewritten: the page limit is now obeyed exactly, MB rule is opt-in
**Symptom (reported on the build machine):** "I want 300 pages split into 40 pages
per upload, but it does not split into the number of pages I want - there was an MB
gate which broke my flow."

**Two separate causes, both in `pdf_ops.split_pdf`:**
1. `_best_cut()` returns the **middle** of the allowed window, so a 40-page limit
   produced 20-page parts: 100 pages @42 -> `[21, 21, 21, 37]` (4 uploads instead of 3),
   300 pages @40 -> 15 files. It looked like the limit was "not working".
2. `split.max_size_mb` defaulted to 4.5 and the Split tool page defaulted to
   `mode="both"`, so the size rule re-cut every part *again* and kept halving until
   a single page was still too big, then wrote "exceeds ... even as one page" into
   `skipped` (shown to the user as a warning). The portal MB box on the main window
   also wrote `split.max_size_mb` on every stepper move, so the gate could not be
   switched off without going into the settings.

**Change:**
- New `pdf_ops._part_end(first, last, max_pages, starts)`: returns the **last page of
  this part**, filled to the limit. The loop now does `(first, end)` + `(end+1, last)`,
  so the off-by-one is impossible to reintroduce. Bookmark boundaries still win, but
  only inside the limit (so "never cut a document in half" can shorten a part - it can
  no longer halve every part).
- The file-size rule now only acts when `mode` is `size`/`both`. With `mode="pages"`
  an over-size part is reported in the new `SplitResult.over_size` and **kept as it is**.
- Defaults: `split.mode="pages"`, `split.max_size_mb=0` (both in `config.py` DEFAULTS
  and `config.json`). The Split tool page defaults to `pages` / `0 MB`, with labels
  that say so. The main window's MB box now only writes `compress.target_size_mb`
  (it must never re-arm the split gate) - also fixed in `_sync_limits_to_config`,
  `_apply_ui_to_config`, `_make_variables`, `_after_advanced` and the result card.
- The split step's log/detail line now states the rule and every part's page count:
  `8 part(s) by page limit (40/file): 40, 40, 40, 40, 40, 40, 40, 20 page(s)`.
- New `tools/split_tests.py` (22 checks) locks all of this down, including the
  pipeline step and the GUI tool runner.

**Verified:** 300 pages @40 -> 8 parts `[40x7, 20]` in 0.1 s, with or without a tiny MB
limit; 41 @40 -> `[40, 1]`; 40 @40 -> 1 part; `tools/selftest.py` 12/12; `tools/ui_tests.py`
all pass; whole-code probe 95/95.

**Watch-out:** with "never cut a document in half" on, 50-page exhibits under a 40-page
limit *must* give short parts (`[40, 10, 40, 10, ...]`) - that is the option working, not
a bug. Turn it off in the Split tool if the court does not care.

### 2026-09-28 — Phase-2 makeover implemented (Preview tab + drag & drop)
See section 9 of `brain/UI_MAKEOVER.md` for the item table.
- New **👀 Preview** tab (`PreviewPanel` in `court_ocr/app.py`): thumbnails in lazy 20-page
  batches, big page view, page navigation, per-page facts (born-digital/scan · mm · KB),
  "⚡ Latest result" opens the newest finished filing, PNG render cache in
  `work/preview_cache/`.
- **Drag & drop**: `tkinterdnd2` optional; loaded via `TkinterDnD._require`, targets bound
  with `tk.call("tkdnd::drop_target", ...)` — CRITICAL: the mixin methods
  (`drop_target_register`) only exist on tkinterdnd2's own Tk subclass, NOT on plain
  tk.Tk/ttkbootstrap roots; calling the tkdnd package directly works everywhere.
  Files→tool lists, folders→"add all usable files inside", folder→Automation input.
- Result card gained a "👀 Preview" button. `requirements.txt` + installed env updated.
- Verified with simulated tkdnd drops (file, folder) and a real preview of the sample filing.

### 2026-09-28 — UX makeover: modern look, less user work, delight feedback
**Requested:** modern (not old-method) look, reduce user work as much as possible, trust
feeling, "game-play" enjoyment, still production-grade.

**Changes (court_ocr/app.py + config.py):**
- Hero header: dark-blue band, big white title, "✓ Ready to work" green pill badge.
- Emoji tabs: 🚀 Automation / 🧰 PDF Tools / ⚙️ Settings; per-tool emoji in the tool
  sub-tabs (📚 merge, 🔍 OCR, 📦 compress, 🔢 numbers, 🏷️ seal, ✂️ split).
- Tool pages: new "Add ALL from a folder..." button (uses pdf_ops.collect_inputs with the
  configured extensions, dedupes), animated indeterminate progress bar shown while running,
  ⏳/✨/❌ status text with saved-size summary.
- `App.celebrate()`: Windows chime (winsound.MessageBeep) + 6-step flashing of the hero
  badge on every successful run (automation and tools).
- Automation: "Recent ▾" menu in step 1 (last 6 folders stored in config `ui.recent_folders`,
  persisted), folders remembered on pick and on successful run, case name auto-filled from
  the folder name.
- config.py DEFAULTS: added `ui.recent_folders: []`.

**Verified:** app constructs (3 tabs, hero badge present), bulk-add resolves 5 sample files,
celebrate() runs. One bug caught during verify: `_add_folder` originally called
`pdf_ops.collect_inputs(chosen)` without the required `extensions` argument - fixed to pass
`cfg.get("merge.file_extensions")`.
### 2026-09-28 — 150% display scaling + dead mouse wheel fixed (UI)
**Symptom:** window looked dated/soft, wheel did nothing, and the bottom of the
Automation tab (RUN button, progress bar, step list) could not be reached.
**Root cause 1:** the app never asked to be DPI aware. This PC runs at 150%
(`AppliedDPI=144`, screen 1920x1080), so the hard-coded `1020x780` window became
1530x1170 *physical* pixels - taller than the screen - and Windows
bitmap-stretched the whole thing (blurry text). Fixed with a new
`enable_dpi_awareness()` (per-monitor v2, `LOGPIXELSX` -> module-level
`UI_SCALE`), called at the very top of `main()` before Tk creates a window, plus
`App._fit_to_screen()` which scales and clamps the size to the screen, centres
the window and sets `tk scaling`. Replaces the hard-coded `geometry()`/
`minsize()` in `App.__init__` and in `_toggle_details()`.
**Root cause 2:** `ScrollFrame` only bound `<MouseWheel>` between the inner
frame's `<Enter>`/`<Leave>`, but Windows sends wheel events to the widget under
the pointer - so a wheel over any child (label, entry, button) never reached the
canvas. Now `bind_all("<MouseWheel>", self._wheel, add="+")` with a
`winfo_containing` hit test, a "nothing to scroll" short-circuit and a
pass-through for widgets that scroll themselves (log box, file list).
**Verified:** `work\_test_ui_fixes.py` -> scale 1.50, window `1530x990+195+30`
fits the 1920x1080 screen, wheel over a child TLabel scrolls 0.00 -> 0.03, wheel
on another tab leaves the page alone; `tools\selftest.py` still 12/12.
**Also:** `ttkbootstrap` added to requirements.txt and to SETUP.ps1's pip step -
the app silently fell back to plain tkinter (dated look) when it was missing.
**Measured for the next step (quality):** page 5 of the sample comes out as
300 dpi JPEG q75 = 358 KB; the same page as 1-bit CCITT G4 is 41 KB at 400 dpi
and 61 KB even at 600 dpi (`work\_bilevel_test.py`) - i.e. better resolution at
~1/9 of the size, so per-page adaptive compression can beat iLovePDF.

### 2026-09-28 — App redesigned: tabbed UI, per-task tool pages, renamed to "PDF Helper"
**Requested:** app felt like one cramped non-scrolling page with marketing wording; wanted
separate pages per task where each task can be run alone (e.g. OCR only) and results saved
right there; keep the full automation page too; more customization.

**Changes (court_ocr/app.py):**
- Renamed APP_NAME to "PDF Helper" (APP_ID "PDFHelper.CourtFiling.1"); INSTALL.ps1/UNINSTALL.ps1
  updated to the new shortcut names (uninstaller also removes the old "Court Filing Automation"
  shortcuts).
- `App._build_layout` is now a `ttk.Notebook` with 3 tabs: **Automation** (old step logic,
  inside a `ScrollFrame`), **PDF Tools**, **Settings**.
- `ScrollFrame` class: canvas + scrollbar + mouse-wheel, used for tall pages.
- `TOOL_SPECS` + `ToolPage`: six standalone pages - Merge, OCR, Compress, Page numbers,
  Bank seal, Split. Each: multi-file picker (listbox, add/remove/clear), "Save results in"
  (defaults to the first file's folder), per-tool option widgets (bool/int/float/choice/text/file
  built from a spec tuple), RUN, and "Open folder" enabled on completion. Results are saved
  next to the input files as `<name>_OCR/_compressed/_numbered/_sealed.pdf`, split outputs as
  `<name>_PartNN.pdf`.
- Tool runners at module level (`_tool_merge`, `_tool_ocr`, ...) call pdf_ops/ocr_engine/stamp
  directly, in a background thread; completion arrives via the events queue as
  `("tool_done", key, outputs, warnings)` / `("tool_error", ...)` handled in `_drain_events`.
- Shared log: `log_line` writes to the automation log box AND the tools-tab "Activity" box.
- Removed marketing wording ("runs on this PC - nothing is uploaded - no subscription",
  "Nothing is uploaded anywhere" in Help). Help dialog rewritten for the tabs.
- Settings tab: opens the existing AdvancedDialog + link to the settings file + Ghostscript note.

**Verified:** app constructs with 3 tabs and 6 tool pages; `_tool_paginate` ran on a sample PDF
and produced `<name>_numbered.pdf` in place. Note the sample-case folder then contained the
expected 4 files only.

**Watch-outs:** tool runner option values arrive as strings/bools; runners cast them.
Custom stamp page list uses the label mapping ("1,3,5-9") literally.
### 2026-09-28 — Fixed invisible-window bug + incomplete uninstall
**Symptoms reported:** double-clicking the icon showed the app in the taskbar
but nothing on screen; UNINSTALL.ps1 seemed to "not work".

**Root cause 1:** `court_ocr/app.py`, `App.__init__` line ~225 passed
`alpha=0.0` to the ttkbootstrap Window — the window was created **100%
transparent**. Taskbar entry exists, screen shows nothing.
**Fix:** changed to `alpha=1.0` and added `_ensure_visible()` (deiconify +
lift + focus_force + `-alpha 1.0`) scheduled 150 ms after start as a safety
net against any remembered off-screen/transparent state.

**Root cause 2:** UNINSTALL.ps1 only deleted the Desktop/Start-Menu `.lnk`
files. (a) A taskbar-**pinned** shortcut lives in
`%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\`
and was never touched. (b) The (invisible) app was still running as
`pythonw.exe`, holding handles.
**Fix:** UNINSTALL.ps1 now kills any `pythonw.exe` whose command line matches
`court_ocr` before deleting, and also removes the pinned-taskbar shortcut.

**Verified:** app constructs in normal state, geometry `1060x701`, `_ensure_visible()` OK.

### Template for future entries
```
### YYYY-MM-DD — <one-line title>
Symptom: ...
Root cause: ...
Change: <files + lines>
Verified: how
```

Everything from `brain/UI_MAKEOVER.md` phase 1 is now built and verified — see section 8 of
that file for the item-by-item table. Key code points in `court_ocr/app.py`:
`JobCancelled`, `Tooltip` + `TIP_TEXT`, `flash_window`, `enforce_single_instance`
(mutex name `PDFHelper.SingleInstance.Mutex`), App: `cancel_flag`/`run_started_at`,
`_tick_run`, `_cancel`, `_cancelled`, `_open_first_pdf`, `_copy_log`, `_add_files_current_tool`,
`_on_close` now saves `ui.geometry` + `ui.tab`. Automation cancel works by raising
`JobCancelled` inside the pipeline progress callback. The result card replaced the
"Finished" messagebox. Run verified end-to-end on the sample case (7.4 s, 8 pages, 0.25 MB).
