# Music

[← Parlor's README](../README.md)

**Music**: all of [music-fndr](https://github.com/ChaseSunstrom/music-fndr). Browse Spotify and download songs, albums, playlists and whole discographies into your music folder, with synced playlists and library tidying.

- **Audio format**: new downloads keep YouTube's source audio as it is, with no re-encoding (AAC `.m4a` on a new install; Opus, MP3 320 or MP3 128 in Settings → Music downloads, for an admin).
- **Changing it**: songs you already have count as there in any format, so changing it fetches nothing again and converts nothing.
- **From YouTube**: a video saved as music from YouTube follows the same choice.

## Request music

See [What the apps are doing](stack.md#what-the-apps-are-doing): **Request → Music** sends albums and artists to Lidarr.

## Music apps (Subsonic)

Parlor speaks the Subsonic/OpenSubsonic API, so Symfonium, Tempo, DSub, Feishin and similar apps can play your music folder.

- **Server URL:** Parlor's address (`http://your-server:8800`); the API is under `/rest`. Apps that ask for a path use `/rest`.
- **Login:** your Parlor name and password work. For apps that send a salted token instead (most do), or a long-lived key, make an **app password** in **Settings → Music apps**: it's shown once. Use it as the app's password, or as its API key. Delete it there to cut that app off.
- Stars, ratings, plays, playlists, play queue and bookmarks are per person. Playlists from music-fndr's `Playlists/` folder show up read-only.
- Tracks are found by a hash of their path in the music folder. **Moving or renaming a file makes it a new track**: its stars, ratings and play counts don't follow it (they're deleted at the next scan, every 10 minutes, once the old path is gone).
- A scan that can't open a folder, or finds no music at all (an unmounted share), removes nothing; it logs a warning instead. Files whose names aren't valid UTF-8 are skipped.
