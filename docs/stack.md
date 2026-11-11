# The apps around Parlor

[← Parlor's README](../README.md)

One `docker compose` file runs Parlor and the whole stack around it: Sonarr, Radarr, Lidarr, Prowlarr, FlareSolverr, qBittorrent behind a Gluetun VPN, Bazarr, Jellyfin, Seerr, Cleanuparr, Questarr and Checkrr. **Parlor sets them all up itself**: you don't sign in to each app, copy API keys or add indexers.

## What setup does

Once you have an account, Parlor, in the background:

- reads every app's API key from its config folder;
- turns off logins **on your home network** in Sonarr, Radarr, Lidarr, Prowlarr, qBittorrent and Cleanuparr (from outside, the others take your Parlor name and password; qBittorrent keeps its own, which it prints in its log the first time);
- gives Sonarr, Radarr and Lidarr their folders (`/data/shows`, `/data/movies`, `/data/music`) and qBittorrent as their download client, each with its own category, saving to `/data/downloads`;
- connects Prowlarr to all three, adds FlareSolverr for sites behind Cloudflare, and adds public indexers, keeping only the ones that answer (tried again on every run);
- **keeps the indexer list fresh by itself**:
  - twice a day it tries more of the public torrent sites Prowlarr knows (Prowlarr updates its own list of them);
  - it adds ones that work, up to 15;
  - it switches off ones that have failed for three days and turns them back on once they answer;
  - one you delete or switch off yourself stays that way, and a Prowlarr that only has indexers of your own is left alone;
- finishes Jellyfin's first-run setup with **your name and password** as its admin and adds libraries for movies, shows, music, YouTube downloads and books;
- sets up Seerr through that Jellyfin account, with its libraries, Radarr and Sonarr;
- connects Bazarr (subtitles) to Sonarr and Radarr, and Cleanuparr to the *arrs and qBittorrent;
- makes Questarr's (games) login itself, with nothing typed, and gives it qBittorrent as its download client, Prowlarr's indexers and the games folder (see [Games you ask for](games.md#games-you-ask-for-questarr));
- gives Checkrr your library folders and the keys of Sonarr, Radarr and Lidarr, so a film, episode or song that no longer plays is fetched again (see [Broken files](#broken-files-checkrr));
- gives Sonarr, Radarr and Lidarr a **Best available** quality profile:
  - it takes every quality except disc images, raw captures, cinema recordings and WAV;
  - upgrades are on, up to 4K remux or lossless;
  - Seerr's requests and Parlor's music requests use it;
  - Best available can mean 4K files of 40 GB or more;
  - Settings → Services → Download quality switches back to each app's own profile; a profile you pick yourself in Seerr or Lidarr afterwards is left alone;
- has each app search for **better copies once a week** (their RSS sync grabs new ones as they appear);
- turns on Cleanuparr's cleaners:
  - downloads that fail to import, stall, crawl (an hour under 20 KB/s) or never get their metadata are removed, blocklisted and **searched for again**;
  - junk and malware files in torrents are blocked (Cleanuparr's own list), and a torrent goes only if all of it is junk;
  - finished torrents are cleared after seeding to ratio 2 or for two weeks (your library keeps its copy);
  - each is set up once: change or delete it in Cleanuparr and it stays that way.

## Settings → Services

**Settings → Services** shows each app, whether it answers, and what setup did, step by step. **Set up again** re-checks everything and puts back anything that went missing. It only ever adds what's missing and never touches a login you set yourself. On later restarts Parlor re-checks what it can without your password.

## Torrents

**Torrents** shows qBittorrent's torrents live: progress, speeds, time left, ratio. It is for admins; the link is in the Downloads drawer and next to qBittorrent in Services.

- **Each torrent**: pause and resume (one or all), check the files again, ask the trackers for more peers, change the category, and remove a torrent with or without its files.
- **Limits**: set download and upload limits, turn on slow mode (qBittorrent's alternative limits) and add a magnet link.
- **Open qBittorrent**: opens qBittorrent's own page at `/qbit/` for everything else, signed in through Parlor. Only admins get in, and there's no second login.
- **Outside Parlor's compose**: set `QBITTORRENT_URL`, `QBITTORRENT_USER` and `QBITTORRENT_PASSWORD`.
- **Session**: Parlor keeps qBittorrent's session to itself (and after a wrong password waits a minute before trying again, so qBittorrent doesn't ban it).
- **Caution**: qBittorrent's page runs on Parlor's address, so a flaw in qBittorrent's own page could act as the admin in Parlor; keep qBittorrent up to date.
- **Port**: qBittorrent's port (8080) is also open on your home network, where qBittorrent skips its login.

## What the apps are doing

What the apps are doing, in Parlor:

- **Downloads drawer**: lists what Sonarr, Radarr and Lidarr are fetching beside your videos and songs: progress, time left, and why one is stuck (a download that won't import says so in red).
- **Movies & TV**: opens with *Downloading now* and *Coming up*, the episodes and films due in the next two weeks; each opens its page in Parlor.

### Request music

**Music → Request music** sends albums and artists to Lidarr, since Seerr has no Lidarr support.

- Search, then ask for one album (only it is wanted and searched for, not the artist's whole catalogue) or a whole artist; asking again searches again.
- Anyone signed in can request, as with Seerr.
- **A request never just sits there:** Parlor follows each album through searching, downloading and importing.

### When Lidarr can't find it

Parlor looks for the album on Spotify when any of these happens:

- Lidarr finds nothing in 6 hours;
- two of its downloads fail;
- its download (or its import) doesn't move for 12 hours.

With one sure match:

- Parlor stops Lidarr looking (the album is unwatched and its downloads removed and blocklisted).
- It gets the album from YouTube with music-fndr into `/data/downloads/parlor-music`.
- It tags it with the album's MusicBrainz ids and has Lidarr import it, telling Lidarr which track each file is (its own matching turns down most YouTube copies).
- Lidarr then counts it as there, watches it again and can still upgrade it later.

If more than one Spotify album could be it, or none, you pick, and Lidarr keeps looking meanwhile.

### Your requests

- **Your requests** on the same page shows each one's state and why, with **Retry** and **Get it from YouTube** (don't wait for Lidarr).
- Lidarr's own wanted list (albums added in Lidarr, or by watching an artist) is followed too and shown to admins.
- It goes to YouTube only if an admin turns that on (Settings → Music requests), a few albums at a time.

### Books

Readarr, for books, is retired upstream, so the stack has none; the Books library in Jellyfin reads whatever you put in `/data/books`.

## The VPN

qBittorrent reaches the internet only through Gluetun, and Gluetun needs your VPN provider's WireGuard file. Upload it any time, with everything running: **Settings → Services → VPN → Upload a WireGuard file**.

- Until there is one, **nothing gets out**: qBittorrent can talk to your home network and the other apps, not the internet, so nothing downloads in the open while you set up.
- Uploading another file switches servers (Gluetun restarts on it); removing it blocks again.
- If the tunnel drops, Gluetun's kill switch keeps qBittorrent offline until it's back.

### The file

- Parlor checks the file before saving it: a `[Interface]` with `PrivateKey` and `Address`, one `[Peer]` with `PublicKey` and `Endpoint`.
- It looks up a host name in `Endpoint`, since Gluetun needs an address.
- The file is readable by root only and never shown again.

### Without a VPN

Don't want a VPN?

- **Run without a VPN…** in the same place asks you to confirm, then lets qBittorrent connect directly. Your home's address is then visible to everyone you download from.
- **Turn the VPN back on** blocks again.
- Without Parlor's page: `touch gluetun/Parlor-vpn-off` does the same, and deleting it undoes it.
- A first version of this compose read `WIREGUARD_CONF` from `.env`; that setting is gone: upload the file, or put it at `gluetun/wireguard/wg0.conf`.

## Broken files (Checkrr)

Disks go bad and downloads arrive damaged. [Checkrr](https://github.com/aetaric/checkrr) reads every film, episode and song in `MOVIES`, `SHOWS` and `MUSIC` **every night at 04:00** (your `TZ`) and checks each is still a video or audio file a player can open.

- A file that isn't is **deleted by the app that owns it (Sonarr, Radarr or Lidarr) and searched for again**, so a fresh copy arrives the usual way.
- Files it already checked and that haven't changed are skipped, so later nights are quick.
- It looks only at the three library folders, never at downloads in progress, and it can only read them: the deleting is done by the *arr, and only for a file that *arr knows. Anything else it can't read is just listed.
- Subtitles, pictures, lyrics and other files beside your media are ignored.
- **Settings → Services** shows the last scan (how many files, how many broken, how many replaced) and links to Checkrr's own page at port 8585, which lists the broken files. That page has no login: like the other apps' pages, keep its port on your home network.
- **To make it report only**, untick **Replace broken files** there: Checkrr then lists what it finds and deletes nothing. Do this first if your library has unusual formats, and look at what a scan lists before ticking it again. Once it is ticked again, the next scan replaces what was listed.

### Checkrr's config

- Parlor writes Checkrr's config (`./checkrr/config/checkrr.yaml`) with the stack's own Sonarr, Radarr and Lidarr; an *arr of yours elsewhere isn't added, as it may see other paths.
- To change the schedule or anything else, edit that file and delete its first line (`# Made by Parlor…`): Parlor then leaves the file alone, and Checkrr restarts by itself whenever the file changes.
- A truncated file that still opens is not caught: Checkrr's deeper pass (`ffmpeg-full` in that file) decodes every file end to end and finds those, at the cost of hours of CPU.

## Games you ask for (Questarr)

Questarr finds and downloads games, with no IGDB keys of yours: see [Games](games.md#games-you-ask-for-questarr).
