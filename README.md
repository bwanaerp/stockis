# GSCORE Dept Stock-Take Scan Desk

A mobile-first web app for scanning printed **department physical stock-take
sheets** on your phone — camera capture, CamScanner-style page cleanup,
handwritten count OCR against a known product checklist, a fast review
pass, and a one-tap `counts.csv` download shaped for GSCORE's populate
script.

This app never talks to GSCORE's database. It only reads a checklist CSV
in and writes a counts CSV out — you feed that file into your existing
populate script separately.

## What it does, in order

1. **Start a session** — upload the department's blank checklist CSV.
2. **Capture pages** — camera, photo library, or PDF, one printed sheet
   per page. Each page gets perspective-corrected, deskewed, and
   contrast-boosted automatically (see `imaging/process.py`).
3. **Line up each page** — tell the app the first checklist `line_no` on
   the page and how many rows it has; the app crops the "Counted" column
   for each of those rows and runs OCR on just that crop, not the whole
   page.
4. **Review** — a mobile-friendly list, filterable to "needs review",
   shows exactly the cropped handwriting next to the product name so you
   can confirm, correct, zero out (dash/X), or skip each line quickly.
5. **Export** — download `counts.csv` in the exact schema your populate
   script expects, plus an optional review report and a zip of the
   cleaned page images.

## Architecture at a glance

```
app.py              Flask routes: sessions, page upload, accept/align, review, export
checklist.py         Checklist CSV parsing + export schema + CSV writers
session_store.py     File-based ephemeral session storage (auto-expiring)
imaging/process.py   OpenCV: page-corner detection, perspective warp, deskew, enhance
ocr/                 Pluggable OCR backends behind one interface (ocr/base.py)
  tesseract_ocr.py    Free, offline, default. Weak on messy handwriting.
  cloud_google.py     Google Document AI (handwriting-capable). Needs credentials + $.
  cloud_textract.py   AWS Textract. Needs credentials + $.
  cloud_azure.py      Azure Document Intelligence. Needs credentials + $.
templates/, static/  Mobile-first HTML/CSS/vanilla JS frontend
```

Nothing here writes to a database. `session_store.py` is plain JSON files
per session under `SESSION_DIR`, purged after `SESSION_TTL_HOURS`. Swap it
for S3/Redis later if you need multi-instance hosting; the interface is
small on purpose.

## Deploy

