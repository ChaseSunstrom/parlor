#!/usr/bin/env bash
# Build Parlor's Android APK (Capacitor shell bundling app/static, with android/native's WebView client) in Docker -> dist/parlor.apk
# Usage: scripts/build-android.sh [--clean]   (--clean also drops the Gradle cache volume)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
img=parlor-android-build:1
vol=parlor-android-gradle
pkg=org.parlor.app
cd "$root"

fail() { echo "check failed: $*" >&2; exit 1; }
# shellcheck disable=SC2154  # rc is set inside the trap string
trap 'rc=$?; [ "$rc" -eq 0 ] || echo "RESULT: FAIL (exit $rc)"' EXIT

if [ "${1:-}" = "--clean" ]; then docker volume rm -f "$vol" >/dev/null; fi

nice -n 19 docker build -t "$img" android

# www and the native project are build artifacts: regenerated from pinned versions every time
rm -rf android/www android/android dist
mkdir -p android/www dist
cp -r app/static/. android/www/

run() { # run <shell script> inside the build image as the caller
  # the debug signing key lives in the Gradle volume, so a rebuilt APK installs over the last one (--clean makes a new key)
  nice -n 19 docker run --rm --cpus 4 -u "$(id -u):$(id -g)" -e HOME=/tmp -e ANDROID_USER_HOME=/gradle/android \
    -v "$root/android":/work -v "$vol":/gradle "$img" bash -euo pipefail -c "$1"
}

# shellcheck disable=SC2016  # the script is meant to expand inside the container
run '
npm ci --no-audit --no-fund
npx cap add android
cp native/*.java android/app/src/main/java/org/parlor/app/   # the WebView client that passes requests on to the server
m=android/app/src/main/AndroidManifest.xml
sed -i "s|<application|<application android:usesCleartextTraffic=\"true\"|" "$m"
grep -q usesCleartextTraffic "$m"
# Android 14 and later: the media notification (lock-screen controls, playing on in the background) is a media foreground service
sed -i "s|<uses-permission android:name=\"android.permission.INTERNET\" />|&\n    <uses-permission android:name=\"android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK\" />|" "$m"
grep -q FOREGROUND_SERVICE_MEDIA_PLAYBACK "$m"
# Android TV: listed in its launcher with a banner, and no touchscreen needed (it opens on the TV mode there)
cp -r native/res/. android/app/src/main/res/
sed -i "s|<application|<application android:banner=\"@drawable/banner\"|" "$m"
sed -i "s|<category android:name=\"android.intent.category.LAUNCHER\" />|&\n                <category android:name=\"android.intent.category.LEANBACK_LAUNCHER\" />|" "$m"
sed -i "s|<uses-permission android:name=\"android.permission.INTERNET\" />|&\n    <uses-feature android:name=\"android.software.leanback\" android:required=\"false\" />\n    <uses-feature android:name=\"android.hardware.touchscreen\" android:required=\"false\" />|" "$m"
grep -q LEANBACK_LAUNCHER "$m"
npx cap sync android
cd android
./gradlew --no-daemon -Dorg.gradle.workers.max=4 assembleDebug
'

apk=android/android/app/build/outputs/apk/debug/app-debug.apk
[ -f "$apk" ] || fail "no apk"
cp "$apk" dist/parlor.apk

# check the APK itself, inside the image
check=$(nice -n 19 docker run --rm -v "$root/dist":/dist:ro "$img" bash -euo pipefail -c '
aapt2 dump badging /dist/parlor.apk | grep -E "^package:|uses-permission: name=.android.permission.(INTERNET|FOREGROUND_SERVICE_MEDIA_PLAYBACK).|leanback-launchable-activity"
aapt2 dump xmltree --file AndroidManifest.xml /dist/parlor.apk | grep -E "usesCleartextTraffic"
')
echo "$check"
grep -q "package: name='$pkg'" <<<"$check" || fail "package"
grep -q "android.permission.INTERNET" <<<"$check" || fail "INTERNET"
grep -q "FOREGROUND_SERVICE_MEDIA_PLAYBACK" <<<"$check" || fail "media playback service"
grep -q "leanback-launchable-activity" <<<"$check" || fail "Android TV launcher"
grep -Eq 'usesCleartextTraffic.*=(true|\(type 0x12\)0xffffffff)' <<<"$check" || fail "cleartext"

ls -l dist/parlor.apk
echo "RESULT: ok"
