# Reproducible Android build image for Parlor (Capacitor 8). All versions pinned.
FROM eclipse-temurin:21.0.9_10-jdk-noble

ARG NODE_VERSION=22.23.4
ARG NODE_SHA256=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
ARG CMDLINE_TOOLS=15859902
ARG CMDLINE_TOOLS_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
ARG PLATFORM=android-36
ARG BUILD_TOOLS=36.0.0
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip xz-utils git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
RUN curl -fsSLo node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
 && echo "${NODE_SHA256}  node.tar.xz" | sha256sum -c - \
 && tar -xJf node.tar.xz -C /usr/local --strip-components=1 \
 && rm node.tar.xz

ENV ANDROID_HOME=/opt/android-sdk
ENV ANDROID_SDK_ROOT=/opt/android-sdk
ENV PATH="${PATH}:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/${BUILD_TOOLS}"

RUN curl -fsSLo cmd.zip "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS}_latest.zip" \
 && echo "${CMDLINE_TOOLS_SHA256}  cmd.zip" | sha256sum -c - \
 && mkdir -p "${ANDROID_HOME}/cmdline-tools" \
 && unzip -q cmd.zip -d "${ANDROID_HOME}/cmdline-tools" \
 && mv "${ANDROID_HOME}/cmdline-tools/cmdline-tools" "${ANDROID_HOME}/cmdline-tools/latest" \
 && rm cmd.zip \
 && { yes || true; } | sdkmanager --licenses >/dev/null \
 && sdkmanager "platform-tools" "platforms;${PLATFORM}" "build-tools;${BUILD_TOOLS}" \
 && sdkmanager --list_installed

# The build runs as the caller's uid (bind-mounted outputs stay owned by them), so the SDK and the
# Gradle cache dir (a named volume at run time) must be world-writable. platform-tools is not pinnable via sdkmanager.
RUN mkdir /gradle && chmod -R a+rwX "${ANDROID_HOME}" /gradle
ENV GRADLE_USER_HOME=/gradle
WORKDIR /work
