"""The README and the docs pages as a reader sees them on GitHub: each goes through GitHub's own Markdown renderer
(`gh api markdown`, so it needs gh signed in and the network), is laid out with GitHub's stylesheet, and is shot light
and dark at a computer's and a phone's width. Fails on a picture that doesn't load, the wrong theme's picture, or
anything wider than the page.   python tests/docs_render.py [OUT_DIR] [pages…]   (default: every page, a temp folder)"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
CSS = "https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.8.1/github-markdown.min.css"
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="parlor-docs-"))
out.mkdir(parents=True, exist_ok=True)
pages = [Path(p).resolve() for p in sys.argv[2:]] or [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
css = requests.get(CSS, timeout=30)
css.raise_for_status()
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
bad = 0
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    for file in pages:
        r = subprocess.run(["gh", "api", "markdown", "--input", "-"], input=json.dumps({"text": file.read_text(), "mode": "gfm"}), capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"{file.name}: GitHub didn't render it: {r.stderr[:300]}")
        stem = file.stem.lower()
        (out / f"{stem}.html").write_text(
            f'<!doctype html><meta charset="utf-8"><base href="{file.parent.as_uri()}/"><style>{css.text}\n'
            "body{margin:0;background:var(--bgColor-default)} .markdown-body{box-sizing:border-box;max-width:1012px;margin:0 auto;padding:32px}"
            f'@media (max-width:767px){{.markdown-body{{padding:16px}}}}</style><article class="markdown-body">{r.stdout}</article>')
        for scheme in ("light", "dark"):
            for width, kind in ((1280, "desktop"), (390, "phone")):
                page = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme).new_page()
                page.goto((out / f"{stem}.html").as_uri())
                page.wait_for_load_state("networkidle")
                broken = page.evaluate("[...document.images].filter((i) => !i.complete || !i.naturalWidth).map((i) => i.currentSrc || i.src)")
                wide = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                shown = page.evaluate("[...document.querySelectorAll('picture img')].map((i) => (i.currentSrc || '').split('/').pop())")
                wrong = [s for s in shown if ("dark" in s or "light" in s) and ("dark" in s) != (scheme == "dark")]
                page.screenshot(path=str(out / f"{stem}-{kind}-{scheme}.png"), full_page=True)
                if broken or wide > 1 or wrong:
                    bad += 1
                    print(f"✗ {file.name} {kind} {scheme}: pictures not loaded {broken}, {wide}px wider than the page, the other theme's picture {wrong}")
                page.context.close()
        print(f"✓ {file.name}: {len(re.findall(r'<h[1-6]', r.stdout))} headings, {r.stdout.count('<table')} tables, {r.stdout.count('<li')} list items")
    browser.close()
print("RESULT:", "ok" if not bad else f"{bad} problems", f"(pictures in {out})")
sys.exit(1 if bad else 0)
