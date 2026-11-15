# Security

[← Parlor's README](../README.md)

- **Sessions**: every API call needs a session. Sessions last 30 days, renew while used, and are stored hashed. Passwords are hashed with scrypt.
- **One link needs no session**: a film or a Watch video sent to a TV set's own player ([On a TV](tv.md#a-film-in-the-tvs-own-player)) is fetched by the TV at `/cast/<a long random name>/…`. The name is made per film, lives for at most 12 hours (in Parlor's encrypted database, so a film that is playing outlasts an update of Parlor), ends with **Stop** or when the remote sees the film end, and is kept out of pages and the access log. Anyone on your network who learned it could fetch that one film until then.
- Every change needs an `X-Requested-With: parlor` header (formerly yeerr, still accepted), which other websites can't send, so a page you visit can't act for you.
- Wrong passwords are throttled per name and per address. Behind a reverse proxy, set `FORWARDED_ALLOW_IPS` to the proxy's address so the address is each visitor's, not the proxy's (see [Settings](settings.md)).
- Changing a password signs out your other devices; Settings also has **Sign out everywhere**.
- Put Parlor behind HTTPS (a reverse proxy) if it's reachable from outside your home.
  The session cookie is marked secure when the proxy says the request came over HTTPS.
- **The other apps' logins**:
  - The *arrs skip their logins for any private address, qBittorrent only for `LAN_SUBNET` and the other containers.
  - Anyone on your home network can use them, so keep untrusted devices on a guest network, and don't forward their ports to the internet.
  - From outside, they ask for a name and password.
  - Your password is used for setup only while it runs, and never stored.

## Encrypted at rest

Everything Parlor itself writes is encrypted at rest, all with one random 256-bit key.

- **Database**: accounts, sessions, settings and the apps' keys, history, subscriptions, with SQLCipher.
- **Files beside it**: YouTube cookies and the download list, with AES-GCM.
- **Data folder**: Parlor's alone (folders 0700, files 0600).
- **Steam sign-ins**: a person who signs in to Steam by QR code ([Games](games.md)) leaves that sign-in in the database, so it is encrypted with everything else; no browser is sent it.
- **Seerr key**: the stack's own Seerr key isn't copied at all: Parlor reads it from Seerr's settings when it needs it.

### Details

- **The key** is made on the first start in `./parlor/key/db.key`, apart from `./parlor/config`, so a copy or backup of the config folder alone can't be read. Outside the compose, set `KEY_DIR` to a folder of its own (otherwise it goes inside `DATA_DIR`, and Settings warns you).
- **Back up the key.** Settings → Security → **Download the key** (it asks for your password; do it at home or over HTTPS, since the key crosses the network). Lose it and the data is lost: nobody can open it, Parlor included. To restore, put the file back as `./parlor/key/db.key`.
- **Upgrading** from a Parlor without encryption converts the database once at start: an encrypted copy is written and checked (every row, and an integrity check) before it replaces the old file, and the plain copy is kept as `yeerr.db.plain-backup` until the next start, then removed.
- **Lock at boot** (Settings → Security) seals the key on disk with a passphrase of yours.
  - After every restart Parlor stays locked, downloads and music apps included, until someone opens it in a browser and enters the passphrase (five wrong ones a minute, then it waits).
  - There's no recovery: forget the passphrase and only a backed-up key opens the data.
  - Turning it off puts the plain key back.
- `ENCRYPT=0` keeps a new install plain; a database that's already encrypted stays encrypted and keeps opening with its key.
- **What it protects against:** a stolen disk, a copied backup or another user on the server reading Parlor's data. It doesn't protect against someone who controls the running server: an admin who changes Parlor's code can read what it opens.
- **What isn't covered:** the apps' own configs and your media, which Jellyfin, the *arrs and qBittorrent read and write directly. Encrypt the disk they're on instead:
  - LUKS when you set the disk up (`cryptsetup luksFormat`, then unlock it at boot, before Docker starts);
  - or `fscrypt` on an ext4 folder (`fscrypt setup`, then `fscrypt encrypt /srv/media`).
  - Put the compose folder on it too, and the whole stack is encrypted at rest.
