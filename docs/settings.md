# Settings

[← Parlor's README](../README.md)

Set these in `.env` (see `.env.example`). Each group has its own table.

## Folders and users

| Variable | Default | What it does |
|---|---|---|
| `DATA` | `/data` | Your media folder. Every app sees it as `/data`; music is `DATA/music`, YouTube downloads `DATA/youtube`. |
| `MOVIES`, `SHOWS`, `MUSIC`, `BOOKS`, `GAMES`, `YOUTUBE`, `DOWNLOADS` | `DATA/<kind>` | Where that kind lives (another disk, an existing library). Every app sees it as `/data/<kind>`. See [Folders per kind](install.md#folders-per-kind). |
| `PUID`, `PGID` | `1000` | The user and group that own `DATA` (`id` shows yours). The apps run as them, and folders Parlor makes belong to them. |

## Network

| Variable | Default | What it does |
|---|---|---|
| `LAN_SUBNET` | `192.168.0.0/16` | Your home network (comma-separated for more than one): qBittorrent's page is reachable through the VPN from it and skips its login there, and a [TV set](tv.md#the-tv-set-itself-power-input-and-volume)'s address in it is taken as your home network even where Docker's own network overlaps |
| `PORT` | `8800` | Parlor's port |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Behind a reverse proxy: its address as Parlor sees it (comma-separated for more than one; `*` only when nothing but the proxy can reach Parlor). See below. |

Parlor then takes each visitor's address from `X-Forwarded-For`, so the limits on guessing passwords and TV codes count each person, not the proxy. From any other address the header is ignored.

## Other apps

| Variable | Default | What it does |
|---|---|---|
| `SEERR_URL`, `SEERR_API_KEY` | set by the compose | For a Seerr, Overseerr or Jellyseerr elsewhere. Settings can set them. |
| `JELLYFIN_URL` | set by the compose | Turns on sign-in with Jellyfin. music-fndr also uses it, with `JELLYFIN_API_KEY`, to make playlists in Jellyfin. |
| `SONARR_URL`, `SONARR_API_KEY` (and `RADARR_`, `LIDARR_`, `PROWLARR_`, `QBITTORRENT_URL`) | the compose's services | For apps that run somewhere else |
| `PUBLIC_INDEXERS` | `yts,eztv,thepiratebay,1337x,limetorrents,nyaasi,knaben` | Public indexers Prowlarr gets (Prowlarr's names); `none` adds none |

## Accounts and data

| Variable | Default | What it does |
|---|---|---|
| `OPEN_SIGNUP` | `0` | `1` lets anyone who can reach Parlor create an account |
| `ENCRYPT` | `1` | Parlor's data encrypted at rest; `0` keeps a new install plain. See [Encrypted at rest](security.md#encrypted-at-rest). |
| `KEY_DIR` | `./parlor/key` in the compose, else `DATA_DIR/key` | Where the encryption key lives. Keep it apart from the data, and back it up. |

## Videos and updates

| Variable | Default | What it does |
|---|---|---|
| `YT_PARALLEL` | `2` | Videos downloading at once |
| `VIDEO_KEEP_DAYS` | `0` | Delete downloaded videos this many days after they're downloaded; `0` keeps them. Settings can change it. |
| `UPDATE_HOURS` | `24` | How often yt-dlp is updated while running (it's also updated on every start). `0` turns both off. |
| `HEAL` | `1` | When YouTube changes something and downloads start failing, update yt-dlp straight away instead of at the next check. `0` turns that off (so does `UPDATE_HOURS=0`). |

## Games and devices

| Variable | Default | What it does |
|---|---|---|
| `GAMES_STREAM`, `GAMES_STREAM_HOST` | off | Any value (leave it out for off) and this server's address on your network turn on Library → Games → From your PC, with `COMPOSE_PROFILES=games-stream` (see [Games](games.md)) |
| `OFFLINE` | on | `off` makes the app's copy on phones and computers remove itself (see [On phones, tablets and computers](devices.md)) |
| `TZ` | `Etc/UTC` | Seerr's time zone |

music-fndr's own settings (`QUALITY`, `FORMAT`, `BITRATE`, `OUTPUT`, `THREADS`, `SYNC_HOURS`, `YT_DLP_ARGS`, `JELLYFIN_API_KEY`, `JELLYFIN_USER`) work as described in [its README](https://github.com/ChaseSunstrom/music-fndr#settings).

Per-device choices (playback quality, autoplay, captions language, SponsorBlock categories, Shorts) are in Settings → Playing videos.
