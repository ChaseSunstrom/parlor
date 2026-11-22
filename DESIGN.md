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

## Home

Home is one design on every screen: a hero for one title over rows of posters (`tvhome.js` holds the rows, the tile and the hero; `home.js` lays them out off a TV).

- **The hero:** the row's name and the kind in the row's colour, the title, facts, up to three lines about it (two on a phone or a TV, where the facts keep to one line and the hero keeps its tallest height so the rows never move) and one pill (Resume, Play or Open). The title's backdrop sits top right, masked into the page, over a blurred wash of the same picture.
- **Which title:** the first of the first row, then the one the pointer rests on, the keyboard focus is on, or (on a touch screen) a swiped row comes to rest on, after 240 ms.
- **Rows:** each scrolls sideways by itself and snaps; its name carries a dot in its colour and **See all** where it has a page. The tile under the pointer or the focus lifts (`scale(1.06)`) and a ring with a glow in the row's colour fades in. Tab goes from row to row, the arrows move inside them.
- **Under the rows:** the counts (channels, saved videos, requests waiting, songs).
- **Phones:** the backdrop spans the top and the hero's text sits over its faded lower half.

## On a TV

A TV (`body.tv`, set by `tv.js`) is the same app laid out for a couch: a 1280×720 canvas that `zoom` scales to the screen, always dark, no top bar. `tvhome.js` and the `body.tv` rules at the end of `app.css` hold all of it.

- **The rail:** the sections as icons down the left (76 px), Search first. Their names and a soft panel fade in while the focus is in it. A section's pages are its row of links (`#pages`) along the top of each page.
- **Home:** the same hero and rows as everywhere (see Home), as one fixed screen. A hero (the focused title: kind in the row's colour, the name at 56 px in up to two lines, the facts on one line, two lines about it, one pill) over rows of posters. It grows up from the rows, so the pill stays in one place and a long title never reaches them. The title's backdrop sits top right, masked into the page; a 64-pixel copy of it stretched across the whole screen is the colour wash behind (a blur that costs nothing to draw).
- **Focus:** on Home it is a row and a place in it, never "the nearest thing": the tile lifts (`scale(1.08)`) and a ring with a glow in the row's colour fades in. The rows slide so the focused tile is always in the same spot. Everywhere else it is the 4 px outline.
- **Sizes for ten feet:** body text 19–21 px on the 720-line canvas (about 30 px on a 1080 screen), captions never under 15 px, row names 22 px, 48 px of margin from the screen's edge.
- **Motion, for the slowest graphics chip in the house at 4K:** only `transform` and `opacity` animate, 0.2–0.3 s, ease. No `backdrop-filter`, no blur drawn live, no shadow that animates (the glow is drawn once and faded). A picture is decoded before it fades in. `tests/perf_tv.py` measures it; a frame over 34 ms fails it.
- **A film:** the whole screen, with a strip (name, time, bar) that fades in on a key or a pause and out after 4 seconds.
- **Idle:** after 5 minutes, black with a dim clock that moves each minute.
- **Updates:** a TV takes a newer Parlor by itself (no bar to press), once no song plays and no film is open.
