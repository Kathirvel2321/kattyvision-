

















# Court Filing Automation (SARFAESI / Raigad court e-filing)

Replaces the manual prepare-and-upload routine with **one local program**.
Everything runs on the office PC: **no upload, no iLovePDF premium, no waiting,
zero cost**, and the bank's confidential documents never leave the machine.

| Step in the old manual workflow | Old cost | This program |
|---|---|---|
| Merge all documents into one PDF | manual | `merge` |
| Compress before upload | manual | `compress` |
| PDF -> OCR (iLovePDF premium nag, 30+ min) | 30+ min | `ocr` - seconds, free |
| Compress again (OCR output got bigger) | manual | `compress` (chases the portal limit) |
| Pagination ("Page X of Y") | manual | `paginate` |
| Government bank seal/stamp | manual | `stamp` |
| Split into portal-sized parts | manual | `split` |
| - | **1 hour+ per case** | **~10-60 seconds** |

Measured on this machine (8 CPU cores, no Ghostscript installed yet):

```
4 documents, 8 pages, 4 of them pure scans (no text layer)
merge       0.2 s
cover       0.1 s
ocr         5.4 s   (300 DPI, 4 pages actually OCRed, 4 skipped - already digital)
compress    0.3 s   4.19 MB -> 1.19 MB   (72% smaller)
paginate    0.1 s
stamp       0.3 s
split       0.1 s
----------------------------------------
TOTAL       6-8 s   -> 8-page searchable, numbered, sealed PDF
```

---

## 1. Install (once, ~2 minutes)

```powershell
powershell -ExecutionPolicy Bypass -File .\SETUP.ps1
```

* `SETUP.ps1` installs **Tesseract OCR** (free, Apache-2.0) - the engine that
  removes the iLovePDF subscription - plus the two Python packages
  (`pymupdf`, `pillow`).
* Add `-WithGhostscript` if you want the strongest possible compression
  (optional, free, ~65 MB).
* Finish with a built-in check:

```powershell
python -m court_ocr.cli --doctor
```

---

## 2. Use it

### Easy way (for staff)

1. Put the case documents in `input\<CASE NAME>\`
   (PDF, PNG, JPG or TIFF - sub-folders are fine, order is natural so
   `Exhibit 2` comes before `Exhibit 10`).
2. Double-click **`Run Court OCR.bat`**.
3. Tick the steps, press **RUN**, wait for the log to say `FINISHED`.
4. Finished files land in `output\<CASE NAME>\`.

### Command line (for batch/multiple cases)

```powershell
# full pipeline, with an index/cover page
python -m court_ocr.cli "input\MY CASE" -c "S.A. 245/2026" --cover

# portal allows 30 pages and 4 MB per upload
python -m court_ocr.cli "input\MY CASE" -c "S.A. 245/2026" --max-pages 30 --max-mb 4

# only OCR and compress, no stamp/split
python -m court_ocr.cli "input\MY CASE" --steps merge,ocr,compress

# any config value can be overridden on the fly
python -m court_ocr.cli "input\MY CASE" --set stamp.pages=first --set ocr.enabled=false

