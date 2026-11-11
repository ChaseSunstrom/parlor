# The API

[← Parlor's README](../README.md)

Scripts and other programs (Home Assistant, a phone shortcut, an AI agent) can use Parlor with an **API key** instead of a sign-in.

## Making and using a key

- **Make one**: in **Settings → API keys**. It's shown once and stored hashed.
- **Send it**: as `X-Api-Key` (or `Authorization: Bearer`) on any `/api` route.
- **Reference**: the full reference is at `/api/docs` once you're signed in.
- **Stop it**: deleting a key stops it at once. **Sign out everywhere** (or an admin resetting your password) ends your keys too.

## What a key can do

A key acts as you, with what your account may use.

- **Search key**: a **search** key only reads.
- **Full key**: a **full** key also downloads and requests.
- **Admin's key**: it reaches what the admin does.
- **Reads**: a key reads what its person can.
- **Changes**: it changes only what the API is for: downloads, requests and your own lists.
- **Needs a sign-in**: accounts, settings, the stack, keys, passwords, private items and the server's lock need a sign-in, an admin's key included.
- **Why**: so a leaked key can't make itself an account or repoint the server.

## Searching and getting

The stable part is `/api/v1`:

```sh
KEY=pk_your_key_here   # made up: use yours
# one search across YouTube, films and shows (Seerr), music on Spotify and in Lidarr, and your Jellyfin library
curl -H "X-Api-Key: $KEY" "http://your-server:8800/api/v1/search?q=ok+computer"
curl -H "X-Api-Key: $KEY" "http://your-server:8800/api/v1/search?q=ok+computer&in=lidarr,spotify"
# get anything a search found: each hit's "get" list holds the bodies to send
curl -H "X-Api-Key: $KEY" -H "content-type: application/json" -d '{"kind": "lidarr_album", "id": "b1392450-e666-3926-a536-22c65f834433"}' \
  http://your-server:8800/api/v1/get        # → {"job": "request:12"}
curl -H "X-Api-Key: $KEY" http://your-server:8800/api/v1/jobs/request:12
```

## Whole sets

Whole sets work with a key too (a key with the search scope can look; requesting needs the full scope):

```sh
# the Avengers films: what's there, then everything missing, counted first and confirmed with the plan's id
curl -H "X-Api-Key: $KEY" "http://your-server:8800/api/seerr/sets/collection/86311"
curl -H "X-Api-Key: $KEY" -H "content-type: application/json" -d '{"kind": "collection", "id": 86311}' http://your-server:8800/api/seerr/sets/plan
curl -H "X-Api-Key: $KEY" -H "content-type: application/json" -d '{"plan": "the-plan-id-from-above"}' http://your-server:8800/api/seerr/sets/request
curl -H "X-Api-Key: $KEY" http://your-server:8800/api/seerr/sets/jobs
```

`kind` there is `collection`, `studio`, `network` or `keyword` (TMDB ids; `/api/seerr/sets/search?q=` finds studios and keywords). A plan takes:

- `year_from`
- `year_to`
- `rating`
- `released`
- `media` (`movie` or `tv`), for a keyword

## The kinds you can get

`kind` is one of:

- `youtube_video`, `youtube_audio`
- `track`, `album`, `artist`, `playlist` (music-fndr, Spotify ids)
- `lidarr_album`, `lidarr_artist` (MusicBrainz ids)
- `movie` and `show` (TMDB ids; `seasons` picks seasons)

Sources you can't use are left out of a search, and one that's down is named in `errors` while the rest answer.
