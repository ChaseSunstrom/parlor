#!/bin/sh
# Gluetun's front door, copied here by Parlor's setup step at every start (edits are overwritten).
# Until a WireGuard file is uploaded in Parlor (Settings -> Services -> VPN), nothing gets in or out
# but your home network and the other containers. With a file, Gluetun runs with its own kill
# switch and restarts whenever the file changes. Turning the VPN off in Parlor (it asks you to
# confirm) lets traffic out directly. The state for Parlor's page is in vpn.state; the
# compose counts this container healthy (so qBittorrent may start) only once the firewall is up.
DIR=${FRONT_DIR:-/gluetun}
ENTRY=${GLUETUN_ENTRYPOINT:-/gluetun-entrypoint}
TICK=${FRONT_TICK:-3}
BACKOFF=${FRONT_BACKOFF:-10}
CONF=$DIR/wireguard/wg0.conf
OFF=$DIR/vpn-off
STATE=$DIR/vpn.state
[ -e "$DIR/yeerr-vpn-off" ] && mv "$DIR/yeerr-vpn-off" "$OFF"  # compat: the off switch from before the rename
NETS=$(echo "${FIREWALL_OUTBOUND_SUBNETS:-192.168.0.0/16,172.16.0.0/12}" | tr ',' ' ')
IPT=iptables
$IPT -L -n >/dev/null 2>&1 || IPT=iptables-legacy  # the same choice Gluetun makes
IP6=$(echo "$IPT" | sed 's/^iptables/ip6tables/')
child=

say() { echo "$1" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"; echo "parlor-vpn: $1"; }
now() { cat "$STATE" 2>/dev/null; }

block() {  # policies first, so there's no moment when anything is open
  for t in $IPT $IP6; do
    $t -P INPUT DROP; $t -P OUTPUT DROP; $t -P FORWARD DROP; $t -F; $t -X 2>/dev/null
    $t -A INPUT -i lo -j ACCEPT; $t -A OUTPUT -o lo -j ACCEPT
  done 2>/dev/null
  $IPT -P OUTPUT DROP || return 1
  for n in $NETS; do
    $IPT -A INPUT -s "$n" -j ACCEPT && $IPT -A OUTPUT -d "$n" -j ACCEPT || return 1
  done
}

open_up() {
  for t in $IPT $IP6; do $t -F; $t -P INPUT ACCEPT; $t -P OUTPUT ACCEPT; $t -P FORWARD ACCEPT; done 2>/dev/null
}

alive() {  # running, and not a zombie waiting to be reaped
  [ -n "$child" ] && kill -0 "$child" 2>/dev/null &&
    [ "$(sed 's/.*) //' "/proc/$child/stat" 2>/dev/null | cut -d' ' -f1)" != Z ]
}

tidy() {  # what a killed Gluetun leaves (its interface, its routing rules) would stop the next one starting
  ip link del "${VPN_INTERFACE:-tun0}" 2>/dev/null
  for v in -4 -6; do
    for _ in 1 2 3 4; do ip "$v" rule del table 51820 2>/dev/null || break; done
  done
}

stop_child() {  # closed before, and again after: however Gluetun leaves its firewall, nothing opens
  [ -n "$child" ] || return 0
  block
  kill "$child" 2>/dev/null
  wait "$child" 2>/dev/null
  child=
  block
  tidy
}

trap 'stop_child; exit 0' TERM INT

rm -f "$STATE"  # no state, no "healthy": qBittorrent waits until the firewall below is in place
if ! block; then  # can't firewall: stop rather than run open (qBittorrent then has no network at all)
  say "broken: no firewall (is NET_ADMIN given?)"
  exit 1
fi
[ -x "$ENTRY" ] || { say "broken: this Gluetun image has no $ENTRY"; exit 1; }
say waiting
last=
ticks=0
while true; do
  if [ -f "$OFF" ]; then
    if [ "$(now)" != off ]; then stop_child; open_up; last=; say off; fi
  elif [ -s "$CONF" ]; then
    sum=$(cksum < "$CONF")
    if ! alive || [ "$sum" != "$last" ]; then
      crashed=
      [ -n "$child" ] && [ "$sum" = "$last" ] && crashed=1
      stop_child
      [ "$(now)" = off ] && block
      if [ -n "$crashed" ]; then say restarting; sleep "$BACKOFF" & wait $!; fi  # it stopped by itself: don't spin
      last=$sum
      say starting
      "$ENTRY" &
      child=$!
    elif "$ENTRY" healthcheck >/dev/null 2>&1; then
      [ "$(now)" = connected ] || say connected
    else
      [ "$(now)" != connected ] || say connecting  # it was up: the kill switch holds while it reconnects
    fi
  elif [ "$(now)" != waiting ]; then
    stop_child; block; last=; say waiting
  fi
  ticks=$((ticks + 1))
  [ $((ticks % 10)) -eq 0 ] && touch "$STATE"  # still here: Parlor notices when this stops
  sleep "$TICK" & wait $!  # wait, not sleep: a stop signal is handled at once
done
