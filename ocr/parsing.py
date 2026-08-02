import re

DASH_X_RE = re.compile(r"^\s*[\-–—xX]\s*$")
NUMBER_RE = re.compile(r"^\s*(\d+([.,]\d+)?)\s*$")


def parse_count_text(raw: str):
    """
    Turn raw OCR text from a single count box into (value, is_dash_or_x, note).
    - '-' or 'X'/'x'        -> (0.0, True, None)
    - a clean number        -> (float, False, None)
    - anything else / empty -> (None, False, "unclear")
    Decimals are supported with '.' or ',' as the separator.
    """
    text = (raw or "").strip()

    if not text:
        return None, False, None  # nothing written -> stays blank, no note

    if DASH_X_RE.match(text):
        return 0.0, True, None

    m = NUMBER_RE.match(text)
    if m:
        num = m.group(1).replace(",", ".")
        try:
            return float(num), False, None
        except ValueError:
            pass

    # strip stray OCR noise (e.g. trailing punctuation) and retry once
    cleaned = re.sub(r"[^0-9.,\-xX]", "", text)
    if cleaned and cleaned != text:
        return parse_count_text(cleaned)

    return None, False, "unclear"
