# Parlor

_Formerly yeerr._ A room for watching, listening and playing: YouTube, films and shows, music, books and the apps that fetch them, in one place.

YouTube, movies and TV, and your music library, in one self-hosted app with accounts.

- **Watch**: YouTube without ads, in up to 4K. Search, channels, playlists, your subscriptions feed, recommendations, continue watching, watch later and history. Fast downloads with chapters, subtitles and the thumbnail built in, or just the audio, straight into your music library.
- **Movies & TV**: discover, search and request films and shows through [Seerr](https://github.com/seerr-team/seerr). Each person requests as their own Seerr user. Trailers play in the ad-free player.
- **Music**: all of [music-fndr](https://github.com/ChaseSunstrom/music-fndr). Browse Spotify and download songs, albums, playlists and whole discographies into your music folder, with synced playlists and library tidying.

One `docker compose` file runs Parlor and the whole stack around it: Sonarr, Radarr, Lidarr, Prowlarr, FlareSolverr, qBittorrent behind a Gluetun VPN, Bazarr, Jellyfin, Seerr and Cleanuparr. **Parlor sets them all up itself**: you don't sign in to each app, copy API keys or add indexers.

## Run it

```sh
cp .env.example .env        # set DATA (your media folder), PUID/PGID and TZ
docker compose up -d --build
```

It needs Docker Compose 2.24.3 or newer (`docker compose version` says which you have).

Open `http://<server>:8800`, create your account, and upload your VPN's WireGuard file in **Settings → Services → VPN** ([more](#the-vpn)). Parlor then, in the background:

- reads every app's API key from its config folder;
- turns off logins **on your home network** in Sonarr, Radarr, Lidarr, Prowlarr, qBittorrent and Cleanuparr (from outside, the others take your Parlor name and password; qBittorrent keeps its own, which it prints in its log the first time);
- gives Sonarr, Radarr and Lidarr their folders (`/data/shows`, `/data/movies`, `/data/music`) and qBittorrent as their download client, each with its own category, saving to `/data/downloads`;
- connects Prowlarr to all three, adds FlareSolverr for sites behind Cloudflare, and adds public indexers, keeping only the ones that answer (tried again on every run); then **keeps the list fresh by itself**: twice a day it tries more of the public torrent sites Prowlarr knows (Prowlarr updates its own list of them), adds ones that work, up to 15, switches off ones that have failed for three days and turns them back on once they answer. One you delete or switch off yourself stays that way, and a Prowlarr that only has indexers of your own is left alone;
- finishes Jellyfin's first-run setup with **your name and password** as its admin and adds libraries for movies, shows, music, YouTube downloads and books;
- sets up Seerr through that Jellyfin account, with its libraries, Radarr and Sonarr;
- connects Bazarr (subtitles) to Sonarr and Radarr, and Cleanuparr to the *arrs and qBittorrent;
- gives Sonarr, Radarr and Lidarr a **Best available** quality profile (every quality except disc images, raw captures, cinema recordings and WAV; upgrades on, up to 4K remux or lossless), which Seerr's requests and Parlor's music requests use, and has each app search for **better copies once a week** (their RSS sync grabs new ones as they appear). Settings → Services → Download quality switches back to each app's own profile; a profile you pick yourself in Seerr or Lidarr afterwards is left alone. Best available can mean 4K files of 40 GB or more;
- turns on Cleanuparr's cleaners: downloads that fail to import, stall, crawl (an hour under 20 KB/s) or never get their metadata are removed, blocklisted and **searched for again**; junk and malware files in torrents are blocked (Cleanuparr's own list), and a torrent goes only if all of it is junk; finished torrents are cleared after seeding to ratio 2 or for two weeks (your library keeps its copy). Each is set up once: change or delete it in Cleanuparr and it stays that way.

**Torrents** (admins; the link is in the Downloads drawer and next to qBittorrent in Services) shows qBittorrent's torrents live: progress, speeds, time left, ratio. You can pause and resume them (one or all), check the files again, ask the trackers for more peers, change the category, and remove a torrent with or without its files. It also sets download and upload limits, turns on slow mode (qBittorrent's alternative limits) and adds a magnet link. **Open qBittorrent** opens qBittorrent's own page at `/qbit/` for everything else, signed in through Parlor: only admins get in, and there's no second login. Outside Parlor's compose, set `QBITTORRENT_URL`, `QBITTORRENT_USER` and `QBITTORRENT_PASSWORD`; Parlor keeps qBittorrent's session to itself (and after a wrong password waits a minute before trying again, so qBittorrent doesn't ban it). qBittorrent's page runs on Parlor's address, so a flaw in qBittorrent's own page could act as the admin in Parlor; keep qBittorrent up to date. Its port (8080) is also open on your home network, where qBittorrent skips its login.

**What the apps are doing, in Parlor.** The Downloads drawer lists what Sonarr, Radarr and Lidarr are fetching beside your videos and songs: progress, time left, and why one is stuck (a download that won't import says so in red). **Movies & TV** opens with *Downloading now* and *Coming up*, the episodes and films due in the next two weeks; each opens its page in Parlor. **Music → Request music** sends albums and artists to Lidarr, since Seerr has no Lidarr support: search, then ask for one album (only it is wanted and searched for, not the artist's whole catalogue) or a whole artist; asking again searches again. Anyone signed in can request, as with Seerr. Readarr, for books, is retired upstream, so the stack has none; the Books library in Jellyfin reads whatever you put in `/data/books`.

**Settings → Services** shows each app, whether it answers, and what setup did, step by step. **Set up again** re-checks everything and puts back anything that went missing. It only ever adds what's missing and never touches a login you set yourself. On later restarts Parlor re-checks what it can without your password.

Your media folder ends up like this (every container sees the same `/data/<kind>` paths):

```
DATA/
  downloads/   qBittorrent, a folder per category
  movies/  shows/  music/  youtube/  books/  games/
```

`games/` is kept for the games apps to come; nothing uses it yet.

### Folders per kind

Each kind can live somewhere else, such as another disk or a library you already have. Set any of `MOVIES`, `SHOWS`, `MUSIC`, `BOOKS`, `GAMES`, `YOUTUBE` and `DOWNLOADS` in `.env`; each defaults to `DATA/<kind>`. Inside every container that uses it, the folder still appears at `/data/<kind>`, so the apps' settings (root folders, Jellyfin libraries, qBittorrent's save path) don't change.

