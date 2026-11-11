# How it works

[← Parlor's README](../README.md)

Parlor is the one app you open. It keeps the sign-in and the accounts, and talks to the other apps for you, so each does one job:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/diagrams/architecture-dark.svg">
  <img alt="Your browser, phone or TV talks to Parlor, one container with the Library, Requests, Watch and Live, and Games. Parlor plays from Jellyfin, asks Seerr for requests, fetches YouTube, Twitch and Steam itself, and reaches your gaming PC through moonlight-web (optional). Seerr hands requests to Sonarr, Radarr and Lidarr, which look through Prowlarr and FlareSolverr, get subtitles from Bazarr, and download with qBittorrent, whose only way out is the Gluetun VPN; Cleanuparr clears stuck downloads, Questarr does the same finding and downloading for games, Checkrr finds files that no longer play and has their app fetch them again, and setup makes the config folders once. Downloads are saved to your media folders." src="assets/diagrams/architecture-light.svg">
</picture>

Asking for a film shows the whole path. You never leave Parlor, and the film turns up in your Library when it has downloaded:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/diagrams/request-dark.svg">
  <img alt="You tap Request. Parlor sends it to Seerr as you. Seerr approves it for Radarr, which searches through Prowlarr and hands the film to qBittorrent. It downloads through the VPN into the Movies folder, Jellyfin scans it, and you press Play." src="assets/diagrams/request-light.svg" width="340">
</picture>

What it costs to run, measured on one machine against the apps beside it:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/charts/cost-dark.svg">
  <img alt="Bar charts. Parlor's image is 1,258 MB, close to Jellyfin's 1,268 MB. Idle, each in its own container, Parlor uses 198 MiB of memory, against 170 to 212 MiB for Seerr, Sonarr, Jellyfin, Radarr and Prowlarr." src="assets/charts/cost-light.svg">
</picture>
