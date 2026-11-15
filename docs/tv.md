# On a TV

[← Parlor's README](../README.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screens/tv-screen-desktop-dark.png">
  <img alt="Parlor on a TV: the Library with its menu on the left and a bright ring on the one thing the remote's arrow keys have selected." src="assets/screens/tv-screen-desktop-light.png">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screens/remote-phone-dark.png">
  <img alt="A phone used as the TV's remote: the TV's name, the film playing with its time, a round pad with arrows and OK, Back and Home, then skip back, pause, skip forward and volume." src="assets/screens/remote-phone-light.png" width="260">
</picture>

Parlor on a TV is made for a remote, and your phone can be the remote. This page covers pairing a TV, the TV layout, the phone remote, and controlling the TV set itself.

## Pairing a TV

1. Open Parlor on the TV's browser at `http://your-server:8800/tv` (or install the Android app on an Android TV: it's in the TV's launcher and opens this way by itself; an LG added as a [TV set](#the-tv-set-itself-power-input-and-volume) opens it from **Open Parlor on it**). The TV shows an 8-character code.
2. On your phone open **You → TVs**, type the code and a name under **Add a TV**, then press **Pair**.
3. The TV signs itself in to your account.

No password is typed on the TV, and the code works once, for five minutes.

## The TV layout

- **Made for a remote**: bigger, with a bright outline on what's selected.
- **Arrows**: they move to the nearest thing in that direction.
- **OK and Back**: OK opens it, Back goes back.
- **The menu** on the left reaches every section, and lists a section's pages once it's open.

## Play on TV

A film's or episode's **On TV** button, a YouTube video's (it carries on from where you are), and **⋯ → Play on TV** on any album or song send it to the TV, which plays it with its own sign-in. With more than one TV on, you pick which.

## The remote

You find it in You → TVs → a TV. It has:

- a joystick: drag it for the arrows, hold to keep going, press it for OK;
- Back and Home;
- play/pause;
- back 10 s and forward 30 s;
- next song;
- volume;
- a box that types on the TV (searching, say).

It shows what the TV is playing.

- **Two phones** can drive the same TV; both see what it does.
- **A computer's** arrow keys, Enter and Space work it too.
- **Volume**: the remote's volume is Parlor's on the TV. The TV set's own power, input and volume are under it, once an admin has added the set (below).
- **Connection**: the remote talks over server-sent events, so it works through the Android app and any reverse proxy. nginx must not buffer `/api/tv/events` (Parlor sends `X-Accel-Buffering: no`, which nginx honours).

## Unpairing and safety

- A TV that's off or not showing Parlor shows as off; it comes back by itself.
- **Unpair** signs it out. Signing out everywhere unpairs every TV.
- A TV is never signed in as an admin, even an admin's, so only enter a code you see on a TV in front of you.
- Admins see every account's TVs, and a remote for each, in **Settings → TVs**.

## The TV set itself: power, input and volume

An admin adds the set in **Settings → TVs → TV sets**:

1. Choose its type.
2. Type a name and its address on your network (`192.168.1.40`, or a name your router gives it).
3. Press **Pair** or **Test**.
4. The admin ticks which accounts may use it (admins always can).

**You → TVs** and the remote then have a row with the set's own **Power**, **Input**, **Vol −**, **Mute**, **Vol +** and **HDMI 1–4**, whichever that type has. The row is there with no TV paired yet.

An LG also has **Open Parlor on it**: the TV opens its own browser at this Parlor's TV screen, so nobody types an address with the TV's remote. It shows the pairing code the first time; pair it as above and the TV stays signed in. Three things to know:

- The TV opens the address you are using Parlor by, so use the server's address on your network (`http://192.168.1.20:8800`), not `localhost` and not a public name: Parlor only sends a TV to an address on your home network.
- An LG paired before this button existed has no leave to open anything: remove it in **Settings → TVs** and add it again.
- An LG always starts in webOS. Parlor can't replace that; after switching on, press **Open Parlor on it**. This button has only been tested against a stand-in, not a real LG, and an old TV's browser may not show the page.

### A film in the TV's own player

An LG can also play a film or an episode from your library, or a video from **Watch**, with no browser and no Parlor screen on the TV: the TV's built-in network player fetches the file from Parlor, and your phone is the remote.

1. Open a film or an episode in the **Library**, or a video in **Watch**, and press **On TV**. Sets that can play are listed as "(its own player)".
2. Pick the set. The TV starts the film from where you left off, and the phone goes to **You → TVs**.
3. Under **The TV set** you see what is playing, with **−10**, **Pause**, **+30**, **Stop** and a line to press along. Where it got to is saved to your library as it plays.

What it needs:

- **An address the TV can use.** The TV fetches the film over plain `http` from an address on your home network. If you open Parlor by a name or over https, an admin types the plain one in **Settings → TVs → The address TVs reach Parlor at**, like `http://192.168.1.20:8800`. Empty means the address you are using.
- **A way through, if a firewall sits between them.** Parlor's server asks the TV on UDP port 1900, then talks to the TCP port the TV answers with (it differs by model, and an LG changes it). An LG on another network segment than the server doesn't answer that question, so Parlor then looks through the TV's TCP ports 1024–9999 for its player: allow the server those. The TV fetches the film from Parlor's port (8800): if it can't, Parlor says the TV couldn't fetch it. The TV needs no internet.
- **The TV on.** A TV in standby doesn't answer.

A **Watch** video plays one of two ways:

- **Downloaded**: the TV gets the file, at the quality you downloaded.
- **Not downloaded**: the TV gets YouTube's one stream that has picture and sound together, fetched through Parlor. It is small, usually 360p, and Parlor says so when it starts. For the full picture, download the video first, then press **On TV**. A video with no such stream plays only once downloaded; a live stream doesn't play on a set.

Where a Watch video got to is saved to your Watch history.

Limits, on purpose for now:

- A film that is playing keeps its link through a restart or an update of Parlor, so the TV can fetch the rest when Parlor is back, and the remote still shows it. If the TV froze while Parlor was away (it says playing but its time has stopped), the remote sends the film back to it from that spot by itself within a few seconds; if it doesn't catch, press **On TV** again.
- The TV gets the file as it is. Parlor doesn't convert it, so a file the TV can't play is refused by the TV, and a sound format it lacks plays silent. Most MP4 and MKV films with H.264 or HEVC video play.
- No choice of subtitles or audio track from the phone: the TV's own remote picks from what is in the file.
- A TV's own player is pickier than a browser. When a film's first sound track is one many TVs play silent (Dolby TrueHD, DTS, FLAC), or its picture is one they can't show (AV1, or Dolby Vision with no plain picture under it), Parlor says so when it sends the film, with the way out: usually, choose the film's other sound track with the TV's remote. The film is sent as it is. Converting it on the way was tried on a real LG and the TV wouldn't start the stream reliably, so that isn't in.
- The link the TV fetches by needs no sign-in, since a TV can't sign in. It is a long random name, made for one film, kept only in memory, never shown in a page or a log, gone when you press **Stop**, when the remote sees the film end, after 12 hours at most, or when Parlor restarts.
- Only an LG is listed. It has played a film on a real LG (an OLED C6), with pause, skip and resume from the phone; other models are untested. If yours refuses, the message says what the TV answered.

With more than one set you pick which goes with the TV, and the phone remembers.

| Type | On the TV, once | Good to know |
|---|---|---|
| **Roku** | Settings → System → Advanced system settings → **Control by mobile apps** → Enabled (or Permissive) | No pairing. Power needs a Roku TV; a Roku stick has no power button to press. |
| **LG (webOS)** | Press **Pair** in Parlor, then **Accept** on the TV's prompt within 30 seconds | Parlor keeps the key the TV gives it. To switch the TV on, type its MAC when adding it and turn on the TV's **Mobile TV On** / **Turn on via Wi-Fi** (wired is more reliable). |
| **Samsung (Tizen)** | Press **Pair** in Parlor, then **Allow** on the TV's prompt | Parlor keeps the token. If the TV said no once: Settings → General → External Device Manager → Device Connection Manager → Device List. Switching on needs the MAC too. |
| **Android TV / Google TV** | Developer options (press **Build** seven times in About) → **USB debugging** and **Network debugging** on; press **Test** in Parlor, then tick **Always allow from this computer** and **Allow** on the TV | Anyone on the network can ask an Android TV for a debugging connection: allow only Parlor's server, and never open port 5555 to the internet. Parlor keeps its adb key in its database, so a rebuilt container isn't asked about again. |
| **HDMI-CEC adapter** | Nothing: it controls whatever is on the HDMI cable | The adapter is plugged into the server and passed into Parlor's container, e.g. `devices: ["/dev/ttyACM0:/dev/ttyACM0"]` for a USB adapter (`/dev/cec0` on a Raspberry Pi's own HDMI port). It has no address. **HDMI 1–4** tell the TV which of its ports to show. |

### Only your home network

- A set's address must be in 10.x, 172.16–31.x, 192.168.x or fc00::/7.
- It must not be on Docker's own network where Parlor's other containers are (unless `LAN_SUBNET` says that is your home network).
- Loopback, link-local (169.254.x and cloud metadata addresses) and anything on the internet are refused.
- A name is looked up again and checked before every connection, and Parlor connects to the address it checked.
- Redirects are not followed.

### Keys stay in

- Pairing keys, tokens and the adb key are in Parlor's encrypted database.
- While Parlor runs, adb reads its key from a copy in a temporary folder inside the container that only Parlor can read.
- Pairing keys, tokens and the adb key are never sent to a browser or written to the log.
- **Remove** forgets a set's key or token.

### Who can press what

- A member only sees and presses the sets an admin ticked for their account.
- Buttons are rate-limited, and a TV that doesn't answer within a few seconds says so on the remote.

### Tested against stand-ins

These were built against each maker's published protocol and tested against stand-ins of each, not against every model: if yours behaves differently, **Test** says what the TV answered.