```
DATA=/srv/media            # everything not listed below
MOVIES=/mnt/bigdisk/films  # an existing library: Radarr, Jellyfin and Bazarr find it at /data/movies
```

To use a library you already have, point its kind at it (`MOVIES=/path/to/your/movies`) and run `docker compose up -d`; the apps' root folders and Jellyfin's libraries already point at `/data/<kind>`, so they find what's there (Settings → Services → Set up again re-checks them). The folder must be readable and writable by `PUID:PGID`.

**Moves between different disks are copies, not instant.** Left unset, every kind is a folder inside `DATA`, all in one mount, so a finished download reaching your library (`downloads` to `movies`, `shows` or `music`) is an instant move or a hardlink that takes no extra space while it seeds. A kind set in `.env` is mounted on its own, and Linux treats two mounts as different places even on one disk: moving into or out of it copies the file, and while the torrent seeds it takes twice the space. So set a kind only for a folder outside `DATA`, such as another disk or an existing library; a folder already inside `DATA` is simply `/data/<that folder>`.

Two things to do before you set one:

- **Mount the disk first.** If the path isn't there when the stack starts, Docker makes an empty folder at it on your system disk, and downloads fill that disk instead.
- **Move what's already in `DATA/<kind>` there.** Once a kind is set, the apps see only its new folder; what's left in `DATA/<kind>` is hidden from them, and Radarr or Sonarr would think it's missing and search for it again.

### Moving over from your own compose

The compose keeps each app's config in `./<app>/config`, the usual hand-made layout. Stop your old stack (`docker compose down` in its folder), copy those `config` folders next to this compose (and your `wg0.conf` to `gluetun/wireguard/`, or upload it later), set `DATA` to the folder you mounted as `/data` before, and start this one. Everything you set up stays; Parlor adds only what's missing: an app that already has a root folder, indexers or a login keeps them, and an indexer you delete stays deleted.

### Upgrading from yeerr

Parlor was formerly yeerr. Running it from this folder already? Stop it under its old project name, then start Parlor:

```sh
docker compose -p yeerr down   # formerly yeerr: the old containers would hold the ports
docker compose up -d --build
```

Setup moves `./yeerr/config` to `./parlor/config` before Parlor starts (formerly yeerr), so accounts and settings come along; sign-ins, open pages and saved preferences from before keep working. A reverse proxy that pointed at the service name `yeerr` should point at `parlor` now (formerly yeerr).

From the very first version (formerly yeerr, the one with the `yeerr-data` volume), copy its accounts and Seerr's settings out of the Docker volumes once, before the first start (`docker volume ls` shows the exact names):