# check the machine, or create throwaway practice documents
python -m court_ocr.cli --doctor
python tools\make_sample_pdf.py
```

Exit code is `0` when every step succeeded, `1` when something needs a look,
`2` on a hard failure - handy for scheduled tasks.

---

## 3. What each step really does

| Step | Engine | Notes |
|---|---|---|
| `merge` | PyMuPDF | Natural sort, one bookmark per source document, images converted to A4 pages, rotated pages "baked" so stamps/numbering always land correctly |
| `cover` | PyMuPDF | Optional index page: court, case number, bank, list of documents with page ranges |
| `ocr` | **Tesseract**, driven by PyMuPDF | Only pages without real text are OCRed (`ocr.mode = skip_text`), so born-digital pleadings are untouched. Runs in parallel worker processes |
| `compress` | PyMuPDF (`rewrite_images`, `subset_fonts`) + optional Ghostscript | Escalates 1 -> 2 -> 3 until it is under the limit |
| `paginate` | PyMuPDF | `Page X of Y`, any corner, roman/alpha support, can skip the cover page |
| `stamp` | PyMuPDF + Pillow | Bank seal image (transparency, opacity, white-background removal) and/or a text block with the date |
| `split` | PyMuPDF | Fills every part up to `split.max_pages_per_file` (300 pages at 40 = 8 parts, not 15 of 20). Splits by file size **only** if you ask for it; never cuts a document in the middle when bookmark boundaries are available |

---

## 4. The settings that matter most (`config.json`)

| Setting | Meaning | Default |
|---|---|---|
| `split.max_pages_per_file` | Portal's page limit per upload - **this is what decides the split** | `50` |
| `split.max_size_mb` | File-size limit for splitting. `0` = never split by size (default). Only matters when `split.mode` is `size` or `both`. Compression chases the portal limit on its own | `0` |
| `split.mode` | `pages` (default) / `size` / `both` - `pages` gives exactly the number of parts you asked for | `pages` |
| `ocr.mode` | `skip_text` (only scans) / `force` (every page) / `off` | `skip_text` |
| `ocr.oversample_dpi` | Higher = more accurate, slower. 300 is a good balance | `300` |
| `ocr.jobs` | Parallel OCR workers, `0` = all cores minus one | `0` |
| `compress.level` | `1` lossless, `2` downsample images, `3` strongest (Ghostscript) | `2` |
| `pagination.format` | Any text with `{page}` and `{total}` | `Page {page} of {total}` |
| `stamp.pages` | `all`, `first`, `last`, `odd`, `even` or `1,3,5-9` | `all` |
| `stamp.position` | `bottom-right`, `top-left`, ... | `bottom-right` |
| `stamp.remove_white` | Key out a white paper background on a scanned seal | `false` |

Anything can be overridden per run with `--set key=value`, so the config file is
a starting point, not a constraint.

---

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `Tesseract OCR engine not found` | Run `SETUP.ps1`, or set `paths.tesseract_exe` in `config.json` |
| OCR makes poor text | Raise `ocr.oversample_dpi` to 400; make sure scans are straight |
| File still too big | `--max-mb 2`, or install Ghostscript (`SETUP.ps1 -WithGhostscript`) and use `--set compress.level=3` |
| A part is split in the middle of a document | Raise `split.max_pages_per_file`; splitting already prefers bookmark boundaries |
| Password-protected PDF | Remove the password first - the program refuses to guess it and reports it in the log |
| Nothing happens on double-click | Open `logs\startup_error.log` - a start-up crash is written there. Still nothing? Right-click the icon > Properties and check that **Target** is `pythonw.exe` and **Arguments** reads `-m court_ocr.app`; re-run `INSTALL.ps1` to repair an icon that lost them |
| "Nothing happens" and no `startup_error.log` either | Python is missing or too old: install Python 3.10+ with "Add python.exe to PATH", then run `SETUP.ps1` |

Log files are written to `logs\run_YYYYMMDD_HHMMSS.log` for every run, and a
per-case CSV report (`<CASE>_Final_report.csv`) lands next to the output files.
If the app cannot open at all, the reason is written to `logs\startup_error.log`
and shown in a small dialog - the icon starts the app without a console window,
so that file is the only place to look.

---

## 6. Good to know

* **Privacy** - nothing is uploaded anywhere; Tesseract and PyMuPDF run locally,
  so confidential bank documents stay on the office machine.
* **Cost** - all components are free: Tesseract (Apache-2.0), PyMuPDF (AGPL,
  fine for internal use), Pillow (MIT), Ghostscript (AGPL, optional).
  Note the AGPL terms if this software itself is ever distributed or offered as
  a network service to third parties.
* **The seal** - the program ships with a *placeholder* seal so you can rehearse
  the whole flow. Replace `assets\stamp.png` with the bank's real transparent
  PNG before filing.
* **Always verify before uploading** - check the page count, the "Page X of Y"
  footer, the seal position and the OCR text on a couple of scanned pages.
  The per-case CSV report lists every step with sizes and timings.

---

## 7. Project layout

```
config.json               all settings (safe to edit)
SETUP.ps1                 one-time installer (Python packages + Tesseract)
INSTALL.ps1               creates the Desktop / Start Menu icons
UNINSTALL.ps1             removes those icons again (nothing else is touched)
Run Court OCR.bat         double-click launcher for staff
requirements.txt          two Python packages
assets/                   app icon, logo, placeholder bank seal
court_ocr/
    app.py                desktop app:  python -m court_ocr.app
    cli.py                command line: python -m court_ocr.cli
    gui.py                old alias - still opens the desktop app
    pipeline.py           runs the 8 steps and writes the report
    pdf_ops.py            merge / cover / compress / paginate / split
    ocr_engine.py         parallel local OCR via Tesseract
    stamp.py              bank seal + text stamp
    toolcheck.py          finds Tesseract / Ghostscript
    config.py             config loading, merging, templates
    util.py               small helpers (units, natural sort, safe names)
    logging_setup.py      console + file logging, step timers
tools/make_sample_pdf.py  creates a fake case for practice
input/<CASE>/             drop the documents here
output/<CASE>/            finished files + CSV report
work/<CASE>/              intermediate files (auto-cleaned)
logs/                     one log per run
```

