#!/usr/bin/env bash
# The app icons, made from app/static/logo.svg (needs rsvg-convert): run again after changing the logo.
set -euo pipefail
cd "$(dirname "$0")/../app/static"
rsvg-convert -w 192 -h 192 logo.svg -o icon-192.png
rsvg-convert -w 512 -h 512 logo.svg -o icon-512.png
# maskable and Apple's: the logo inside the safe zone, on Parlor's background (phones crop these to their own shape)
inner=$(sed -e '1,/<svg/{/<svg/d}' -e 's#</svg>##' logo.svg)
for size in 512 180; do
  printf '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#1c1a24"/><g transform="translate(6.4 6.4) scale(.6)">%s</g></svg>' "$inner" \
    | rsvg-convert -w "$size" -h "$size" -o "$([ "$size" = 512 ] && echo icon-maskable.png || echo icon-180.png)"
done
