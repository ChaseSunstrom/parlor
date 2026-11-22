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

## Cards, buttons and forms (the TV's look everywhere)

The phone and computer site use the TV's pieces, in one block at the end of `app.css` (T-0203):

- **Every card is a TV tile:** posters, video cards, square music tiles and games are rounded art that lifts (`translateY(-4px) scale(1.03)`) under the pointer or the keyboard, with a 2 px ring and a glow in the section's colour; the caption turns that colour; a press sinks it (`scale(.98)`). Artists are round. A row that scrolls sideways leaves room (12 px above, 28 px below) so the lift and glow aren't cut off.
- **Buttons are solid or tonal:** a ghost button is a soft fill of the text colour (8 %, 14 % on hover), never an outline; on, it takes the accent's tint.
- **Tick boxes are switches** (42 × 24, the knob slides); **panels** are soft cards (18 px corners, a faint top light, a long soft shadow); a focused field gets a 4 px accent halo.
- **A page rises in** (10 px and a fade, 0.34 s) as it first paints. `backwards`, not `both`: a held `translate` would make the page the containing block of the player and dialogs.
- **On a phone the tab bar floats** 10 px off the edges with 22 px corners, and the player, TV and update bars float above it; the queue opens as a sheet 8 px over the player, as wide as it.
- **Light mode:** the section colours are bright for a dark ground, so text and controls in them use `--accent-ink` (55 % accent, 45 % text), and an off switch is 42 % text: 3:1 and up on white.
- **A picture that doesn't load** is hidden (`img.broke`), leaving the card's own ground, and shows again if its address changes and loads.
- Only `transform`, `opacity`, `box-shadow` and colours move, and under reduced motion nothing does. A TV keeps its own focus look (the lifts are scoped to `body:not(.tv)`) and its pages don't rise in; neither does a page redrawn in place, nor a book's reader.

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
- **Idle:** after 5 minutes, the household's own pictures (the backdrops of this TV's last Home), shuffled: each decoded before it fades in over the last (2.4 s), panning and zooming slowly (15 s), dimmed to 60 % under a vignette; the title, a clock and the date small in a corner that changes with each picture (every 12 s), so nothing stands still. With no pictures, black with a dim clock that moves each minute. The first key only wakes it, where it was. Under reduced motion the pictures still change, without the pan.
- **Burn-in (OLED):** nothing stands still for long. Every 3 minutes the fixed parts (the rail, the clock, the hero, row titles, the player's strips, the pair and who's-watching cards) drift a few pixels together, over 4 s, so nobody sees it. After 2 minutes on one screen with no key, a black veil dims it to 45 % over 3 s; a key lifts it at once and still does its job (the screen stayed readable). A playing film is never dimmed. The player's strip shows for 4 s, or 10 s when paused, then goes; a film left paused falls to the pictures after 5 minutes like any idle screen. At rest the drift is `translate: none`, since any translate holds fixed children to it.
- **Updates:** a TV takes a newer Parlor by itself (no bar to press), once no song plays and no film is open.
