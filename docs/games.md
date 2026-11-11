# Games

[← Parlor's README](../README.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screens/games-desktop-dark.png">
  <img alt="Games: a Steam library with recently and most played, then retro games to play in the browser and a gaming PC to stream." src="assets/screens/games-desktop-light.png">
</picture>

**Games** has three parts: your Steam library, retro games played right in Parlor, and your gaming PC streamed to any screen. Each person sees their own. A fourth section covers asking for new games with Questarr.

## Steam

An admin adds a Steam Web API key once, on the Games page (get one at steamcommunity.com/dev/apikey). It stays on the server: no browser ever sees it.

Each person then links their profile with any of these:

- its address (`steamcommunity.com/id/yourname` or `/profiles/7656…`);
- its custom name;
- its 17-digit id.

They see what they own, most played first, with what they played in the last two weeks above.

- **How often**: Steam is asked at most once an hour per person.
- **Pictures**: they come through Parlor and are kept.
- **Private profile**: it shows nothing; Games says which Steam privacy setting to change (Game details: Public).

## Retro games

Put games in `GAMES/roms/<system>/`, with a picture of the same name beside each if you like (`Game.png`). The systems are `nes`, `snes`, `n64`, `gb`, `gbc`, `gba`, `nds`, `vb`, `genesis`, `sms`, `gamegear`, `32x`, `segacd`, `psx`, `pce`, `atari2600`, `atari7800`, `lynx`, `ngp`, `ws`, `coleco`, `arcade`, `c64`, `dos` and `psp`.

**Play** opens the game on its own page with [EmulatorJS](https://emulatorjs.org) 4.2.3.

- **Emulator files**: Parlor fetches each emulator file from EmulatorJS's server the first time it's needed.
- **Checked**: it keeps a file only if it matches the sha256 pinned in `app/emulatorjs.json`, so after that it plays without the internet.
- **Saves**: the game's own save (its battery memory) goes to Parlor whenever it changes.
- **Save states**: they go to Parlor when you save one from the emulator's menu.
- **Private**: both are sealed like the rest of Parlor's data and each person's own; on another device they come back.
- **Where it reads**: Parlor only reads from inside `GAMES/roms` (links out of it are ignored).
- **Whose games**: bring your own games, and only ones you own.

## Streaming from your gaming PC

[Sunshine](https://app.lizardbyte.dev/Sunshine/) on your PC streams its screen; Parlor's bridge ([Moonlight Web](https://github.com/MrCreativ3001/moonlight-web-stream)) plays it in any browser, the TV's included. It's off until you turn it on in `.env`:

```sh
COMPOSE_PROFILES=games-stream
GAMES_STREAM=1
GAMES_STREAM_HOST=192.168.1.20   # this server's address on your network
```

Then run `docker compose up -d`.

- **The bridge's page**: it's only reachable through Parlor, at Games → From your PC (an admin opens it once first).
- **No second login**: Parlor tells it who you are, and each person keeps their own PCs.
- **Add your PC**: add it by its address, and type the PIN it shows into Sunshine's page on the PC (Sunshine → PIN).
- **Picture and sound**: they go straight from the bridge to your browser over WebRTC on UDP ports 40000–40100, so those must reach the server.
- **From outside your home**: add a TURN server in `./moonlight-web/config/config.json` (`webrtc.ice_servers`).
- **Gamepads and keyboard capture**: they need Parlor on https.

## Games you ask for (Questarr)

[Questarr](https://github.com/Doezer/Questarr) is to games what Radarr is to films: you name a game, it searches the indexers and hands the download to qBittorrent, behind the VPN like everything else.

It runs at port 5000 (`http://your-server:5000`) and always asks for a login: your Parlor name and password, which setup gives it the first time you type your password in **Settings → Services**. Setup also:

- adds qBittorrent as its download client (category `games`, saved under `DOWNLOADS/games`);
- has it read Prowlarr's indexers, and again on any later setup run after you add or remove one in Prowlarr;
- points its library at your `GAMES` folder (`/data/games`), unless you chose another in Questarr.

### It needs no keys from you

Questarr looks every game up at IGDB, which wants keys from a Twitch developer account. In this stack you register nothing: Parlor answers those lookups itself, from Steam's public store, so finding a game by its title works on a fresh install.

- What you get: titles, covers, screenshots, release dates, genres, Steam's review score, and the popular, new and upcoming lists.
- What you don't: games Steam doesn't sell (console exclusives, games only in another store) aren't found by title, though searching the indexers by hand in Questarr still works for them; and there are no editions or time-to-beat figures.
- Rather have IGDB's catalogue? Enter IGDB keys of your own in Questarr's Settings, then `docker compose restart questarr`: its lookups go to IGDB instead.

### How it's done

- Setup puts a small file and a random key into `./questarr/config`.
- The file is loaded into Questarr and sends its IGDB lookups to Parlor; the key goes with each one, and Parlor answers nobody without it.
- It is written against the Questarr version pinned in `docker-compose.yml`.
- When an update of Parlor changes the file, Questarr uses the new one from its next start (`docker compose restart questarr`).

### Login and network

- Questarr has one login, the admin's who ran setup first.
- Like the other apps' pages, port 5000 is for your home network only.
- Until setup has run, whoever reaches it first can make its login (setup then says Questarr has its own login), so don't forward it to the internet.

### Your own Questarr and the pinned image

- A Questarr of your own elsewhere is left alone (it keeps needing its own IGDB keys).
- The image is pinned to a version by its digest in `docker-compose.yml`, as the project is young: read its release notes before moving the pin.
- After moving it, run `python tests/live_stack.py --only questarr`, which searches a game by title for real.
