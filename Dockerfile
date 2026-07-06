FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (from apt.txt)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]