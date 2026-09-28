# 📄 QUALITY.md — output-quality engine (beating iLovePDF)

> Goal: the PDF we hand to the court must be **sharper and smaller** than what
> iLovePDF produces, in one click, with no upload.

## 1. Where we stand today (measured on this PC, 2026-09-28)

Source: `work\_quality_probe.py`, `work\_bilevel_test.py`, `work\_crop_compare.py`.

| Item | Current behaviour | Verdict |
|---|---|---|
| Scanned page (A4, text) | 300 dpi **JPEG q75**, 358 KB/page (`pdfocr_tobytes(compress=True)` in `ocr_engine._worker`) | readable, but JPEG ringing around fine lines; biggest cost in the file |
| Born-digital page | left as vector text | ✅ already better than iLovePDF, which can flatten |
| `compress` level 1 | structure cleanup + font subset | ✅ lossless |
| `compress` level 2 (default) | `rewrite_images(dpi 187 -> 150, q75)` — `_rewrite_images()` | 150 dpi is a **quality cut**, not a quality win |
| `compress` level 3 | Ghostscript, or without GS: 105 dpi + gray + q55 (`compress_pdf._attempt`) | last resort only |
| Seal / stamp | placed **after** compress in the pipeline, so it stays crisp | ✅ keep it this way |
| OCR | 300 dpi, `skip_text` mode, 1 worker per core-1 | ✅ good; can be better (see §3) |
| Metadata | inherited from the source PDF (author, printer, producer) | ❌ leaks internal names into a filing |

**Test that proves the opportunity** — same scanned page:

| Encoding | Size | Resolution |
|---|---|---|
| today: 300 dpi JPEG q75 | 358 KB | 300 dpi |
| **1-bit CCITT G4** | **41 KB** | **400 dpi** |
| 1-bit CCITT G4 | 61 KB | 600 dpi |

We currently pay 358 KB/page and still hand over the *worse* of the two.

## 2. Design — per-page adaptive compression (new module `court_ocr/optimize.py`)

iLovePDF applies one global setting to every page. We classify **every page**
and encode it the way a scanner would, which is the whole trick.

### 2.1 Page classes

| Class | Test | Encoding |
|---|---|---|
| `born_digital` | page has real text AND no image ≥ 60 % of the page | **untouched** (vector + fonts subset) |
| `text_scan` | raster page, coloured pixel share < 2 %, ≥ 85 % of pixels near white | **1-bit** at `text_dpi` (300–400) |
| `gray_scan` | raster page, coloured share < 2 %, not bimodal (stamp photos, shading) | **8-bit gray JPEG** q`gray_quality` |
| `mixed_scan` | coloured share ≥ 2 % (blue ink signature, red/blue seal, colour letterhead) | **RGB JPEG** q`photo_quality` at `photo_dpi` |

Rule: the seal/stamp XObject is **never** re-encoded (skip images smaller than
40 % of the page) — the stamp stays a crisp, beautifully placed image.

### 2.2 How the image is replaced (keeps the searchable text layer!)

`Page.replace_image(xref, stream=...)` swaps the pixel data **in place**: page
size, placement rectangle and the invisible OCR text layer all stay exactly as
they were. That is the safe way to do this — never rebuild the page from
scratch, and never touch `hidden_text` (it *is* our OCR layer).

### 2.3 Order inside the pipeline

```
merge → cover → ocr → OPTIMIZE → paginate → stamp → split → report
                       ^^^^^^^^  before page numbers + seal, so both stay crisp
```
`compress.engine` selects the path:
* `"adaptive"` (new default) → `optimize.optimize_pdf()`
* `"levels"` (old behaviour) → `pdf_ops.compress_pdf()` — kept as escape hatch

### 2.4 Quality presets (config `quality.preset`)

| Preset | text pages | colour pages | Use |
|---|---|---|---|
| `archival` | 400 dpi 1-bit | 300 dpi q92 | court record / long-term file copy |
| `high` | 400 dpi 1-bit | 250 dpi q88 | best everyday filing |
| **`balanced`** (default) | 300 dpi 1-bit | 200 dpi q85 | normal filings |
| `small` | 300 dpi 1-bit | 150 dpi q78, gray where possible | weak portal limit |
| `portal` | `balanced`, then escalate to `small` until `compress.target_size_mb` is met | | |

New config section (defaults live in `config.py`, so existing `config.json` files
keep working untouched):

```json
"quality": {
  "preset": "balanced",
  "text_dpi": 300,          "photo_dpi": 200,
  "photo_quality": 85,      "gray_quality": 80,
  "bitonal": true,          "colour_threshold_percent": 2.0,
  "scrub_metadata": true
}
```

### 2.5 Metadata scrub (privacy)

Keep `title`, `subject`, `keywords`, dates; blank `author`, `creator`, `producer`
and drop XMP (`set_xml_metadata("")`). **Never** use `Doc.scrub(hidden_text=True)`
— that deletes the OCR layer.

### 2.6 Reporting / QC

* `OptimizeResult` keeps one row per page: class, dpi, encoding, KB before/after
  → the pipeline CSV gains a real per-page trail.
* New `tools/quality_report.py <file.pdf>`: per-page class/dpi/encoding/size plus
  plain-English verdicts (searchable? seal on every page? page count? portal fit?)
  — a "check before upload" tool with no iLovePDF equivalent.

### 2.7 Acceptance criteria

1. `tools\selftest.py` still 12/12 (OCR text searchable, seal on 7/7, under 4.5 MB).
2. Scanned pages of the sample case come out **≤ 60 KB at ≥ 300 dpi** (today 358 KB).
3. Whole file ≤ 50 % of today's size with *equal or better* legibility on a 300 dpi
   visual crop (`work\_crop_compare.py`).
4. Search on a scanned page still finds `POSSESSION` (16 hits today).
5. `--set compress.engine=levels` reproduces the old behaviour (regression escape).

## 3. OCR accuracy upgrades (phase 2 — better text than iLovePDF)

1. Pre-process before OCR: autocontrast + **deskew** (Tesseract OSD angle) + light
   despeckle, only when the page is detected as skewed/dirty (`ocr.deskew` and
   `ocr.rotate_pages` exist in config but currently do nothing).
2. OCR faint scans at **400 dpi** (`ocr.oversample_dpi`) with a per-page retry when
   the first pass extracts suspiciously little text.
3. Install **Marathi/Hindi** tessdata (`mar`, `hin`) — Raigad filings are often
   Marathi; today only `eng` + `osd` are installed.
4. Legal word list for Tesseract (`user-words`: SARFAESI, 13(2), bank name, court
   name) so exactly the words that matter are correct.
5. Optional per-page text-confidence check (Tesseract TSV) → flag pages for a human
   look instead of silently shipping bad text.

## 4. Order of work

1. ✅ notes (this file)
2. `optimize.py` + config defaults + pipeline wiring + CLI `--quality`
3. `tools/quality_report.py` + new selftest checks
4. measure on `input\SAMPLE CASE`, compare crops, tune the presets
5. phase 2 (OCR accuracy), phase 3 (PDF/A, SHA-256 manifest, JBIG2 via Ghostscript)

