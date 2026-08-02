FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_NAME="Spotify Decades Plus" \
    PORT=8080 \
    DATABASE_PATH=/data/spotify_decades.db \
    LOG_PATH=/logs/app.log \
    PLAYLIST_PREFIX="My" \
    PLAYLIST_PUBLIC=false \
    APP_DEBUG=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app
RUN mkdir -p /data /logs

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "180", "app:app"]