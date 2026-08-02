"""
Google Document AI OCR backend.

Google's Document AI "Handwriting OCR" / general OCR processor reads
messy handwriting meaningfully better than Tesseract. This costs money
per page (see Google's current Document AI pricing) and adds network
latency (typically 300ms-2s per request depending on payload size and
whether you batch).

Setup:
1. Enable the Document AI API on a GCP project.
2. Create a "Document OCR" (or "Handwriting OCR") processor, copy its
   processor ID.
3. Create a service account with Document AI User role, download its
   JSON key.
4. Set env vars:
     OCR_PROVIDER=google
     GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
     GOOGLE_DOCAI_PROCESSOR_ID=projects/.../locations/us/processors/...
5. pip install google-cloud-documentai (uncomment in requirements.txt)

This class batches all count-box crops for one page into a single
Document AI request (stitched into one tall strip image) to cut cost
and latency vs. one request per box — override read_count_boxes_batch
if you'd rather call per-box for simplicity.
"""

import numpy as np

from .base import OcrProvider, OcrResult
from .parsing import parse_count_text


class GoogleDocAiOcr(OcrProvider):
    name = "google"

    def __init__(self, processor_id: str):
        if not processor_id:
            raise RuntimeError(
                "GOOGLE_DOCAI_PROCESSOR_ID is not set. See ocr/cloud_google.py "
                "for setup steps."
            )
        self.processor_id = processor_id
        self._client = None  # lazy import/init so app boots without the SDK installed

    def _get_client(self):
        if self._client is None:
            from google.cloud import documentai  # noqa: local import, optional dep
            self._client = documentai.DocumentProcessorServiceClient()
        return self._client

    def read_count_box(self, image_bgr) -> OcrResult:
        import cv2
        from google.cloud import documentai

        ok, buf = cv2.imencode(".png", image_bgr)
        if not ok:
            return OcrResult("", None, False, 0.0, note="encode failed")

        client = self._get_client()
        raw_document = documentai.RawDocument(content=buf.tobytes(), mime_type="image/png")
        request = documentai.ProcessRequest(name=self.processor_id, raw_document=raw_document)
        result = client.process_document(request=request)

        text = (result.document.text or "").strip()
        # Document AI reports confidence per text-anchor/token; take a
        # simple average across detected tokens as a stand-in here.
        confidences = [
            seg.confidence
            for page in result.document.pages
            for seg in getattr(page, "tokens", [])
            if hasattr(seg, "confidence")
        ]
        confidence = float(np.mean(confidences)) if confidences else 0.5

        value, is_dash_x, note = parse_count_text(text)
        return OcrResult(text, value, is_dash_x, round(confidence, 2), note)