```sh
docker compose -p Parlor down
mkdir -p parlor/config seerr/config
docker run --rm -v yeerr_yeerr-data:/from -v "$PWD/parlor/config:/to" alpine sh -c 'cp -a /from/. /to/ && chown -R 1000:1000 /to'   # formerly yeerr
docker run --rm -v yeerr_seerr-config:/from -v "$PWD/seerr/config:/to" alpine sh -c 'cp -a /from/. /to/ && chown -R 1000:1000 /to'   # formerly yeerr
```

Then move your music to `$DATA/music` and downloaded videos to `$DATA/youtube` (use your `PUID:PGID` if they aren't 1000).

### The VPN

qBittorrent reaches the internet only through Gluetun, and Gluetun needs your VPN provider's WireGuard file. Upload it any time, with everything running: **Settings → Services → VPN → Upload a WireGuard file**. Until there is one, **nothing gets out**: qBittorrent can talk to your home network and the other apps, not the internet, so nothing downloads in the open while you set up. Uploading another file switches servers (Gluetun restarts on it); removing it blocks again. If the tunnel drops, Gluetun's kill switch keeps qBittorrent offline until it's back.

Parlor checks the file before saving it (a `[Interface]` with `PrivateKey` and `Address`, one `[Peer]` with `PublicKey` and `Endpoint`) and looks up a host name in `Endpoint`, since Gluetun needs an address. The file is readable by root only and never shown again.

Don't want a VPN? **Run without a VPN…** in the same place asks you to confirm, then lets qBittorrent connect directly (your home's address is then visible to everyone you download from). **Turn the VPN back on** blocks again. Without Parlor's page: `touch gluetun/Parlor-vpn-off` does the same, and deleting it undoes it. (A first version of this compose read `WIREGUARD_CONF` from `.env`; that setting is gone: upload the file, or put it at `gluetun/wireguard/wg0.conf`.)

Coming from music-fndr? Stop its container first: Parlor uses the same port (8800). Point `DATA` at the folder that holds your music as `music/`; Parlor picks up music-fndr's queue and synced playlists from `music/.music-findr/`.

## Accounts

The first account is the admin, created on first visit (nobody can sign in, not even with Jellyfin, until it exists). After that, accounts come from:

- **Settings → People → Add someone** (name and password), or
- **Jellyfin**: set the Jellyfin address in Settings (or `JELLYFIN_URL`). Anyone can then sign in with their Jellyfin name and password, and gets an account here the first time. Jellyfin admins are admins here. A Jellyfin account never takes over a local account with the same name.
- **Open sign-up**: a switch in Settings; off by default.

Each account has its own subscriptions, history, resume points, watch later and YouTube cookies. Downloads and the music library are shared.