This is a standard Flask app + Dockerfile, so any of Fly.io, Railway,
Render, or a plain VM will work. Streamlit was considered and rejected —
its camera widget isn't reliable enough for a true "snap a page" flow on
iOS Safari, which is why this is a thin Flask + hand-rolled mobile HTML
app instead (per the brief's own fallback guidance).

### Option A — Railway / Render (Docker-based hosts)
1. Push this folder to a git repo.
2. Create a new service from the repo; both platforms auto-detect the
   `Dockerfile`.
3. Set environment variables from `.env.example` in the host's dashboard
   (at minimum `APP_PASSWORD` and `SECRET_KEY`).
4. Deploy. Open the generated HTTPS URL on your iPhone.

### Option B — Fly.io
```bash
fly launch --no-deploy      # picks up the Dockerfile
fly secrets set APP_PASSWORD=... SECRET_KEY=...
fly deploy
```

### Option C — any Docker host
```bash
docker build -t gscore-scan .
docker run -p 8080:8080 --env-file .env gscore-scan
```

### Local dev (no Docker)
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# Tesseract must also be installed on the machine, e.g.:
#   macOS:  brew install tesseract
#   Ubuntu: sudo apt-get install tesseract-ocr
cp .env.example .env   # edit values
export $(cat .env | xargs)
python app.py
```

`smoke_test.py` exercises the full pipeline (create session → upload a
synthetic page → align/OCR → manual edit → blocked/allowed export → notes)
against a synthetic image with the Flask test client, no browser needed —
useful for confirming a fresh environment's Tesseract/OpenCV install is
working before you deploy: `python3 smoke_test.py`.

Then open `http://localhost:8080` — for camera capture testing you'll
need HTTPS or `localhost` specifically (iOS Safari requires a secure
context for `capture="environment"`); use a tunnel like `ngrok http 8080`
to test on an actual phone during development.

## Environment variables

See `.env.example` for the full list. The two you should always set in
production:
- `APP_PASSWORD` — shared password gate so scanned sheets stay internal.
  Leave blank only if the hosting URL itself is private.
- `SECRET_KEY` — random string for session cookie signing.

Everything else has a working default (`OCR_PROVIDER=tesseract`, 24-hour
session expiry, 25MB upload cap).

## Exporting a checklist CSV from GSCORE

This app expects a CSV with (at minimum) these columns, one row per
printed checklist line, in print order:

```
line_no,store_product_id,product_name,unit,category,system_eod_qty
```

If your GSCORE instance's department physical stock-take screen has an
"Export checklist" / "Export blank sheet" action, use that — it should
already be in `line_no` print order, which is what keeps page alignment
correct. If it only offers a print-ready PDF, ask whoever maintains your
GSCORE populate script for the underlying line list; it's the same data
the populate script already keys off `store_product_id` for. `system_eod_qty`
and `physical_qty` columns are optional on upload — the app ignores any
existing `physical_qty` values and fills them itself.

See `samples/checklist_sample.csv` for the expected shape and
`samples/counts_sample.csv` for what a completed export looks like
(blank boxes stay blank, dash/X becomes `0`, an illegible box gets an
`unclear` note instead of a guessed number, and — per the brief — a bar
sheet's numbers are stored exactly as written even when the printed unit
says "Each", since spirits/wine counts are often tots or glasses poured).

## OCR: accuracy and the human-review gate

Handwriting recognition on messy tally marks is genuinely hard, and this
app is built around that fact rather than pretending otherwise:

- **Tesseract (default)** is free and runs entirely offline, but it was
  designed for printed text; on handwritten digits its accuracy is
  noticeably lower than a cloud handwriting model, and its own confidence
  scores run optimistic (this app deliberately discounts them, see
  `ocr/tesseract_ocr.py`). Expect it to reliably catch clean, unambiguous
  digits and to correctly push messier ones into "amber"/"red" for human
  review — it is not meant to run unattended.
- **Google Document AI / AWS Textract / Azure Document Intelligence** all
  handle handwriting meaningfully better and are worth the per-page cost
  if volume justifies it. Each is a few lines of credentials away — set
  `OCR_PROVIDER` and the matching credentials in `.env` — see the
  docstring at the top of each `ocr/cloud_*.py` file for exact setup
  steps, and check the vendor's current pricing page before committing,
  since per-page rates change. All three add real network latency
  (roughly 200ms-3s per call depending on vendor and whether you batch),
  which is why each backend batches boxes per-page rather than per-line
  where the vendor's API supports it.
- **The review gate is mandatory, not optional.** Amber/red lines cannot
  be exported silently — `counts.csv` export is blocked with a 409 until
  either every line is resolved or you explicitly check "export with
  blanks" in the UI. Blank boxes are never turned into invented zeros.
- Row alignment (which pixels on the page correspond to which checklist
  line) is done by evenly dividing the page's printed row area given a
  header height and row count you confirm per page, not by trying to
  detect the sheet's ruling lines. This is simple and robust across sheet
  layouts, but if a property's sheet has an unusual header/footer size,
  use the "Adjust count-column position" control on the align screen.

## Non-goals (by design)

- No writes to GSCORE's MySQL/Railway database — CSV in, CSV out only.
- No approve / verify / set-as-opening actions.
- No Main Stores mode — department checklists only in this version,
  though `checklist.py` and the alignment logic don't assume anything
  department-specific, so a Main mode can reuse most of this.
- Never invents a product row from a margin note. Anything that doesn't
  match a checklist line goes in the free-text session notes
  (`session_notes.txt`, downloadable from the Export screen), or as an
  `ocr_note` on the nearest related line — never a new row.

## Deliverables in this repo

- `app.py`, `checklist.py`, `session_store.py`, `imaging/`, `ocr/` — app source
- `templates/`, `static/` — frontend
- `requirements.txt`, `Dockerfile` — dependencies + container build
- `.env.example` — configuration reference
- `samples/checklist_sample.csv` — example blank department checklist input
- `samples/counts_sample.csv` — example completed export output
- This README
