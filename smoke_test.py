import io
import os
import shutil

os.environ.setdefault("SESSION_DIR", "/tmp/gscore_smoke_sessions")
os.environ.setdefault("APP_PASSWORD", "")
os.environ.setdefault("OCR_PROVIDER", "tesseract")

shutil.rmtree(os.environ["SESSION_DIR"], ignore_errors=True)

import numpy as np
import cv2

import app as app_module

client = app_module.app.test_client()


def make_fake_sheet(rows, values):
    """Build a synthetic 'printed + handwritten' sheet: product name column
    on the left (printed-style), a Counted box on the right with a
    machine-printed-looking digit standing in for handwriting (good enough
    to sanity-check the pipeline end-to-end without a real scan)."""
    W, H = 900, 1400
    img = np.full((H, W), 255, dtype=np.uint8)
    header_frac, footer_frac = 0.12, 0.04
    top, bottom = header_frac * H, (1 - footer_frac) * H
    row_h = (bottom - top) / rows

    cv2.putText(img, "DEPARTMENT STOCK TAKE - MAIN BAR", (30, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)
    cv2.line(img, (0, int(top)), (W, int(top)), 0, 2)

    col_left = int(0.78 * W)
    cv2.line(img, (col_left, int(top)), (col_left, int(bottom)), 0, 1)

    for i in range(rows):
        y = int(top + i * row_h)
        cv2.line(img, (0, y), (W, y), 180, 1)
        text_y = int(top + (i + 0.6) * row_h)
        cv2.putText(img, f"Product {i+1}", (30, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 0, 1)
        val = values[i]
        cv2.putText(img, str(val), (col_left + 25, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2)

    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def main():
    # 1. create session
    checklist_csv = open("samples/checklist_sample.csv", "rb").read()
    resp = client.post(
        "/api/sessions",
        data={
            "checklist_csv": (io.BytesIO(checklist_csv), "checklist.csv"),
            "property_id": "LDG-04",
            "department": "Main Bar",
            "stock_take_date": "2026-08-02",
            "enhance_mode": "color",
        },
        content_type="multipart/form-data",
    )
    print("create_session:", resp.status_code, resp.get_json())
    assert resp.status_code == 200
    sid = resp.get_json()["session_id"]

    # 2. upload a synthetic page covering lines 1-10
    values = ["2", "0", "3.5", "-", "1", "X", "2", "1", "5", "4"]
    sheet = make_fake_sheet(10, values)
    ok, buf = cv2.imencode(".jpg", sheet)
    resp = client.post(
        f"/api/sessions/{sid}/pages",
        data={"file": (io.BytesIO(buf.tobytes()), "page1.jpg"), "enhance_mode": "color"},
        content_type="multipart/form-data",
    )
    print("upload_page:", resp.status_code, resp.get_json())
    assert resp.status_code == 200
    page_id = resp.get_json()["pages"][0]["id"]

    # 3. accept / align / OCR
    resp = client.post(
        f"/api/sessions/{sid}/pages/{page_id}/accept",
        json={"first_line_no": 1, "row_count": 10, "header_frac": 0.12, "column_left_frac": 0.78},
    )
    print("accept_page:", resp.status_code)
    for r in resp.get_json()["results"]:
        print("  line", r["line_no"], "-> qty=", r["physical_qty"], "conf=", r["confidence"],
              "status=", r["status"], "raw=", repr(r["raw_text"]))
    assert resp.status_code == 200

    # 4. progress
    resp = client.get(f"/api/sessions/{sid}/progress")
    print("progress:", resp.get_json())

    # 5. manual edit of one line (simulate reviewer)
    resp = client.patch(f"/api/sessions/{sid}/lines/8", json={"action": "edit", "physical_qty": "1"})
    print("manual edit line 8:", resp.status_code, resp.get_json())

    # 6. confirm a couple of lines so export isn't fully blocked
    for ln in ["1", "2", "3", "5", "7", "9", "10"]:
        client.patch(f"/api/sessions/{sid}/lines/{ln}", json={"action": "confirm"})

    # 7. try export without allow_blanks (lines 11-20 never scanned -> still blank, that's fine;
    #    but any amber/red among 1-10 should block)
    resp = client.get(f"/api/sessions/{sid}/export/counts.csv")
    print("export (strict):", resp.status_code)
    if resp.status_code == 409:
        print("  blocked as expected:", resp.get_json())

    # 8. force export with blanks and print the CSV
    resp = client.get(f"/api/sessions/{sid}/export/counts.csv?allow_blanks=true")
    print("export (allow_blanks):", resp.status_code)
    assert resp.status_code == 200
    print(resp.data.decode())

    # 9. notes roundtrip
    client.put(f"/api/sessions/{sid}/notes", json={"notes": "Found 2 unlisted mixers in the fridge."})
    resp = client.get(f"/api/sessions/{sid}/notes")
    print("notes:", resp.get_json())

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
