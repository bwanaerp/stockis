import os

# --- Auth -------------------------------------------------------------
# Simple shared-password gate. Leave APP_PASSWORD unset to disable auth
# entirely (fine for a private, hard-to-guess URL; not recommended for
# a public host).
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# --- Storage ------------------------------------------------------------
SESSION_DIR = os.environ.get("SESSION_DIR", os.path.join(os.path.dirname(__file__), "sessions"))
SESSION_TTL_HOURS = float(os.environ.get("SESSION_TTL_HOURS", "24"))
MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "25"))

# --- OCR ------------------------------------------------------------
# One of: tesseract | google | textract | azure
OCR_PROVIDER = os.environ.get("OCR_PROVIDER", "tesseract").lower()

GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
GOOGLE_DOCAI_PROCESSOR_ID = os.environ.get("GOOGLE_DOCAI_PROCESSOR_ID", "")

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

AZURE_FORM_RECOGNIZER_ENDPOINT = os.environ.get("AZURE_FORM_RECOGNIZER_ENDPOINT", "")
AZURE_FORM_RECOGNIZER_KEY = os.environ.get("AZURE_FORM_RECOGNIZER_KEY", "")

# --- Confidence thresholds --------------------------------------------
# OCR provider confidence (0-1) at/above this -> "green" (high confidence)
CONFIDENCE_GREEN = float(os.environ.get("CONFIDENCE_GREEN", "0.80"))
# below CONFIDENCE_GREEN but at/above this -> "amber" (needs a quick glance)
CONFIDENCE_AMBER = float(os.environ.get("CONFIDENCE_AMBER", "0.45"))
# below CONFIDENCE_AMBER, or no read at all -> "red" (needs manual entry)
