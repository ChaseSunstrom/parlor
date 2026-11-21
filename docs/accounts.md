# Accounts and who can use what

[← Parlor's README](../README.md)

This page covers signing in, adding people, linking Seerr users, what each member can use, and private items.

## Signing in

The first account is the admin, created on first visit. Nobody can sign in, not even with Jellyfin, until it exists. After that, accounts come from:

- **Settings → People → Add someone** (name and password), or
- **Jellyfin**: set the Jellyfin address in Settings (or `JELLYFIN_URL`). Anyone can then sign in with their Jellyfin name and password, and gets an account here the first time.
  - Jellyfin admins are admins here.
  - A Jellyfin account never takes over a local account with the same name.
- **Open sign-up**: a switch in Settings; off by default.

Each account has its own subscriptions, history, resume points, watch later and YouTube cookies. Downloads and the music library are shared.

## The household and profiles on a TV

A TV acts as one person, so each person's history, resume points, limits and Jellyfin library follow them onto the TV, and nobody types a password on it.

- **The household**: an admin marks accounts as part of it (Settings → People, `household` on `PATCH /api/users/{id}`). A profile is a household account; there are no sub-profiles inside one account.
- **Who a TV acts as**: its owner (the account that paired it) until someone else in the household is picked. Then every request from the TV is answered as that person: their sections, their libraries, their history. Their own playing preferences on that TV follow too (subtitles and captions, speed, autoplay, skipping sponsors, hiding Shorts, the spoiler shield); the TV's own settings, like its quality, stay with the TV. It is never an admin, even when that person is.
- **Falling back**: the TV acts as its owner again when the person is deleted or taken out of the household, signs out everywhere, or changes their password or PIN. Picking them again asks for the PIN again.
- **Who may pick and drive**: the TV's owner, the person it is acting as, and any household account, from a phone or the TV's own screen (`GET /api/tv/{id}/people`, `POST /api/tv/{id}/viewer`). The household shares the TVs its own accounts paired: a TV paired by an account outside the household stays that account's alone. Only the owner (or an admin from Settings → TVs) unpairs a TV.
- **What a TV never does**: whoever it acts as, a TV's session can't change a password or a PIN, make or list API keys and app passwords, see or sign out that person's other devices, set their YouTube cookies or pair another screen. Those are done from the person's own phone or computer.
- **PINs**: anyone can set a 4 to 8 digit PIN with their password (`PUT /api/me/pin`, `null` clears it; an admin can clear a forgotten one). Switching a TV to a person with a PIN asks for it, from a TV or someone else's phone, including going back to an owner who has one. Their own phone, and a TV already acting as them, aren't asked. Wrong PINs are throttled like wrong passwords: 8 in 15 minutes per person and per address, after which even the right PIN waits. A PIN is stored hashed.
- **Where it is in Parlor**:
  - *Settings → People* (an admin): **Into the household** / **Out of the household** on each person, **Clear PIN** for a forgotten one, and the age limit under **Access**.
  - *Settings* (a household member, on their own phone or computer): **TV PIN**, set or removed with the account's password.
  - *On the TV*: **Who's watching** when it starts and from the round picture in the menu. Settings on a TV shows none of the account panels (password, PIN, keys, devices, cookies).
  - *On the remote*: the round picture beside the TV's name.

## Seerr users

Requests go to Seerr as the person's own Seerr user, so Seerr's quotas, permissions and approvals apply.

- **Jellyfin sign-in**: people who sign in with Jellyfin are linked to the Seerr user with the same Jellyfin account by themselves.
- **Everyone else**: an admin links them in Settings → People, which points out a Seerr user with the same name. Names alone aren't trusted: with open sign-up anyone could pick one.
- **Not linked**: members who aren't linked can browse but can't request. An admin who isn't linked requests as the API key's owner.

## Who can use what

Settings → People → **Access** sets, for each member, what they can use:

- **Sections**: **Watch** (YouTube), **Films & shows** (discovering and requesting), **Music** (finding and requesting music, and music apps over Subsonic), **Library** (playing what's in Jellyfin, and asking for books), **Games** (playing them, and asking for new ones) and **Downloads** (saving videos and songs to the server).
- **When a section is off**: it leaves their menus, and the server refuses it too, from the page, the API or a music app.
- **Which Jellyfin libraries they see.** Jellyfin holds this itself. A member limited to some libraries plays as a Jellyfin user of their own: their Jellyfin sign-in, or one Parlor makes for them, hidden and with a password nobody needs. Parlor keeps its library list in step.
  - Search, Next up, pictures and streams follow it, in Parlor and in Jellyfin's own apps.
  - Someone sharing the default Jellyfin user is given their own first, so the others keep theirs.
  - If Jellyfin can't be reached while you save, they see no library until it can, never every library.
- **An age limit.** An admin can set the highest rating a member may watch, from Jellyfin's own ratings (its *Maximum parental rating*: G, PG, PG-13 and so on; `GET /api/jf/ratings` lists them). Parlor sets it on the same Jellyfin user of their own, and blocks titles with no rating (music is left alone), so Jellyfin hides what is too old in lists, search and streams, in Parlor and in Jellyfin's own apps.
  - Clearing it removes it. Making someone an admin clears it too.
  - Give a parent's account a PIN: without one, anyone at the TV can switch it to that parent and watch as them.
  - YouTube has no ratings Parlor can enforce: close Watch for that account if it should not be there.
  - If Jellyfin can't be reached while you save, the limit stays as it was and the save fails: it is never half set.

## Private items

Anyone can make a video they downloaded private (the lock button on Downloaded).

- **What happens**: it leaves the shared video folder and is encrypted with a key of its own, which is sealed to the people it's for.
- **Turning it on**: each person does this once in Settings → Private items, which makes them a key pair. The private half is sealed by their password, and by a **recovery code shown once**.
- **Opening**: Parlor opens it only in memory, for a signed-in session, so after a restart the page asks for the password again.
- **Sharing**: the owner shares it by name with people who have it turned on. Taking someone off re-encrypts the file with a new key, so a key they kept opens nothing.
- **Who sees it**: nobody else, its title included. An admin can't open it without being given it.
- **Passwords**: changing your password in Parlor keeps it. An admin resetting your password (or a Jellyfin account's password changing in Jellyfin) can't open it, and your recovery code then seals it to the new password (without the code it's lost).
- **Playing**: private videos play in Parlor, with seeking, and stay out of Jellyfin's and the music apps' reach.
- **Leftovers**: the thumbnail and subtitle files are left behind (deleted). Deleting an account deletes its private files.
- **Undo**: **Back to the shared library** decrypts one into the video folder again.
- **Disks**: a deleted file can linger on an SSD until it's overwritten, so for the strongest protection encrypt the disk as well (see [Encrypted at rest](security.md#encrypted-at-rest)).

## Admins and older accounts

- Admins always have everything, and accounts from before this have everything until you change it.
- The apps' own pages (Sonarr, qBittorrent and the rest) are admin tools outside these settings; keep their ports to your home network.
