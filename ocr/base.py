"""
Abstract interface every OCR backend implements, so the rest of the app
never talks to Tesseract / Google / AWS / Azure directly.

A "count box" is a small cropped image (numpy BGR array) containing a
single handwritten number, dash, or X — the rightmost "Counted" column
cell for one checklist line.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class OcrResult:
    raw_text: str          # exactly what the engine returned, unparsed
    value: Optional[float] # parsed numeric value, or None if not a number
    is_dash_or_x: bool     # true if the box read as '-' / 'x' / 'X'
    confidence: float      # 0.0 - 1.0, engine-reported or estimated
    note: Optional[str] = None  # e.g. "unclear", "multiple candidates"


class OcrProvider(ABC):
    name = "base"

    @abstractmethod
    def read_count_box(self, image_bgr) -> OcrResult:
        """Read a single handwritten count-box crop and return an OcrResult."""
        raise NotImplementedError

    def read_count_boxes_batch(self, crops) -> list[OcrResult]:
        """Default: call read_count_box in a loop. Cloud providers that
        support batch calls should override this for speed/cost."""
        return [self.read_count_box(c) for c in crops]
