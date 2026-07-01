FROM python:3.11-slim

LABEL maintainer="m3u8-downloader"
LABEL description="M3U8 video downloader for NAS / Docker"

# Prevent Python from buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DOWNLOAD_DIR=/downloads
ENV PORT=7860

# Install ffmpeg
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Ensure download directory exists
RUN mkdir -p /downloads

EXPOSE 7860

CMD ["python", "app.py"]
