# Design

## Direction

music-fndr's look, grown into one app with three sections. The base is warm and
dark: an aubergine background (`#1c1a24`), raised panels, and Bricolage
Grotesque with tight, heavy display type. Every section keeps the same layout
language and has its own accent:

| Section | Accent | Used for |
|---|---|---|
| Home | amber `#ffb547` | the mix of all three |
| Watch (YouTube) | coral `#ff6b6b` | video cards, the player glow, progress |
| Films & shows (Seerr) | violet `#a58bff` | posters, requests |
| Music (music-fndr) | amber `#ffb547` | unchanged from music-fndr |
| Settings | mint `#6be0c4` | forms |

The accent is one CSS variable (`--accent`), switched by `body[data-section]`.

The app always shows a little movement, which carries information where it can:

- the logo record spins while anything downloads, and the Downloads button shows equalizer bars
- download meters have a moving glint, and progress the server can't measure gets an indeterminate bar
- a slow aurora drifts behind page heads; film backdrops slowly pan (Ken Burns)
- cards rise in, staggered; loading shows shimmer skeletons shaped like the content
- the player's seek map draws the YouTube "most replayed" heat curve, chapters and SponsorBlock segments under a live playhead

All of it stops under `prefers-reduced-motion`. Light mode follows the system.

## Navigation

One list (`NAV` in `app/static/js/nav.js`) draws all of it, so a new section or page is one entry:

- **Wide screens (900 px and up):** a sidebar with the six sections (Home, Library, Watch, Music, Request, You) and, under the open one only, its pages; the section icon in its own colour, a glowing dot on the current page. A **Back** button appears in the top bar on pages inside a section (a film, an album, a video).
- **Phones and tablets:** a tab bar with all six sections, and one row of the section's pages in one fixed place: right under the top bar (`#pages`; a page asks for it by putting `subnav()` in what it paints). A page never adds a second row of links.
- **You** holds your own lists (watch later, history, downloaded, channels) and Settings, and lists every section's pages, so anything is two taps away.
- **Pages off the menu:** a page reached from a menu page (Request's four kinds, Collections, Recommended, Playlists, Continue watching) is listed in `UNDER` beside `NAV`: it lights its menu page and gets **Back**.
- **Choices on a page** (a list's order, a filter) are small menus (`pick()`), never another row of chips.
