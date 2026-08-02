"""
AWS Textract OCR backend.

Textract's AnalyzeDocument (FORMS/TABLES not needed here — plain text
detection is enough for a single-digit-box crop) handles handwriting
reasonably well and is billed per page (see AWS Textract pricing).

Setup:
  OCR_PROVIDER=textract
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION
  pip install boto3 (uncomment in requirements.txt)

Latency is typically 200ms-1s per synchronous DetectDocumentText call.
For a full page of ~30-60 count boxes, batching into one call per
physical page (rather than one call per box) will be both cheaper and
faster — this stub reads one box at a time for clarity; swap in a
per-page batch call for production volume.
"""

from .base import OcrProvider, OcrResult
from .parsing import parse_count_text


class TextractOcr(OcrProvider):
    name = "textract"

    def __init__(self, region: str):
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3  # noqa: local import, optional dep
            self._client = boto3.client("textract", region_name=self.region)
        return self._client

    def read_count_box(self, image_bgr) -> OcrResult:
        import cv2

        ok, buf = cv2.imencode(".png", image_bgr)
        if not ok:
            return OcrResult("", None, False, 0.0, note="encode failed")

        client = self._get_client()
        resp = client.detect_document_text(Document={"Bytes": buf.tobytes()})

        lines = [b for b in resp.get("Blocks", []) if b.get("BlockType") == "LINE"]
        if not lines:
            return OcrResult("", None, False, 0.0, note="no reading")

        text = " ".join(b.get("Text", "") for b in lines).strip()
        confidence = sum(b.get("Confidence", 0.0) for b in lines) / len(lines) / 100.0

        value, is_dash_x, note = parse_count_text(text)
        return OcrResult(text, value, is_dash_x, round(confidence, 2), note)
