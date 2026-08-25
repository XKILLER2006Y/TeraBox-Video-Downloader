# Native arch per target — BUILDPLATFORM pin made pip bake amd64 wheels
# into the arm64 image (cryptg/cryptography .so ImportError crash-loop).
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Final stage ────────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# ffmpeg installed properly (binary alone lacks its shared libs and cannot run)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Copy runtime Python deps from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]