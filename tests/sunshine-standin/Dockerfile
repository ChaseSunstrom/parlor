# A gaming PC for the streaming check: Sunshine on a virtual screen, encoding in software (no GPU), showing a clock.
FROM lizardbyte/sunshine:v2026.1107.205007-ubuntu-24.04
USER root
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends xvfb xterm libxfixes3 libxrandr2 libxtst6 libxdamage1 x11-xserver-utils && rm -rf /var/lib/apt/lists/*
COPY run.sh /run.sh
USER lizard
ENTRYPOINT ["/bin/sh", "/run.sh"]
