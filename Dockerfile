FROM --platform=$BUILDPLATFORM python:3.12-slim AS builder

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Final stage ────────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy only runtime deps from builder
COPY --from=builder /install /usr/local
COPY --from=builder /usr/bin/ffmpeg /usr/bin/ffmpeg

# Copy application code
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]