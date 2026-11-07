#!/usr/bin/env python3
"""Pad each frame's narration with lead/tail silence so frames breathe between lines.

The faceless pipeline sets each frame's duration to its voice clip's length, so the padding
here is what puts air between two frames' narration. Writes assets/voice/padded-NN.wav and
audio_meta.json (frame-keyed, host BGM entry added once the bed exists).
"""
import json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEAD, TAIL = 0.35, 0.9
FIRST_LEAD, LAST_TAIL = 0.8, 2.2

manifest = json.loads((ROOT / "voice_manifest.json").read_text())
frames = sorted(int(k) for k in manifest)
voices = []
for n in frames:
    lead = FIRST_LEAD if n == frames[0] else LEAD
    tail = LAST_TAIL if n == frames[-1] else TAIL
    src, out = ROOT / manifest[str(n)]["path"], ROOT / f"assets/voice/padded-{n:02d}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af",
                    f"adelay={int(lead * 1000)},apad=pad_dur={tail}", "-ar", "44100", "-ac", "1", str(out)], check=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                "default=nk=1:nw=1", str(out)], capture_output=True, text=True).stdout)
    voices.append({"frame": n, "path": str(out.relative_to(ROOT)), "duration_s": round(dur, 2), "lead_s": lead})

meta_path = ROOT / "audio_meta.json"
meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"bgm": None, "sfx": []}
meta["voices"] = voices
meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
print("\n".join(f"frame {v['frame']:02d} {v['duration_s']}s" for v in voices))
print(f"total {sum(v['duration_s'] for v in voices):.1f}s")