**Seerr users.** Requests go to Seerr as the person's own Seerr user, so Seerr's quotas, permissions and approvals apply. People who sign in with Jellyfin are linked to the Seerr user with the same Jellyfin account by themselves. Everyone else is linked by an admin in Settings → People, which points out a Seerr user with the same name (names alone aren't trusted: with open sign-up anyone could pick one). Members who aren't linked can browse but can't request; an admin who isn't linked requests as the API key's owner.

## Watching YouTube

The player streams through your server. YouTube serves HD video and audio separately, and its stream links only work from the address that asked for them. Parlor fetches both, in byte ranges, and the player keeps them in step. Seeking, quality changes (144p to 4K, HDR when your browser can show it), captions, playback speed, picture-in-picture and your system's media keys all work.

- **SponsorBlock**: sponsors, self-promotion and "like and subscribe" segments are skipped (with Undo), and the seek bar shows every marked segment, the chapters and YouTube's "most replayed" curve. Choose categories in Settings. Lookups use a hash prefix, so SponsorBlock never learns which video you're watching.
- **Resume**: videos pick up where you left off, on any device.
- **Keys**: Space or K to play and pause, J and L to skip 10 s, ← and → for 5 s, ↑ and ↓ for volume, M to mute, F for full screen, T for theater mode, C for captions, < and > for speed, 0–9 to jump, Shift+N for the next video, / to search.
- Live streams open on YouTube.

### Browsing

You don't need links or an account to find something to watch. **Watch → Home** has:

- **Continue watching**, and **Because you watched …**: the videos YouTube suggests beside the ones you watched last.
- **New from your subscriptions**.
- **Topics**: a shelf of this week's most-watched videos for each topic you pick (Music, Gaming, Science… to start; **Edit topics** to change them). YouTube retired its Trending page, so this stands in for it. Each account has its own topics.

The player's **Up next** list is YouTube's own suggestions for that video, and autoplay follows it. Channels in search results have a Subscribe button.

### Subscriptions

Subscribe on any channel or video page, or import them in Watch → Channels from:

- Google Takeout (`subscriptions.csv`, under YouTube and YouTube Music)
- NewPipe or FreeTube (`.json`)
- Invidious or FreeTube (`.opml`)
- a list of channel links or `@handles`

The feed reads each channel's public RSS feed (no Google account involved) and falls back to yt-dlp when a feed is down. **Hide Shorts** filters them out. Export your list as OPML any time.

### Recommended

Without a YouTube account, Recommended mixes videos like the ones you watched last with new videos from the channels you watch most but don't follow, so no single video or channel takes over.

For YouTube's own recommendations, add your YouTube cookies in Settings. Export them with a "cookies.txt" browser extension while signed in to youtube.com. This also adds your YouTube feed and YouTube watch later, and plays videos that need a sign-in (age-restricted ones). The cookies are stored with owner-only permissions and used only for those requests. YouTube can flag accounts used this way, so a spare account is safer.

### Downloads

Use the download button on any video (choose Best up to 4K, 1080p, 720p or 480p), or **Download all** on a playlist.

- **Videos** go to `VIDEO_DIR/<channel>/<title> [<id>].mp4`, with chapters, English subtitles, the thumbnail and the description embedded. Jellyfin and Plex read them, and they play in Parlor from Watch → Downloaded. Up to 1080p Parlor prefers H.264 + AAC, which plays everywhere; higher qualities only exist as VP9 or AV1.
- **Cut sponsors out of downloaded videos** (Settings) removes SponsorBlock's sponsor segments from the file.
- **Save audio to music** puts a tagged audio file (artist, album, square cover art) in your music library, in music-fndr's format and bitrate. Non-music parts of music videos are cut out.
- Downloads resume after a restart. If YouTube rate-limits the server, they wait 15 minutes and try again instead of failing.
- **Clearing out old downloads** (Settings → Downloaded videos, off by default): delete each video a week, 30 days, 90 days… after it was downloaded, or only once someone has watched it to the end, counting from then. Press **Keep** on a video in Watch → Downloaded and it's never deleted automatically. Each video there says when it goes.

## Music apps (Subsonic)

Parlor speaks the Subsonic/OpenSubsonic API, so Symfonium, Tempo, DSub, Feishin and similar apps can play your music folder.

- **Server URL:** Parlor's address (`http://your-server:8800`); the API is under `/rest`. Apps that ask for a path use `/rest`.
- **Login:** your Parlor name and password work. For apps that send a salted token instead (most do), or a long-lived key, make an **app password** in **Settings → Music apps**: it's shown once. Use it as the app's password, or as its API key. Delete it there to cut that app off.
- Stars, ratings, plays, playlists, play queue and bookmarks are per person. Playlists from music-fndr's `Playlists/` folder show up read-only.
- Tracks are found by a hash of their path in the music folder. **Moving or renaming a file makes it a new track**: its stars, ratings and play counts don't follow it (they're deleted at the next scan, every 10 minutes, once the old path is gone).
- A scan that can't open a folder, or finds no music at all (an unmounted share), removes nothing; it logs a warning instead. Files whose names aren't valid UTF-8 are skipped.

## Settings

Set these in `.env` (see `.env.example`).

