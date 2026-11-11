#!/usr/bin/env bash
# Build Parlor's desktop app (an Electron shell that bundles app/static and plays films in mpv) in Docker, for Linux
# x86_64 and arm64 (the Raspberry Pi; Electron ships its own arm64 build, so nothing is emulated)
#   -> dist/desktop/Parlor-x86_64.AppImage, Parlor-arm64.AppImage, Parlor-amd64.deb, Parlor-arm64.deb
# Windows and macOS are built by .github/workflows/desktop.yml on their own machines.
# Usage: scripts/build-desktop.sh [--clean]   (--clean also drops the download cache volume)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
img=parlor-desktop-build:1
vol=parlor-desktop-cache
cd "$root"

fail() { echo "check failed: $*" >&2; exit 1; }
# shellcheck disable=SC2154  # rc is set inside the trap string
trap 'rc=$?; [ "$rc" -eq 0 ] || echo "RESULT: FAIL (exit $rc)"' EXIT

if [ "${1:-}" = "--clean" ]; then docker volume rm -f "$vol" >/dev/null; fi

nice -n 19 docker build -q -t "$img" desktop >/dev/null

# www and out are build artifacts: the page is copied in fresh every time
rm -rf desktop/www desktop/out dist/desktop
mkdir -p desktop/www dist/desktop
cp -r app/static/. desktop/www/

nice -n 19 docker run --rm --cpus 4 -u "$(id -u):$(id -g)" -e HOME=/cache/home -e npm_config_cache=/cache/npm \
  -e ELECTRON_CACHE=/cache/electron -e ELECTRON_BUILDER_CACHE=/cache/builder \
  -v "$root/desktop":/work -v "$vol":/cache -w /work "$img" bash -euo pipefail -c '
npm ci --no-audit --no-fund
npx electron-builder --linux --x64 --arm64 --publish never
file out/linux-unpacked/parlor-desktop out/linux-arm64-unpacked/parlor-desktop
'

cp desktop/out/Parlor-*.AppImage desktop/out/Parlor-*.deb dist/desktop/
for f in Parlor-x86_64.AppImage Parlor-arm64.AppImage Parlor-amd64.deb Parlor-arm64.deb; do
  [ -s "dist/desktop/$f" ] || fail "dist/desktop/$f is missing"
done
# the page inside the package is this repo's page, whole
docker run --rm -v "$root/desktop":/work -w /work "$img" bash -c 'npx --no-install asar extract out/linux-unpacked/resources/app.asar /tmp/a && diff -rq /tmp/a/www www' || fail "the packaged page differs from app/static"
ls -la dist/desktop
echo "RESULT: ok"
