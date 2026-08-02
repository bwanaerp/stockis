FROM python:3.11-slim

# Tesseract is the default (free, offline) OCR engine. Keep it even if you
# switch OCR_PROVIDER to a cloud vendor, so local dev / fallback still works.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/sessions

ENV PORT=8080
ENV SESSION_DIR=/app/sessions
ENV SESSION_TTL_HOURS=24
ENV OCR_PROVIDER=tesseract

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "--timeout", "120", "app:app"]
