"""
Azure AI Document Intelligence (formerly Form Recognizer) OCR backend,
using the prebuilt-read model, which supports handwriting.

Setup:
  OCR_PROVIDER=azure
  AZURE_FORM_RECOGNIZER_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
  AZURE_FORM_RECOGNIZER_KEY=<key>
  pip install azure-ai-formrecognizer (uncomment in requirements.txt)

Billed per page (see Azure Document Intelligence pricing); latency is
typically 1-3s per call since analyze is an async poll under the hood.
Given that per-call overhead, batch all count boxes for a page into a
single stitched image before calling this in production rather than
one call per box (this stub is one-call-per-box for readability).
"""

from .base import OcrProvider, OcrResult
from .parsing import parse_count_text


class AzureReadOcr(OcrProvider):
    name = "azure"

    def __init__(self, endpoint: str, key: str):
        if not endpoint or not key:
            raise RuntimeError(
                "AZURE_FORM_RECOGNIZER_ENDPOINT / AZURE_FORM_RECOGNIZER_KEY not set. "
                "See ocr/cloud_azure.py for setup steps."
            )
        self.endpoint = endpoint
        self.key = key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from azure.ai.formrecognizer import DocumentAnalysisClient
            from azure.core.credentials import AzureKeyCredential
            self._client = DocumentAnalysisClient(self.endpoint, AzureKeyCredential(self.key))
        return self._client

    def read_count_box(self, image_bgr) -> OcrResult:
        import cv2

        ok, buf = cv2.imencode(".png", image_bgr)
        if not ok:
            return OcrResult("", None, False, 0.0, note="encode failed")

        client = self._get_client()
        poller = client.begin_analyze_document("prebuilt-read", document=buf.tobytes())
        result = poller.result()

        text = (result.content or "").strip()
        confidences = [
            w.confidence
            for page in result.pages
            for w in getattr(page, "words", [])
        ]
        confidence = sum(confidences) / len(confidences) if confidences else 0.5

        value, is_dash_x, note = parse_count_text(text)
        return OcrResult(text, value, is_dash_x, round(confidence, 2), note)
