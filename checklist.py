import csv
import io

EXPORT_COLUMNS = [
    "line_no",
    "store_product_id",
    "product_name",
    "unit",
    "category",
    "system_eod_qty",
    "physical_qty",
    "ocr_note",
]

REQUIRED_INPUT_COLUMNS = {
    "line_no",
    "store_product_id",
    "product_name",
    "unit",
    "category",
}


class ChecklistError(ValueError):
    pass


def parse_checklist_csv(file_bytes: bytes):
    """
    Parse a GSCORE blank checklist CSV (with or without a physical_qty
    column already present, and with or without system_eod_qty).
    Returns a list of dicts, one per checklist line, sorted by line_no,
    with physical_qty/ocr_note always reset to blank — this app fills
    those, it never trusts pre-existing values in an uploaded checklist.
    """
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise ChecklistError("CSV appears to be empty.")

    headers = {h.strip() for h in reader.fieldnames}
    missing = REQUIRED_INPUT_COLUMNS - headers
    if missing:
        raise ChecklistError(
            f"Checklist CSV is missing required column(s): {', '.join(sorted(missing))}"
        )

    lines = []
    for row_num, row in enumerate(reader, start=2):  # header is row 1
        raw_line_no = (row.get("line_no") or "").strip()
        if not raw_line_no:
            continue  # skip blank trailing rows
        try:
            line_no = int(float(raw_line_no))
        except ValueError:
            raise ChecklistError(f"Row {row_num}: line_no '{raw_line_no}' is not a number.")

        product_name = (row.get("product_name") or "").strip()
        if not product_name:
            raise ChecklistError(f"Row {row_num} (line_no {line_no}): product_name is blank.")

        lines.append({
            "line_no": line_no,
            "store_product_id": (row.get("store_product_id") or "").strip(),
            "product_name": product_name,
            "unit": (row.get("unit") or "").strip(),
            "category": (row.get("category") or "").strip(),
            "system_eod_qty": (row.get("system_eod_qty") or "").strip(),
        })

    if not lines:
        raise ChecklistError("No checklist lines found (checked for a non-blank line_no).")

    lines.sort(key=lambda r: r["line_no"])

    seen = set()
    for l in lines:
        if l["line_no"] in seen:
            raise ChecklistError(f"Duplicate line_no {l['line_no']} in checklist CSV.")
        seen.add(l["line_no"])

    return lines


def build_initial_line_states(checklist_lines):
    """Per-line review state, keyed by line_no as a string (JSON-safe)."""
    states = {}
    for l in checklist_lines:
        states[str(l["line_no"])] = {
            "physical_qty": None,   # None = blank/skip
            "ocr_note": None,
            "confidence": None,     # 0-1, or None if never OCR'd
            "status": "blank",      # blank | ocr_green | ocr_amber | ocr_red | confirmed | manual
            "source_page": None,    # which page id produced the OCR read
        }
    return states


def format_qty(value):
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def rows_to_csv_bytes(checklist_lines, line_states) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
    writer.writeheader()
    for l in checklist_lines:
        state = line_states.get(str(l["line_no"]), {})
        writer.writerow({
            "line_no": l["line_no"],
            "store_product_id": l["store_product_id"],
            "product_name": l["product_name"],
            "unit": l["unit"],
            "category": l["category"],
            "system_eod_qty": l["system_eod_qty"],
            "physical_qty": format_qty(state.get("physical_qty")),
            "ocr_note": state.get("ocr_note") or "",
        })
    return buf.getvalue().encode("utf-8")


def review_report_csv_bytes(checklist_lines, line_states) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=["line_no", "product_name", "physical_qty", "confidence", "status", "ocr_note"]
    )
    writer.writeheader()
    for l in checklist_lines:
        state = line_states.get(str(l["line_no"]), {})
        writer.writerow({
            "line_no": l["line_no"],
            "product_name": l["product_name"],
            "physical_qty": format_qty(state.get("physical_qty")),
            "confidence": state.get("confidence") if state.get("confidence") is not None else "",
            "status": state.get("status", "blank"),
            "ocr_note": state.get("ocr_note") or "",
        })
    return buf.getvalue().encode("utf-8")
