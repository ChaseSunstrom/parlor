"""Draw the cost chart (dark and light SVG) from the measured numbers: python tests/readme_cost_chart.py docs/assets/charts
Measured on one machine: `docker image inspect <image> --format '{{.Size}}'` for the image sizes; idle memory is
`docker stats --no-stream` for the apps already running, and for Parlor the resident set of a fresh process after
30 s idle (tests/readme_idle_rss.py APP_DIR PYTHON), since Parlor's own container wasn't running here."""
import sys
from html import escape
from pathlib import Path

IMAGES = {"Seerr": 1342803151, "Jellyfin": 1268426988, "Parlor": 1257998209, "Radarr": 309241979, "Sonarr": 298859087, "Prowlarr": 273741584}
IDLE = {"Parlor": 218.6, "Prowlarr": 207.3, "Sonarr": 195.3, "Radarr": 193.7, "Seerr": 181.1, "Jellyfin": 180.7}
TITLE = "Parlor costs about one more app: a 1.26 GB image and 219 MiB idle"
TOKENS = {
    "dark": dict(bg="#0a0e16", text="#d6deeb", line="#1c2433", accent="#FFB547", muted="#5d6b82", sub="#9aa7bd"),
    "light": dict(bg="#F5F4F8", text="#1C1A24", line="#CECDD2", accent="#A47739", muted="#8a8794", sub="#5e5b68"),
}
W, LABEL, BAR, ROW = 760, 120, 470, 26


def panel(y, head, rows, unit, fmt, t):
    top = max(v for _, v in rows)
    out = [f'<text x="28" y="{y}" font-size="13" font-weight="700" fill="{t["text"]}">{escape(head)}</text>',
           f'<line x1="{LABEL}" y1="{y + 10}" x2="{LABEL}" y2="{y + 14 + ROW * len(rows)}" stroke="{t["line"]}"/>']
    for i, (name, v) in enumerate(rows):
        yy = y + 14 + ROW * i
        w = max(2, v / top * BAR)
        us = name == "Parlor"
        fill = t["accent"] if us else t["muted"]
        out.append(f'<text x="{LABEL - 10}" y="{yy + 17}" font-size="12" text-anchor="end" fill="{t["text" if us else "sub"]}"'
                   f'{" font-weight=\"700\"" if us else ""}>{name}</text>')
        out.append(f'<rect class="b" x="{LABEL}" y="{yy + 4}" width="{w:.1f}" height="{ROW - 10}" rx="2" fill="{fill}" style="animation-delay:{i * 60}ms"/>')
        out.append(f'<text x="{LABEL + w + 8:.1f}" y="{yy + 17}" font-size="12" fill="{t["text"]}" style="font-variant-numeric:tabular-nums">{fmt(v)} {unit}</text>')
    return out


def svg(mode):
    t = TOKENS[mode]
    imgs = sorted(((n, b / 1e6) for n, b in IMAGES.items()), key=lambda r: -r[1])
    mem = sorted(IDLE.items(), key=lambda r: -r[1])
    h = 46 + 2 * (40 + 14 + ROW * 6) + 58
    desc = ("Image size in MB: " + ", ".join(f"{n} {v:,.0f}" for n, v in imgs) + ". Idle memory in MiB: "
            + ", ".join(f"{n} {v:.0f}" for n, v in mem) + ".")
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" aria-labelledby="t d" '
         f"font-family=\"ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace\">",
         f'<title id="t">{escape(TITLE)}</title><desc id="d">{escape(desc)}</desc>',
         '<style>.b{transform-box:fill-box;transform-origin:left;animation:g .5s cubic-bezier(.2,.7,.2,1) both}'
         '@keyframes g{from{transform:scaleX(0)}}@media (prefers-reduced-motion:reduce){.b{animation:none}}</style>',
         f'<rect width="{W}" height="{h}" rx="10" fill="{t["bg"]}"/>',
         f'<text x="28" y="38" font-size="16" font-weight="700" fill="{t["text"]}">{escape(TITLE)}</text>']
    o += panel(78, "Image on disk, in MB (smaller is lighter)", imgs, "MB", lambda v: f"{v:,.0f}", t)
    y2 = 78 + 14 + ROW * 6 + 40
    o += panel(y2, "Memory when idle, in MiB", mem, "MiB", lambda v: f"{v:.0f}", t)
    base = y2 + 14 + ROW * 6 + 24
    o.append(f'<text x="28" y="{base}" font-size="11" fill="{t["sub"]}">One machine, nothing playing. Images: docker image inspect. Memory: docker stats for the apps</text>')
    o.append(f'<text x="28" y="{base + 16}" font-size="11" fill="{t["sub"]}">that were running; for Parlor, a fresh process after 30 s idle (its resident set).</text>')
    o.append("</svg>")
    return "\n".join(o) + "\n"


out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for mode in TOKENS:
    (out / f"cost-{mode}.svg").write_text(svg(mode))
print("wrote", *(out / f"cost-{m}.svg" for m in TOKENS))
