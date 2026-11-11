# Builds and checks Parlor's desktop app: Node for Electron and electron-builder, the libraries Electron needs to run
# (the check runs the real app on a virtual screen), and a real mpv.
FROM node:22-bookworm
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgtk-3-0 libnss3 libasound2 libgbm1 libxss1 libxtst6 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libcups2 \
      xvfb xauth mpv file ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /cache && chmod 1777 /cache
