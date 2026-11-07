#!/usr/bin/env python3
"""Write renders/video.zh-TW.srt from SCRIPT.md, timed to the assembled index.html.

Each frame's line is split at sentence punctuation; cue times are spread over the clip's
spoken span (lead silence → lead + raw narration length) in proportion to character count.
Spoken-only spellings that exist for the TTS are mapped back to how the screen writes them.
"""
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DISPLAY = {"H T T P S": "HTTPS", "P W A": "PWA", "十六千赫": " 16kHz ", "五百七十秒": " 570 秒",
           "每六十秒": "每 60 秒", "每十段": "每 10 段", "一百二十秒": " 120 秒",
           "兩秒、四秒、八秒": "2、4、8 秒", "一分鐘、五分鐘、十五分鐘": " 1、5、15 分鐘"}


def ts(t):
    h, rem = divmod(t, 3600); m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}".replace(".", ",")


index = (ROOT / "index.html").read_text(encoding="utf-8")
starts = {int(m.group(1)): float(m.group(2)) for m in
          re.finditer(r'src="assets/voice/padded-(\d+)\.wav"\s+data-start="([\d.]+)"', index)}
voices = {v["frame"]: v for v in json.loads((ROOT / "audio_meta.json").read_text())["voices"]}
raw = json.loads((ROOT / "voice_manifest.json").read_text())
script = (ROOT / "SCRIPT.md").read_text(encoding="utf-8")

cues = []
for n, body in re.findall(r"## Line (\d+) .*?\n(.*?)(?=\n## Line|\Z)", script, re.S):
    n = int(n)
    text = " ".join(l.strip() for l in body.splitlines() if l.startswith("    "))
    for k, v in DISPLAY.items():
        text = text.replace(k, v)
    text = re.sub(r"\s+", " ", text).replace(" ，", "，").replace(" 。", "。").strip()
    parts = [p.strip() for p in re.findall(r"[^。？；]+[。？；]?", text) if p.strip()]
    t0 = starts[n] + voices[n]["lead_s"]
    span = raw[str(n)]["duration_s"]
    total_chars = sum(len(p) for p in parts)
    for p in parts:
        dur = span * len(p) / total_chars
        cues.append((t0, t0 + dur, p.rstrip("。；")))
        t0 += dur

out = ROOT / "renders/video.zh-TW.srt"
out.parent.mkdir(exist_ok=True)
out.write_text("\n".join(f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1)), encoding="utf-8")
print(f"{len(cues)} cues → {out.relative_to(ROOT)}")
