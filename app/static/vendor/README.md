# Vendored scripts

Scripts loaded only when needed (a transcoded video, a book) and the page's font, from here rather than a CDN, so Parlor works offline.

| File | Version | Source | Licence | sha256 |
|---|---|---|---|---|
| hls.light.min.js | hls.js 1.5.20 | https://cdn.jsdelivr.net/npm/hls.js@1.5.20/dist/hls.light.min.js | Apache-2.0 | 4a582e5e99d6a424c5f50b5e716d70dc256cd9b6c264cdba95c5ea2a4df9328a |
| epub.min.js | epub.js 0.3.93 | https://cdn.jsdelivr.net/npm/epubjs@0.3.93/dist/epub.min.js | BSD-2-Clause | 06eae15745107b4aa508c95538275251f69bfb9f1175621fc458d9f42ed082d4 |
| jszip.min.js | JSZip 3.10.1 | https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js | MIT (dual MIT/GPLv3) | acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e |
| bricolage-grotesque-latin.woff2 | @fontsource-variable/bricolage-grotesque 5.2.10 | https://cdn.jsdelivr.net/npm/@fontsource-variable/bricolage-grotesque@5.2.10/files/bricolage-grotesque-latin-opsz-normal.woff2 | OFL-1.1 | a79fdb52d4a5c76552452f69202add96e287401fff03d3e8c0e38b4dcb5a99cd |
| bricolage-grotesque-latin-ext.woff2 | @fontsource-variable/bricolage-grotesque 5.2.10 | https://cdn.jsdelivr.net/npm/@fontsource-variable/bricolage-grotesque@5.2.10/files/bricolage-grotesque-latin-ext-opsz-normal.woff2 | OFL-1.1 | 776f6dcaf03636cd69a5802c94808cc8896c0a66c8e9ce0fe147231b6ee01957 |

The font is the page's own type (app.css's `@font-face`), here rather than from Google so the page never waits on another site: a TV with no internet showed a blank page while its browser waited for Google's.

Licence texts: LICENSES.txt. To update one, download the new pinned version, check its licence, and update this table.
