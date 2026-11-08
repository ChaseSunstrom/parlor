# SQLCipher (Parlor's database is encrypted at rest) has no prebuilt wheel for ARM: build it here,
# from its bundled source with OpenSSL, so every platform gets the same one
FROM python:3.12-slim AS sqlcipher
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip wheel --no-cache-dir --wheel-dir /wheels "sqlcipher3==0.6.0"

FROM python:3.12-slim

# ffmpeg joins video and audio and converts music; deno lets yt-dlp solve
# YouTube's JavaScript challenges (without it downloads crawl or fail).
# ffmpeg comes from the base image's Debian release, which pins it already
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
COPY --from=sqlcipher /wheels /wheels
# yt-dlp is the newest on purpose: an older one stops working with YouTube within weeks
# hadolint ignore=DL3013
RUN pip install --no-cache-dir "spotdl~=4.5.2" "spotapi~=1.2.8" "pillow~=13.0" "cryptography~=50.0" /wheels/*.whl \
    && pip install --no-cache-dir -U "yt-dlp[default]" && rm -rf /wheels

COPY app /app

# music-fndr writes relative to the working directory
WORKDIR /music
ENV PORT=8800 DATA_DIR=/data VIDEO_DIR=/videos MUSIC_DIR=/music UPDATE_HOURS=24 PYTHONUNBUFFERED=1
EXPOSE 8800
# a yt-dlp older than a few weeks fails YouTube downloads: update it on every start
# (and every UPDATE_HOURS while running; UPDATE_HOURS=0 turns both off). Offline,
# this gives up within a minute and starts with the yt-dlp it has.
CMD ["sh", "-c", "[ \"${UPDATE_HOURS:-24}\" = 0 ] || timeout 60 pip install -q -U --no-cache-dir --disable-pip-version-check --root-user-action=ignore --timeout 15 --retries 2 'yt-dlp[default]' || echo 'yt-dlp update skipped'; mkdir -p \"$MUSIC_DIR\" && cd \"$MUSIC_DIR\" && exec uvicorn --app-dir /app app:app --host 0.0.0.0 --port $PORT"]
