# Install, folders and moving in

[← Parlor's README](../README.md)

Starting Parlor, where your media lives, and bringing an older setup along.

## Start it

You need Docker Compose 2.24.3 or newer (`docker compose version` says which you have).

1. Copy the example settings and edit them. Set `DATA` (your media folder), `PUID`/`PGID` and `TZ`:

   ```sh
   cp .env.example .env        # set DATA (your media folder), PUID/PGID and TZ
   ```

2. Start the stack:

   ```sh
   docker compose up -d --build
   ```

3. Open `http://<server>:8800` and create your account.
4. Upload your VPN's WireGuard file in **Settings → Services → VPN** ([more](stack.md#the-vpn)).
5. Wait a little: Parlor then sets up every app in the background ([what setup does](stack.md#what-setup-does)).

## Your media folder

Your media folder ends up like this (every container sees the same `/data/<kind>` paths):

```
DATA/
  downloads/   qBittorrent, a folder per category
  movies/  shows/  music/  youtube/  books/  games/
```

`games/` holds retro games for Games → Retro, in `games/roms/<system>/` (see [Games](games.md)).

### Folders per kind

Each kind can live somewhere else, such as another disk or a library you already have.

- Set any of `MOVIES`, `SHOWS`, `MUSIC`, `BOOKS`, `GAMES`, `YOUTUBE` and `DOWNLOADS` in `.env`; each defaults to `DATA/<kind>`.
- Inside every container that uses it, the folder still appears at `/data/<kind>`, so the apps' settings (root folders, Jellyfin libraries, qBittorrent's save path) don't change.

```
DATA=/srv/media            # everything not listed below
MOVIES=/mnt/bigdisk/films  # an existing library: Radarr, Jellyfin and Bazarr find it at /data/movies
```

To use a library you already have:

1. Point its kind at it (`MOVIES=/path/to/your/movies`).
2. Run `docker compose up -d`. The apps' root folders and Jellyfin's libraries already point at `/data/<kind>`, so they find what's there (Settings → Services → Set up again re-checks them).

The folder must be readable and writable by `PUID:PGID`.

**Moves between different disks are copies, not instant.**

- **Left unset**: every kind is a folder inside `DATA`, all in one mount. A finished download reaching your library (`downloads` to `movies`, `shows` or `music`) is an instant move or a hardlink that takes no extra space while it seeds.
- **Set in `.env`**: the kind is mounted on its own, and Linux treats two mounts as different places even on one disk. Moving into or out of it copies the file, and while the torrent seeds it takes twice the space.
- **So**: set a kind only for a folder outside `DATA`, such as another disk or an existing library. A folder already inside `DATA` is simply `/data/<that folder>`.

Two things to do before you set one:

- **Mount the disk first.** If the path isn't there when the stack starts, Docker makes an empty folder at it on your system disk, and downloads fill that disk instead.
- **Move what's already in `DATA/<kind>` there.** Once a kind is set, the apps see only its new folder; what's left in `DATA/<kind>` is hidden from them, and Radarr or Sonarr would think it's missing and search for it again.

### Moving over from your own compose

The compose keeps each app's config in `./<app>/config`, the usual hand-made layout.

1. Stop your old stack (`docker compose down` in its folder).
2. Copy those `config` folders next to this compose (and your `wg0.conf` to `gluetun/wireguard/`, or upload it later).
3. Set `DATA` to the folder you mounted as `/data` before.
4. Start this stack.

Everything you set up stays; Parlor adds only what's missing. An app that already has a root folder, indexers or a login keeps them, and an indexer you delete stays deleted.

### Coming from music-fndr

Coming from music-fndr? Stop its container first: Parlor uses the same port (8800). Point `DATA` at the folder that holds your music as `music/`; Parlor picks up music-fndr's queue and synced playlists from `music/.music-findr/`.

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

## Updating

To update everything else:

```sh
docker compose pull && docker compose build --pull && docker compose up -d
```

yt-dlp updates itself: see [Watching YouTube](youtube.md#keeping-up-with-youtube).
