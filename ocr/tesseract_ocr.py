import cv2
import numpy as np
import pytesseract

from .base import OcrProvider, OcrResult
from .parsing import parse_count_text

# Whitelist keeps Tesseract from guessing letters where only digits/marks
# are expected. psm 7 = "treat the crop as a single text line".
_TESS_CONFIG = (
    "--psm 7 --oem 3 "
    "-c tessedit_char_whitelist=0123456789.,-xX"
)


class TesseractOcr(OcrProvider):
    name = "tesseract"

    def read_count_box(self, image_bgr) -> OcrResult:
        prepped = self._prep(image_bgr)

        data = pytesseract.image_to_data(
            prepped, config=_TESS_CONFIG, output_type=pytesseract.Output.DICT
        )

        texts, confs = [], []
        for i, t in enumerate(data.get("text", [])):
            t = t.strip()
            if not t:
                continue
            texts.append(t)
            try:
                c = float(data["conf"][i])
            except (ValueError, TypeError):
                c = -1.0
            if c >= 0:
                confs.append(c)

        raw_text = "".join(texts)
        value, is_dash_x, note = parse_count_text(raw_text)

        # Tesseract's own confidence is on handwriting is generally
        # unreliable and tends to run optimistic. We scale it down and
        # additionally downgrade anything Tesseract couldn't parse at all,
        # since Tesseract is a weak handwriting reader — this app treats
        # its confidence as a rough triage signal, not a guarantee.
        if not texts:
            confidence = 0.0
            note = note or "no reading"
        else:
            avg_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.3
            confidence = avg_conf * 0.7  # deliberately conservative
            if value is None and not is_dash_x:
                confidence = min(confidence, 0.35)

        return OcrResult(
            raw_text=raw_text,
            value=value,
            is_dash_or_x=is_dash_x,
            confidence=round(max(0.0, min(1.0, confidence)), 2),
            note=note,
        )

    @staticmethod
    def _prep(image_bgr):
        """Upscale + binarize the crop; handwriting engines do much better
        on a clean, large, high-contrast crop than on the raw photo pixels."""
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        scale = max(1, int(200 / max(h, 1)))
        if scale > 1:
            gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # Tesseract expects dark text on light background
        if np.mean(thresh) < 127:
            thresh = cv2.bitwise_not(thresh)
        return thresh
