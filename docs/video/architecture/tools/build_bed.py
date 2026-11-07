#!/usr/bin/env python3
"""Build the ducked BGM bed: loop a QC-clean slice of a Music 3 seed, then duck it under the narration.

Why a slice: both Music 3 seeds failed bgm_qc's vocal-stem gate (the model adds wordless humming
on its own) and seed 23's clean opening failed the drum gate. Seed 7's 28–59 s passed all four
gates (drum r 0.122, density 5.7%, vocal stem max -27.9 dB, no recognisable speech), so the bed
is that slice looped seamlessly.

Loop: the unit's head is an equal-power crossfade of the slice's tail into its head, so tiling
the unit has no seam. The crossfade length is swept and the one with the smallest seam
discontinuity (max |Δsample| at the seam ÷ p99.99 of the whole bed) wins; < 0.3 is inaudible.
No limiter at this layer (alimiter's end-of-stream flush clicks when looped); only constant gain.

Ducking: narration placed at the index.html voice start times is the sidechain.
Run with a Python that has numpy (e.g. the series' .venv-phrasing).
"""
import json, re, subprocess, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SR = 44100
SRC, START, END = ROOT / "assets/audio/bgm-seed7.mp3", 28.0, 59.0
BED_LUFS = -28.0
OUT = ROOT / "assets/audio/bgm_bed.wav"


def decode(path, ss=None, to=None, ch=2):
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", str(ss), "-to", str(to)] if ss is not None else []) + \
          ["-i", str(path), "-f", "f32le", "-ac", str(ch), "-ar", str(SR), "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32).reshape(-1, ch).copy()


def encode(arr, path, af=None):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ac", str(arr.shape[1]), "-ar", str(SR), "-i", "-"]
    cmd += (["-af", af] if af else []) + [str(path)]
    subprocess.run(cmd, input=arr.astype(np.float32).tobytes(), check=True)


def lufs(path):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", out)[-1])


def loop_unit(seg, xf):
    n = int(xf * SR)
    t = np.linspace(0, np.pi / 2, n)[:, None]
    unit = seg[: len(seg) - n].copy()
    unit[:n] = seg[:n] * np.sin(t) + seg[len(seg) - n:] * np.cos(t)
    return unit


def seam_score(unit):
    tiled = np.concatenate([unit, unit])
    j = len(unit)
    local = np.abs(np.diff(tiled[j - 64: j + 64], axis=0)).max()
    return local / np.percentile(np.abs(tiled), 99.99)


# 1. seamless loop unit
seg = decode(SRC, START, END)
scores = {xf: seam_score(loop_unit(seg, xf)) for xf in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0)}
best = min(scores, key=scores.get)
print("seam scores:", {k: round(v, 3) for k, v in scores.items()}, "→ xf", best)
unit = loop_unit(seg, best)

# 2. narration guide track at the index.html voice positions
index = (ROOT / "index.html").read_text(encoding="utf-8")
total = float(re.findall(r'data-duration="([\d.]+)"', index)[0])  # the root composition comes first
voices = re.findall(r'<audio\s+id="[^"]*-voice"\s+src="([^"]+)"\s+data-start="([\d.]+)"', index)
guide = np.zeros((int(total * SR) + SR, 1), np.float32)
for src, start in voices:
    v = decode(ROOT / src, ch=1)
    s = int(float(start) * SR)
    guide[s: s + len(v)] += v[: len(guide) - s]
print(f"total {total}s, {len(voices)} voice clips")

# 3. tile, fades, constant gain to BED_LUFS
reps = int(np.ceil(total * SR / len(unit))) + 1
bed = np.tile(unit, (reps, 1))[: int(total * SR)]
fin, fout = int(1.5 * SR), int(3.0 * SR)
bed[:fin] *= np.linspace(0, 1, fin)[:, None]
bed[-fout:] *= np.linspace(1, 0, fout)[:, None]
tmp = OUT.with_name("bed_raw.wav")
encode(bed, tmp)
gain_db = BED_LUFS - lufs(tmp)
guide_path = OUT.with_name("narration_guide.wav")
encode(guide, guide_path)

# 4. EQ room for speech + sidechain duck under the narration
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-i", str(guide_path), "-filter_complex",
                f"[0:a]volume={gain_db:.2f}dB,highpass=f=40,equalizer=f=1400:width_type=o:width=2.2:g=-3[b];"
                "[1:a]aformat=channel_layouts=stereo,volume=4[sc];"
                "[b][sc]sidechaincompress=threshold=0.03:ratio=6:attack=40:release=600:makeup=1[out]",
                "-map", "[out]", str(OUT)], check=True)
tmp.unlink(); guide_path.unlink()
print(f"bed gain {gain_db:+.1f} dB → {OUT.relative_to(ROOT)} ({lufs(OUT):.1f} LUFS after ducking)")
(OUT.with_suffix(".json")).write_text(json.dumps({
    "source": str(SRC.relative_to(ROOT)), "slice_s": [START, END], "crossfade_s": best,
    "seam_score": round(float(scores[best]), 4), "bed_lufs_before_duck": BED_LUFS,
    "eq": "highpass 40 Hz, -3 dB @ 1.4 kHz (2.2 oct)",
    "duck": "sidechaincompress threshold 0.03 ratio 6 attack 40 release 600"}, indent=2))
