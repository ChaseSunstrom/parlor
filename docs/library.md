# The Library: playing Jellyfin in Parlor

[← Parlor's README](../README.md)

**Library**: play what's in Jellyfin right here: films and shows with resume, next up and watched marks kept in your Jellyfin account, music in a player that keeps going while you look around, audiobooks, and books in a reader.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screens/film-desktop-dark.png">
  <img alt="A film's page in the Library: its poster, year and length, Play, Save on this device and Mark watched buttons, and the cast below." src="assets/screens/film-desktop-light.png">
</picture>

Library plays what Jellyfin has, inside Parlor, with Parlor's login. Each person plays as their Jellyfin user (their Jellyfin sign-in, the user an admin linked, or the default in Settings), so resume points, watched marks, favourites and Next up are the same here as in Jellyfin's own apps.

## Films and shows

- **Browsing**: Continue watching, Next up and what's new in each library; every film, show, album and book; a show's seasons and episodes.
- **Sorting**: A–Z, recently added, year, rating, shuffle.
- **Filters**: new to you, in progress, favourites.
- **Play**: **Play** resumes where you stopped; **From the start** doesn't.
- **Your place**: it's saved every few seconds and when you pause. Near the end it's marked watched and the next episode starts after a countdown.
- **Movies & TV** shows **Play** (or **Watch**, for a show) on a title Jellyfin has, straight into the Library.

## Playback

- **Direct play**: Jellyfin sends the file as it is when the browser can play it (H.264 or HEVC in MP4, VP9 or AV1 in WebM), and converts it otherwise.
- **Quality**: the quality menu asks for a lower bitrate when you're away from home.
- **Menus**: audio tracks, subtitles and chapters have menus.
- **Keys**: Space or K, J and L or ← and → for 10 s, F for full screen, M to mute, Shift+N for the next episode.

## Music and audiobooks

- **The bar**: they play in the bar along the bottom, which keeps playing as you move around Parlor and answers the lock screen's and keyboard's media keys.
- **Albums**: they play in order or shuffled.
- **Audiobooks**: an audiobook carries on from where you stopped, on any device.
- **Your own folder**: songs in your own music folder (Music → Your library → an album) play there too.

## Books

- **EPUB**: books open in a reader (pages, contents, text size; it remembers your page on that device).
- **PDF**: PDFs open in the browser's own viewer.
- **Needs**: reading needs your Jellyfin user to be allowed to download.

## The Jellyfin key

- **The Jellyfin key stays on the server.** Streams, pictures and books go through Parlor, which adds the key itself and checks each one against what your Jellyfin user may see.