| Variable | Default | What it does |
|---|---|---|
| `DATA` | `/data` | Your media folder. Every app sees it as `/data`; music is `DATA/music`, YouTube downloads `DATA/youtube`. |
| `MOVIES`, `SHOWS`, `MUSIC`, `BOOKS`, `GAMES`, `YOUTUBE`, `DOWNLOADS` | `DATA/<kind>` | Where that kind lives (another disk, an existing library). Every app sees it as `/data/<kind>`. See [Folders per kind](#folders-per-kind). |
| `PUID`, `PGID` | `1000` | The user and group that own `DATA` (`id` shows yours). The apps run as them, and folders Parlor makes belong to them. |
| `LAN_SUBNET` | `192.168.0.0/16` | Your home network (comma-separated for more than one): qBittorrent's page is reachable through the VPN from it and skips its login there |
| `PUBLIC_INDEXERS` | `yts,eztv,thepiratebay,1337x,limetorrents,nyaasi,knaben` | Public indexers Prowlarr gets (Prowlarr's names); `none` adds none |
| `PORT` | `8800` | Parlor's port |
| `SEERR_URL`, `SEERR_API_KEY` | set by the compose | For a Seerr, Overseerr or Jellyseerr elsewhere. Settings can set them. |
| `JELLYFIN_URL` | set by the compose | Turns on sign-in with Jellyfin. music-fndr also uses it, with `JELLYFIN_API_KEY`, to make playlists in Jellyfin. |
| `SONARR_URL`, `SONARR_API_KEY` (and `RADARR_`, `LIDARR_`, `PROWLARR_`, `QBITTORRENT_URL`) | the compose's services | For apps that run somewhere else |
| `OPEN_SIGNUP` | `0` | `1` lets anyone who can reach Parlor create an account |
| `YT_PARALLEL` | `2` | Videos downloading at once |
| `VIDEO_KEEP_DAYS` | `0` | Delete downloaded videos this many days after they're downloaded; `0` keeps them. Settings can change it. |
| `UPDATE_HOURS` | `24` | How often yt-dlp is updated while running (it's also updated on every start). `0` turns both off. |
| `HEAL` | `1` | When YouTube changes something and downloads start failing, update yt-dlp straight away instead of at the next check. `0` turns that off (so does `UPDATE_HOURS=0`). |
| `TZ` | `Etc/UTC` | Seerr's time zone |

music-fndr's own settings (`FORMAT`, `BITRATE`, `OUTPUT`, `THREADS`, `SYNC_HOURS`, `YT_DLP_ARGS`, `JELLYFIN_API_KEY`, `JELLYFIN_USER`) work as described in [its README](https://github.com/ChaseSunstrom/music-fndr#settings).

Per-device choices (playback quality, autoplay, captions language, SponsorBlock categories, Shorts) are in Settings → Playing videos.

## Updating

YouTube changes often, and a yt-dlp a few weeks old starts failing. Parlor updates yt-dlp every time it starts, checks again every `UPDATE_HOURS`, and when there's a new one it restarts between downloads to load it. Docker brings it back and the queues carry on.

When YouTube changes something before then, there's nothing to do either. A download, video or search that fails because of it (not a rate limit, not a removed or private video) makes Parlor update yt-dlp straight away, at most once an hour, to yt-dlp's nightly build when the regular release has no fix yet; it moves back to the regular release once a newer one is out. Those downloads, videos and songs alike, show "YouTube changed something; waiting for a yt-dlp update" and wait instead of failing. yt-dlp is replaced only once the running downloads have finished, new ones hold until Parlor restarts to load it, then they all carry on (each waits at most three times before it's marked failed). `HEAL=0` turns this off. If yt-dlp can't be installed there (a read-only image), Parlor says so once in its log and stops trying until it restarts; without internet for a while, it simply tries again on the next failure an hour later.

To update everything else:

```sh
docker compose pull && docker compose build --pull && docker compose up -d
```

## Security

- Every API call needs a session. Sessions last 30 days, renew while used, and are stored hashed. Passwords are hashed with scrypt.
- Every change needs an `X-Requested-With: parlor` header (formerly yeerr, still accepted), which other websites can't send, so a page you visit can't act for you.
- Wrong passwords are throttled per name and per address.
- Changing a password signs out your other devices; Settings also has **Sign out everywhere**.
- Put Parlor behind HTTPS (a reverse proxy) if it's reachable from outside your home. The session cookie is marked secure when the proxy says the request came over HTTPS.
- The *arrs skip their logins for any private address, qBittorrent only for `LAN_SUBNET` and the other containers. Anyone on your home network can use them, so keep untrusted devices on a guest network, and don't forward their ports to the internet; from outside, they ask for a name and password. Your password is used for setup only while it runs, and never stored.

## Development

```sh
uv venv -p 3.12 .venv && VIRTUAL_ENV=.venv uv pip install "spotdl~=4.5.2" "spotapi~=1.2.8" "httpx<0.28" -U "yt-dlp[default]"
mkdir -p dev/music && cd dev/music && DATA_DIR=../data VIDEO_DIR=../videos ../../.venv/bin/uvicorn --app-dir ../../app app:app --reload --port 8800
cd app && ../.venv/bin/python -m unittest discover -s ../tests   # unit tests, no network
python3 tests/smoke.py http://127.0.0.1:8800                     # live check against a running Parlor
```

- `app/app.py` puts it together. `auth.py` is accounts and settings, `yt.py` is YouTube, `seerr.py` is Seerr, and `core.py` is the database and sessions.
- `app/music.py` is music-fndr's backend, copied by `scripts/vendor-music.sh ../music-fndr`. Change it in music-fndr, then copy it again.
- `app/static/` is the whole UI, with no build step. `js/music.js` is music-fndr's page code.
- [DESIGN.md](DESIGN.md) describes the look.
