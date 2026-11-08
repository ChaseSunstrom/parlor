#!/bin/sh
# a virtual screen with a ticking clock on it, and Sunshine (login parlor / standin-pass) streaming it
Xvfb :0 -screen 0 1920x1080x24 -nolisten tcp &
export DISPLAY=:0
while [ ! -S /tmp/.X11-unix/X0 ]; do sleep 0.1; done
xterm -geometry 100x30+40+40 -fa Monospace -fs 28 -e sh -c 'while true; do date +%T.%N; sleep 0.05; done' &
mkdir -p ~/.config/sunshine
printf "capture = x11\nencoder = software\nsw_preset = ultrafast\norigin_web_ui_allowed = wan\nmin_log_level = debug\n" > ~/.config/sunshine/sunshine.conf
sunshine ~/.config/sunshine/sunshine.conf --creds parlor standin-pass >/dev/null 2>&1
exec sunshine ~/.config/sunshine/sunshine.conf
