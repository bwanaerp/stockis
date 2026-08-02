import io
import os
from datetime import date

import cv2
import numpy as np
from flask import (
    Flask, request, jsonify, session as flask_session,
    send_file, render_template, abort, Response
)

import config
import checklist as checklist_mod
import session_store
from imaging import process as imaging
from ocr import get_ocr_provider

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = int(config.MAX_UPLOAD_MB * 1024 * 1024)

_ocr = None


def ocr_provider():
    global _ocr
    if _ocr is None:
        _ocr = get_ocr_provider()
    return _ocr


# ----------------------------------------------------------------------
# Auth (simple shared password; skipped entirely if APP_PASSWORD unset)
# ----------------------------------------------------------------------
@app.before_request
def require_auth():
    if not config.APP_PASSWORD:
        return
    if request.path in ("/api/login", "/health") or request.path.startswith("/static"):
        return
    if flask_session.get("authed"):
        return
    if request.path == "/" :
        return  # index page itself renders the password prompt
    abort(401)


@app.post("/api/login")
def login():
    password = (request.json or {}).get("password", "")
    if password == config.APP_PASSWORD:
        flask_session["authed"] = True
        flask_session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Incorrect password"}), 401


@app.get("/health")
def health():
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------
@app.get("/")
def index():
    return render_template(
        "index.html",
        auth_required=bool(config.APP_PASSWORD),
        already_authed=flask_session.get("authed", False),
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
MAX_DIM = 2200  # cap long edge so slow-wifi uploads / processing stay fast


def _decode_image(file_storage):
    data = np.frombuffer(file_storage.read(), np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image.")
    h, w = img.shape[:2]
    long_edge = max(h, w)
    if long_edge > MAX_DIM:
        scale = MAX_DIM / long_edge
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    return img


def _pdf_to_images(file_bytes):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(file_bytes)
    images = []
    for page in pdf:
        bitmap = page.render(scale=200 / 72)  # ~200 DPI
        pil_img = bitmap.to_pil().convert("RGB")
        arr = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        h, w = arr.shape[:2]
        long_edge = max(h, w)
        if long_edge > MAX_DIM:
            scale = MAX_DIM / long_edge
            arr = cv2.resize(arr, (int(w * scale), int(h * scale)))
        images.append(arr)
    return images


def _confidence_status(confidence, has_value):
    if not has_value:
        return "ocr_red"
    if confidence >= config.CONFIDENCE_GREEN:
        return "ocr_green"
    if confidence >= config.CONFIDENCE_AMBER:
        return "ocr_amber"
    return "ocr_red"


def _save_jpg(path, image_bgr, quality=85):
    ok, buf = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("Could not encode image.")
    with open(path, "wb") as f:
        f.write(buf.tobytes())


# ----------------------------------------------------------------------
# Session lifecycle
# ----------------------------------------------------------------------
@app.post("/api/sessions")
def create_session():
    session_store.purge_expired()

    if "checklist_csv" not in request.files:
        return jsonify({"error": "checklist_csv file is required"}), 400

    try:
        checklist_lines = checklist_mod.parse_checklist_csv(request.files["checklist_csv"].read())
    except checklist_mod.ChecklistError as e:
        return jsonify({"error": str(e)}), 400

    meta = {
        "property_id": request.form.get("property_id", "").strip(),
        "department": request.form.get("department", "").strip() or "department",
        "stock_take_date": request.form.get("stock_take_date", "").strip() or date.today().isoformat(),
        "stock_take_id": request.form.get("stock_take_id", "").strip(),
        "enhance_mode": request.form.get("enhance_mode", "color"),
    }

    sid = session_store.new_session_id()
    sess = session_store.get_session(sid)
    line_states = checklist_mod.build_initial_line_states(checklist_lines)
    sess.create(checklist_lines, line_states, meta)

    return jsonify({"session_id": sid, "meta": meta, "line_count": len(checklist_lines)})


@app.get("/api/sessions/<sid>")
def get_session_state(sid):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404
    sess.touch()
    return jsonify({
        "session_id": sid,
        "meta": sess.meta(),
        "checklist": sess.checklist(),
        "line_states": sess.line_states(),
        "pages": sess.pages(),
    })


# ----------------------------------------------------------------------
# Page capture / processing
# ----------------------------------------------------------------------
@app.post("/api/sessions/<sid>/pages")
def upload_page(sid):
    """Accepts one image OR one multi-page PDF. Runs the CamScanner-style
    pipeline on each resulting page and stores raw + processed images.
    Does NOT run OCR yet — that happens on /accept, once the operator
    confirms first_line_no / row_count for the page."""
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404

    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400

    f = request.files["file"]
    filename = (f.filename or "").lower()
    enhance_mode = request.form.get("enhance_mode", sess.meta().get("enhance_mode", "color"))

    try:
        if filename.endswith(".pdf"):
            images = _pdf_to_images(f.read())
        else:
            images = [_decode_image(f)]
    except Exception as e:
        return jsonify({"error": f"Could not read upload: {e}"}), 400

    pages = sess.pages()
    created = []
    next_order = max([p["order"] for p in pages], default=-1) + 1

    for img in images:
        page_id = session_store.new_session_id()
        corners = imaging.find_page_corners(img)
        processed = imaging.process_page(img, corners=corners, enhance_mode=enhance_mode)

        _save_jpg(sess.page_image_path(page_id, "raw"), img)
        _save_jpg(sess.page_image_path(page_id, "processed"), processed)

        page_entry = {
            "id": page_id,
            "order": next_order,
            "corners_detected": corners is not None,
            "enhance_mode": enhance_mode,
            "first_line_no": None,   # set on /accept
            "row_count": None,       # set on /accept
            "status": "pending_accept",
        }
        pages.append(page_entry)
        created.append(page_entry)
        next_order += 1

    sess.save_pages(pages)
    return jsonify({"pages": created})


@app.post("/api/sessions/<sid>/pages/<pid>/reprocess")
def reprocess_page(sid, pid):
    """Re-run the enhancement pipeline, e.g. after the user drags corners
    manually or switches color/BW mode, without re-uploading the photo."""
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404

    raw_path = sess.page_image_path(pid, "raw")
    if not os.path.exists(raw_path):
        return jsonify({"error": "Page not found"}), 404

    body = request.json or {}
    corners = body.get("corners")  # optional [[x,y]x4] from manual drag, in raw-image pixel coords
    enhance_mode = body.get("enhance_mode", "color")

    img = cv2.imread(raw_path)
    processed = imaging.process_page(img, corners=corners, enhance_mode=enhance_mode)
    _save_jpg(sess.page_image_path(pid, "processed"), processed)

    pages = sess.pages()
    for p in pages:
        if p["id"] == pid:
            p["enhance_mode"] = enhance_mode
            p["corners_detected"] = corners is not None or p.get("corners_detected", False)
    sess.save_pages(pages)

    return jsonify({"ok": True})


@app.delete("/api/sessions/<sid>/pages/<pid>")
def delete_page(sid, pid):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404
    pages = [p for p in sess.pages() if p["id"] != pid]
    sess.save_pages(pages)
    for variant in ("raw", "processed"):
        path = sess.page_image_path(pid, variant)
        if os.path.exists(path):
            os.remove(path)
    return jsonify({"ok": True})


@app.post("/api/sessions/<sid>/pages/reorder")
def reorder_pages(sid):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404
    order = (request.json or {}).get("page_ids", [])
    pages = {p["id"]: p for p in sess.pages()}
    reordered = []
    for i, pid in enumerate(order):
        if pid in pages:
            pages[pid]["order"] = i
            reordered.append(pages[pid])
    # keep any pages not included in `order` at the end, stable
    remaining = [p for p in pages.values() if p["id"] not in order]
    reordered.extend(remaining)
    sess.save_pages(reordered)
    return jsonify({"ok": True})


@app.get("/api/sessions/<sid>/pages/<pid>/image")
def get_page_image(sid, pid):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404
    variant = request.args.get("variant", "processed")
    if variant not in ("raw", "processed"):
        return jsonify({"error": "invalid variant"}), 400
    path = sess.page_image_path(pid, variant)
    if not os.path.exists(path):
        return jsonify({"error": "Image not found"}), 404
    return send_file(path, mimetype="image/jpeg")


# ----------------------------------------------------------------------
# Accept a page -> align rows to checklist lines -> OCR the count column
# ----------------------------------------------------------------------
@app.post("/api/sessions/<sid>/pages/<pid>/accept")
def accept_page(sid, pid):
    """
    Body:
      first_line_no (int, required) - checklist line_no of this page's first row
      row_count (int, required)     - number of product rows printed on this page
      header_frac (float, optional) - fraction of page height before row 1 starts (default 0.12)
      footer_frac (float, optional) - fraction of page height after the last row (default 0.04)
      column_left_frac / column_right_frac (float, optional) - Counted column bounds
    Runs OCR on each row's count box and writes results into line_states.
    """
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404

    body = request.json or {}
    try:
        first_line_no = int(body["first_line_no"])
        row_count = int(body["row_count"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "first_line_no and row_count are required integers"}), 400

    if row_count <= 0:
        return jsonify({"error": "row_count must be positive"}), 400

    processed_path = sess.page_image_path(pid, "processed")
    if not os.path.exists(processed_path):
        return jsonify({"error": "Page not found"}), 404

    page_img = cv2.imread(processed_path)
    header_frac = float(body.get("header_frac", 0.12))
    footer_frac = float(body.get("footer_frac", 0.04))
    col_left = float(body.get("column_left_frac", 0.78))
    col_right = float(body.get("column_right_frac", 1.0))

    usable_top, usable_bottom = header_frac, 1.0 - footer_frac
    if usable_bottom <= usable_top:
        return jsonify({"error": "header_frac + footer_frac leave no usable row area"}), 400
    row_height = (usable_bottom - usable_top) / row_count

    checklist_by_line = {l["line_no"]: l for l in sess.checklist()}
    line_states = sess.line_states()

    provider = ocr_provider()
    results_out = []

    for i in range(row_count):
        line_no = first_line_no + i
        if line_no not in checklist_by_line:
            continue  # page runs past the end of the checklist; skip silently

        top = usable_top + i * row_height
        bottom = top + row_height
        crop = imaging.crop_count_column(page_img, top, bottom, col_left, col_right)
        if crop.size == 0:
            continue

        result = provider.read_count_box(crop)
        status = _confidence_status(result.confidence, result.value is not None or result.is_dash_or_x)

        key = str(line_no)
        line_states[key] = {
            "physical_qty": result.value if not result.is_dash_or_x else 0.0,
            "ocr_note": result.note,
            "confidence": result.confidence,
            "status": status,
            "source_page": pid,
        }
        results_out.append({"line_no": line_no, **line_states[key], "raw_text": result.raw_text})

    sess.save_line_states(line_states)

    pages = sess.pages()
    for p in pages:
        if p["id"] == pid:
            p.update({
                "first_line_no": first_line_no,
                "row_count": row_count,
                "status": "read",
                "header_frac": header_frac,
                "footer_frac": footer_frac,
                "column_left_frac": col_left,
                "column_right_frac": col_right,
            })
    sess.save_pages(pages)

    return jsonify({"results": results_out})


# ----------------------------------------------------------------------
# Manual review edits
# ----------------------------------------------------------------------
@app.patch("/api/sessions/<sid>/lines/<line_no>")
def update_line(sid, line_no):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404

    checklist_lines = {str(l["line_no"]) for l in sess.checklist()}
    if line_no not in checklist_lines:
        return jsonify({"error": "Unknown line_no"}), 404

    body = request.json or {}
    line_states = sess.line_states()
    state = line_states.get(line_no, {})

    action = body.get("action")  # confirm | edit | skip | zero
    if action == "confirm":
        state["status"] = "confirmed"
    elif action == "skip":
        state["physical_qty"] = None
        state["status"] = "blank"
        state["ocr_note"] = None
    elif action == "zero":
        state["physical_qty"] = 0.0
        state["status"] = "confirmed"
        state["ocr_note"] = None
    elif action == "edit":
        qty = body.get("physical_qty", None)
        state["physical_qty"] = float(qty) if qty not in (None, "") else None
        state["status"] = "manual"
        state["ocr_note"] = body.get("ocr_note") or None
    else:
        return jsonify({"error": "action must be one of confirm, edit, skip, zero"}), 400

    line_states[line_no] = state
    sess.save_line_states(line_states)
    return jsonify({"line_no": line_no, **state})


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------
def _progress(checklist_lines, line_states):
    total = len(checklist_lines)
    filled = needs_review = blank = 0
    for l in checklist_lines:
        s = line_states.get(str(l["line_no"]), {})
        status = s.get("status", "blank")
        if status in ("ocr_amber", "ocr_red"):
            needs_review += 1
        elif status in ("confirmed", "manual", "ocr_green") and s.get("physical_qty") is not None:
            filled += 1
        elif status == "blank" or s.get("physical_qty") is None:
            blank += 1
    return {"total": total, "filled": filled, "needs_review": needs_review, "blank": blank}


@app.get("/api/sessions/<sid>/progress")
def progress(sid):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404
    return jsonify(_progress(sess.checklist(), sess.line_states()))


def _filename_stub(meta):
    prop = meta.get("property_id") or "property"
    dept = meta.get("department") or "department"
    d = meta.get("stock_take_date") or date.today().isoformat()
    safe = lambda s: "".join(c if c.isalnum() or c in "-_" else "_" for c in s)
    return f"{safe(prop)}_{safe(dept)}_{safe(d)}"


@app.get("/api/sessions/<sid>/export/counts.csv")
def export_counts(sid):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404

    allow_blanks = request.args.get("allow_blanks", "false").lower() == "true"
    checklist_lines = sess.checklist()
    line_states = sess.line_states()

    prog = _progress(checklist_lines, line_states)
    if prog["needs_review"] > 0 and not allow_blanks:
        return jsonify({
            "error": "unresolved_review_items",
            "message": f"{prog['needs_review']} line(s) are still amber/red. "
                       "Resolve them or export with allow_blanks=true.",
            "progress": prog,
        }), 409

    csv_bytes = checklist_mod.rows_to_csv_bytes(checklist_lines, line_states)
    filename = f"{_filename_stub(sess.meta())}_counts.csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/sessions/<sid>/export/review.csv")
def export_review(sid):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404
    csv_bytes = checklist_mod.review_report_csv_bytes(sess.checklist(), sess.line_states())
    filename = f"{_filename_stub(sess.meta())}_review_report.csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/sessions/<sid>/notes")
