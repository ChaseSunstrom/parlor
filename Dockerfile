FROM python:3.12-slim

# ffmpeg joins video and audio and converts music; deno lets yt-dlp solve
# YouTube's JavaScript challenges (without it downloads crawl or fail)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
RUN pip install --no-cache-dir "spotdl~=4.5.2" "spotapi~=1.2.8" \
    && pip install --no-cache-dir -U "yt-dlp[default]"

COPY app /app

# music-fndr writes relative to the working directory
WORKDIR /music
ENV PORT=8800 DATA_DIR=/data VIDEO_DIR=/videos MUSIC_DIR=/music UPDATE_HOURS=24 PYTHONUNBUFFERED=1
EXPOSE 8800
# a yt-dlp older than a few weeks fails YouTube downloads: update it on every start
# (and every UPDATE_HOURS while running; UPDATE_HOURS=0 turns both off). Offline,
# this gives up within a minute and starts with the yt-dlp it has.
CMD ["sh", "-c", "[ \"${UPDATE_HOURS:-24}\" = 0 ] || timeout 60 pip install -q -U --no-cache-dir --disable-pip-version-check --root-user-action=ignore --timeout 15 --retries 2 'yt-dlp[default]' || echo 'yt-dlp update skipped'; mkdir -p \"$MUSIC_DIR\" && cd \"$MUSIC_DIR\" && exec uvicorn --app-dir /app app:app --host 0.0.0.0 --port $PORT"]
