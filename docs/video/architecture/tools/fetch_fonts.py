#!/usr/bin/env python3
"""Fetch the video's fonts from Google Fonts into assets/fonts/ as local woff2 files.

The render machine is a clean headless Chrome, so every family must ship as a file.
Latin faces: the full `latin` slice. CJK faces (Noto Serif TC / Noto Sans TC): subset with the
`text=` parameter to exactly the characters used across STORYBOARD.md and compositions/,
so re-run this after any copy change or a new glyph renders as a fallback font.
"""
import re, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets/fonts"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
LATIN = {"Newsreader-400": "Newsreader:wght@400", "HankenGrotesk-400": "Hanken+Grotesk:wght@400",
         "HankenGrotesk-600": "Hanken+Grotesk:wght@600", "DMMono-400": "DM+Mono:wght@400"}
CJK = {"NotoSerifTC-600": "Noto+Serif+TC:wght@600", "NotoSansTC-500": "Noto+Sans+TC:wght@500",
       "NotoSansTC-700": "Noto+Sans+TC:wght@700"}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()


def used_chars():
    texts = [(ROOT / "STORYBOARD.md").read_text(encoding="utf-8")]
    texts += [p.read_text(encoding="utf-8") for p in (ROOT / "compositions").rglob("*.html")]
    chars = {c for t in texts for c in t if ord(c) > 0x2000}
    return "".join(sorted(chars | set("０１２３４５６７８９：，。、；？！「」（）／→←↔⇄·—…")))


OUT.mkdir(parents=True, exist_ok=True)
for name, fam in LATIN.items():
    css = get(f"https://fonts.googleapis.com/css2?family={fam}&display=swap").decode()
    block = [b for b in css.split("/*") if b.strip().startswith("latin */")][0]
    (OUT / f"{name}.woff2").write_bytes(get(re.search(r"url\((.+?)\)", block).group(1)))
text = used_chars()
for name, fam in CJK.items():
    css = get(f"https://fonts.googleapis.com/css2?family={fam}&text={urllib.parse.quote(text)}").decode()
    (OUT / f"{name}.woff2").write_bytes(get(re.search(r"url\((.+?)\)", css).group(1)))
print(f"{len(text)} CJK/punct glyphs; files:")
for p in sorted(OUT.glob("*.woff2")):
    print(f"  {p.name} {p.stat().st_size // 1024} KB")