def get_notes(sid):
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404
    path = os.path.join(sess.dir, "session_notes.txt")
    text = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    return jsonify({"notes": text})


@app.put("/api/sessions/<sid>/notes")
def put_notes(sid):
    """
    Free-text notes for anything that doesn't belong on a checklist line:
    margin comments, products spotted that aren't on the checklist, damaged
    stock, discrepancies to flag to the person running GSCORE populate.
    Never used to invent new checklist rows.
    """
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404
    text = (request.json or {}).get("notes", "")
    with open(os.path.join(sess.dir, "session_notes.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    return jsonify({"ok": True})


@app.get("/api/sessions/<sid>/export/images.zip")
def export_images(sid):
    import zipfile
    sess = session_store.get_session(sid)
    if not sess.exists():
        return jsonify({"error": "Session not found or expired"}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(sess.pages(), key=lambda x: x["order"]):
            path = sess.page_image_path(p["id"], "processed")
            if os.path.exists(path):
                zf.write(path, arcname=f"page_{p['order']+1:02d}_{p['id']}.jpg")
    buf.seek(0)
    filename = f"{_filename_stub(sess.meta())}_pages.zip"
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=filename)


if __name__ == "__main__":
    os.makedirs(config.SESSION_DIR, exist_ok=True)
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
