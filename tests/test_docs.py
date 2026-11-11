"""The README and the docs/ pages it links to: every link between them lands on a page and a heading that exist, every
picture is there in both themes, and the README stays short enough to scan."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to dashes."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "").replace("*", "")
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def prose(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)  # a command isn't a link


def anchors(path: Path) -> set:
    return {slug(h) for h in re.findall(r"^#{1,6} (.+)$", prose(path.read_text()), re.M)}


class Docs(unittest.TestCase):
    def test_every_link_lands_somewhere(self):
        broken = []
        for page in PAGES:
            for target in re.findall(r"\]\(([^)\s]+)\)", prose(page.read_text())):
                if re.match(r"[a-z]+:", target):
                    continue  # another site
                file, _, anchor = target.partition("#")
                dest = (page.parent / file).resolve() if file else page
                if not dest.exists() or (anchor and dest.suffix == ".md" and anchor not in anchors(dest)):
                    broken.append(f"{page.relative_to(ROOT)}: {target}")
        self.assertEqual(broken, [])

    def test_every_picture_is_there_dark_and_light_with_words_for_it(self):
        missing = []
        for page in PAGES:
            text = page.read_text()
            for src in re.findall(r'(?:srcset|src)="([^"]+)"', text):
                if not (page.parent / src).is_file():
                    missing.append(f"{page.relative_to(ROOT)}: {src}")
            for picture in re.findall(r"<picture>.*?</picture>", text, re.S):
                if "prefers-color-scheme: dark" not in picture or not re.search(r'alt="[^"]{20,}"', picture):
                    missing.append(f"{page.relative_to(ROOT)}: a picture without its dark version or its description")
        self.assertEqual(missing, [])

    def test_every_page_is_reached_from_the_readme(self):
        linked = set(re.findall(r"\]\(docs/([\w-]+\.md)", (ROOT / "README.md").read_text()))
        self.assertEqual(sorted(p.name for p in PAGES[1:] if p.name not in linked), [])

    def test_the_readme_stays_scannable(self):
        text = (ROOT / "README.md").read_text()
        self.assertLess(len(text.splitlines()), 220, "the detail belongs in docs/")
        long = [line[:60] for line in prose(text).splitlines() if len(line) > 420 and not line.lstrip().startswith(("<", "|"))]
        self.assertEqual(long, [], "a paragraph this long is a list or a docs page")
        self.assertRegex(text, r"(?m)^## Contents$")
        for page in PAGES[1:]:
            walls = [line[:60] for line in prose(page.read_text()).splitlines() if len(line) > 700 and not line.lstrip().startswith(("<", "|"))]
            self.assertEqual(walls, [], f"{page.name}: a wall of text")


if __name__ == "__main__":
    unittest.main()
