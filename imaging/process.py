"""
CamScanner-style page processing:
  1. find_page_corners  - locate the sheet of paper in a raw photo
  2. warp_to_page        - perspective-correct + deskew to a flat rectangle
  3. enhance              - contrast boost / "whiteboard" clean-up
  4. crop_count_column    - slice the rightmost "Counted" column into
                             per-row boxes for OCR, once row count is known
"""

import cv2
import numpy as np


def _order_corners(pts):
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.array(pts, dtype="float32")
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).flatten()
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype="float32")


def find_page_corners(image_bgr):
    """
    Best-effort detection of the sheet-of-paper quadrilateral in a photo.
    Returns 4 (x, y) corners, or None if no confident quad was found (the
    frontend should then let the user drag corners manually, or the
    caller can fall back to using the full image).
    """
    h, w = image_bgr.shape[:2]
    scale = 1000.0 / max(h, w)
    small = cv2.resize(image_bgr, (int(w * scale), int(h * scale)))

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blur, 50, 150)
    edged = cv2.dilate(edged, np.ones((5, 5), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]

    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4 and cv2.contourArea(approx) > 0.2 * small.shape[0] * small.shape[1]:
            corners = approx.reshape(4, 2).astype("float32") / scale
            return _order_corners(corners).tolist()

    return None


def warp_to_page(image_bgr, corners=None):
    """Perspective-correct the photo to a flat top-down rectangle. If
    corners is None, tries auto-detection; if that also fails, returns
    the original image untouched (better than a bad warp)."""
    if corners is None:
        corners = find_page_corners(image_bgr)
    if corners is None:
        return image_bgr

    rect = _order_corners(corners)
    (tl, tr, br, bl) = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = max(int(width_a), int(width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = max(int(height_a), int(height_b))

    if max_width < 10 or max_height < 10:
        return image_bgr

    dst = np.array(
        [[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image_bgr, matrix, (max_width, max_height))


def enhance(image_bgr, mode="color"):
    """
    CamScanner-style "optimize": boosts local contrast and lightens the
    background so faint pencil/pen marks and printed text both stand out.
    mode: "color" (keeps a natural document look, default) or
          "bw" (hard black-and-white, closer to a photocopy).
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    # CLAHE evens out lighting across the page (phone flash hot-spots,
    # shadows from the person's own arm, uneven ambient light).
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    equalized = clahe.apply(gray)

    if mode == "bw":
        thresh = cv2.adaptiveThreshold(
            equalized, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 15
        )
        return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

    # "color" mode: apply the equalized luminance back onto the original,
    # so ink color / highlighter marks stay visible for human review.
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hsv[:, :, 2] = cv2.addWeighted(hsv[:, :, 2], 0.3, equalized, 0.7, 0)
    result = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    # mild sharpen
    blurred = cv2.GaussianBlur(result, (0, 0), 3)
    return cv2.addWeighted(result, 1.4, blurred, -0.4, 0)


def deskew(image_bgr):
    """Small-angle rotation correction for pages that are mostly square
    but slightly tilted (use after warp_to_page, or standalone if no
    quad was detected)."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 200, minLineLength=100, maxLineGap=10)
    if lines is None:
        return image_bgr

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if -20 < angle < 20:  # ignore near-vertical lines
            angles.append(angle)

    if not angles:
        return image_bgr

    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.3:
        return image_bgr

    h, w = image_bgr.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), median_angle, 1.0)
    return cv2.warpAffine(image_bgr, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def process_page(image_bgr, corners=None, enhance_mode="color"):
    """Full CamScanner-style pipeline: warp -> deskew -> enhance."""
    warped = warp_to_page(image_bgr, corners)
    straightened = deskew(warped)
    cleaned = enhance(straightened, mode=enhance_mode)
    return cleaned


def crop_count_column(
    processed_page_bgr,
    row_top_frac,
    row_bottom_frac,
    column_left_frac=0.78,
    column_right_frac=1.0,
    pad_frac=0.15,
):
    """
    Crop one row's "Counted" box out of a processed page.
    Fractions are relative to page width/height (0-1), so this works
    regardless of the photo's resolution. Defaults assume the count
    column occupies the right ~22% of the printed sheet — adjust via
    the template/layout config if a property's sheet layout differs.
    """
    h, w = processed_page_bgr.shape[:2]

    row_h = row_bottom_frac - row_top_frac
    top = max(0.0, row_top_frac - row_h * pad_frac)
    bottom = min(1.0, row_bottom_frac + row_h * pad_frac)

    y1, y2 = int(top * h), int(bottom * h)
    x1, x2 = int(column_left_frac * w), int(column_right_frac * w)

    y1, y2 = max(0, y1), min(h, max(y2, y1 + 1))
    x1, x2 = max(0, x1), min(w, max(x2, x1 + 1))

    return processed_page_bgr[y1:y2, x1:x2]
