# yeerr

YouTube, movies and TV, and your music library, in one self-hosted app with accounts.

- **Watch**: YouTube without ads, in up to 4K. Search, channels, playlists, your subscriptions feed, recommendations, continue watching, watch later and history. Fast downloads with chapters, subtitles and the thumbnail built in, or just the audio, straight into your music library.
- **Movies & TV**: discover, search and request films and shows through [Seerr](https://github.com/seerr-team/seerr). Each person requests as their own Seerr user. Trailers play in the ad-free player.
- **Music**: all of [music-fndr](https://github.com/ChaseSunstrom/music-fndr). Browse Spotify and download songs, albums, playlists and whole discographies into your music folder, with synced playlists and library tidying.

One `docker compose` file runs yeerr and Seerr.

## Run it

```sh
cp .env.example .env        # set MUSIC_DIR and VIDEO_DIR
docker compose up -d --build
```

1. **Seerr**: open `http://<server>:5055` and do its setup (sign in with Jellyfin, Plex or Emby; add Radarr and Sonarr). Copy the API key from Seerr → Settings → General.
2. **yeerr**: open `http://<server>:8800` and create the admin account. In Settings, paste Seerr's API key and choose Save and test.
3. Add everyone else in Settings → People, or let them sign in with Jellyfin (see below).

Already running Seerr, Overseerr or Jellyseerr? Delete the `seerr` service from `docker-compose.yml` and set `SEERR_URL` to it. All three speak the same API.

Coming from music-fndr? Stop its container first: yeerr uses the same port (8800) and the same music folder, and picks up music-fndr's queue and synced playlists from `/music/.music-findr/`.

## Accounts

The first account is the admin, created on first visit (nobody can sign in, not even with Jellyfin, until it exists). After that, accounts come from:

- **Settings → People → Add someone** (name and password), or
- **Jellyfin**: set the Jellyfin address in Settings (or `JELLYFIN_URL`). Anyone can then sign in with their Jellyfin name and password, and gets an account here the first time. Jellyfin admins are admins here. A Jellyfin account never takes over a local account with the same name.
- **Open sign-up**: a switch in Settings; off by default.

Each account has its own subscriptions, history, resume points, watch later and YouTube cookies. Downloads and the music library are shared.

**Seerr users.** Requests go to Seerr as the person's own Seerr user, so Seerr's quotas, permissions and approvals apply. People who sign in with Jellyfin are linked to the Seerr user with the same Jellyfin account by themselves. Everyone else is linked by an admin in Settings → People, which points out a Seerr user with the same name (names alone aren't trusted: with open sign-up anyone could pick one). Members who aren't linked can browse but can't request; an admin who isn't linked requests as the API key's owner.

## Watching YouTube

The player streams through your server. YouTube serves HD video and audio separately, and its stream links only work from the address that asked for them. yeerr fetches both, in byte ranges, and the player keeps them in step. Seeking, quality changes (144p to 4K, HDR when your browser can show it), captions, playback speed, picture-in-picture and your system's media keys all work.

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

- **Videos** go to `VIDEO_DIR/<channel>/<title> [<id>].mp4`, with chapters, English subtitles, the thumbnail and the description embedded. Jellyfin and Plex read them, and they play in yeerr from Watch → Downloaded. Up to 1080p yeerr prefers H.264 + AAC, which plays everywhere; higher qualities only exist as VP9 or AV1.
- **Cut sponsors out of downloaded videos** (Settings) removes SponsorBlock's sponsor segments from the file.
- **Save audio to music** puts a tagged audio file (artist, album, square cover art) in your music library, in music-fndr's format and bitrate. Non-music parts of music videos are cut out.
- Downloads resume after a restart. If YouTube rate-limits the server, they wait 15 minutes and try again instead of failing.

## Settings

Set these in `.env` (see `.env.example`).

| Variable | Default | What it does |
|---|---|---|
| `MUSIC_DIR` | `/data/music` | Your music folder (music-fndr's library) |
| `VIDEO_DIR` | `/data/videos` | Where downloaded videos go |
| `PORT` | `8800` | yeerr's port. It uses the host's network, like music-fndr. |
| `SEERR_URL` | `http://localhost:5055` | Seerr's address, from the server's point of view. Settings can change it. |
| `SEERR_API_KEY` | | Seerr's API key. Settings can set it instead. |
| `JELLYFIN_URL` | | Turns on sign-in with Jellyfin. music-fndr also uses it, with `JELLYFIN_API_KEY`, to make playlists in Jellyfin. |
| `OPEN_SIGNUP` | `0` | `1` lets anyone who can reach yeerr create an account |
| `YT_PARALLEL` | `2` | Videos downloading at once |
| `UPDATE_HOURS` | `24` | How often yt-dlp is updated while running (it's also updated on every start). `0` turns both off. |
| `TZ` | `Etc/UTC` | Seerr's time zone |

music-fndr's own settings (`FORMAT`, `BITRATE`, `OUTPUT`, `THREADS`, `SYNC_HOURS`, `YT_DLP_ARGS`, `JELLYFIN_API_KEY`, `JELLYFIN_USER`) work as described in [its README](https://github.com/ChaseSunstrom/music-fndr#settings).

Per-device choices (playback quality, autoplay, captions language, SponsorBlock categories, Shorts) are in Settings → Playing videos.

## Updating

YouTube changes often, and a yt-dlp a few weeks old starts failing. yeerr updates yt-dlp every time it starts, checks again every `UPDATE_HOURS`, and when there's a new one it restarts between downloads to load it. Docker brings it back and the queues carry on. To update everything else:

```sh
docker compose pull && docker compose build --pull && docker compose up -d
```

## Security

- Every API call needs a session. Sessions last 30 days, renew while used, and are stored hashed. Passwords are hashed with scrypt.
- Every change needs an `X-Requested-With: yeerr` header, which other websites can't send, so a page you visit can't act for you.
- Wrong passwords are throttled per name and per address.
- Changing a password signs out your other devices; Settings also has **Sign out everywhere**.
- Put yeerr behind HTTPS (a reverse proxy) if it's reachable from outside your home. The session cookie is marked secure when the proxy says the request came over HTTPS.

## Development

```sh
uv venv -p 3.12 .venv && VIRTUAL_ENV=.venv uv pip install "spotdl~=4.5.2" "spotapi~=1.2.8" "httpx<0.28" -U "yt-dlp[default]"
mkdir -p dev/music && cd dev/music && DATA_DIR=../data VIDEO_DIR=../videos ../../.venv/bin/uvicorn --app-dir ../../app app:app --reload --port 8800
cd app && ../.venv/bin/python -m unittest discover -s ../tests   # unit tests, no network
python3 tests/smoke.py http://127.0.0.1:8800                     # live check against a running yeerr
```

- `app/app.py` puts it together. `auth.py` is accounts and settings, `yt.py` is YouTube, `seerr.py` is Seerr, and `core.py` is the database and sessions.
- `app/music.py` is music-fndr's backend, copied by `scripts/vendor-music.sh ../music-fndr`. Change it in music-fndr, then copy it again.
- `app/static/` is the whole UI, with no build step. `js/music.js` is music-fndr's page code.
- [DESIGN.md](DESIGN.md) describes the look.
