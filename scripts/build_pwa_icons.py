"""Render the PWA icons in static/icons/ from scripts/icon.svg."""

from pathlib import Path
import re

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "static" / "icons"


def build():
    source = (ROOT / "scripts" / "icon.svg").read_text()
    # Android crops maskable icons to a circle or squircle, so the paper colour has to reach
    # every edge. The glyph already sits inside the 80% safe zone.
    maskable = re.sub(r'<rect x="52" y="52" width="920" height="920" rx="208"',
                      '<rect width="1024" height="1024"', source, count=1)
    assert maskable != source, "icon.svg changed; update the maskable background here"
    OUTPUT.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            for name, svg, size in (("icon-192.png", source, 192), ("icon-512.png", source, 512),
                                    ("icon-maskable-512.png", maskable, 512)):
                page = browser.new_page(viewport={"width": size, "height": size})
                svg = svg.replace('width="1024" height="1024" viewBox',
                                  f'width="{size}" height="{size}" viewBox', 1)
                page.set_content('<style>body { margin: 0 }</style>' + svg)
                page.screenshot(path=str(OUTPUT / name), omit_background=True)
                page.close()
                print(OUTPUT / name)


if __name__ == "__main__":
    build()
