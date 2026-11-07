#!/usr/bin/env python3
"""Generate instrumental BGM candidates locally with MiniMax Music 3 (MLX, open weights).

The MiniMax cloud Music API returns HTTP 410 / 2153 for new users, so this runs the
open-weight model through tools/music3.py (copied from the mcp-ultimate series).
Each candidate gets a .json sidecar holding (caption, lyrics, duration, steps, seed),
which is what regenerates it; the audio itself is gitignored.

Caption rules from the minimax-music3-bgm notes: three-section structured caption, and
never name percussion or any voice word, not even negated — naming it summons it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import music3  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAPTION = "\n".join([
    "Global Metadata",
    "Basic Attributes: bpm is 84. key is D major. Warm minimal ambient underscore for a calm technical explainer.",
    "Global Emotional Progression: Focused and quietly optimistic from start to finish, a steady even mood with a soft settle at the end.",
    "Sonics & Production Profile: Even and unobtrusive. Wide gentle stereo field, soft low end, no harsh transients. "
    "Dynamics stay flat so nothing pokes through a voiceover.",
    "Vocal Details",
    "Vocal Gender & Timbre: Instrumental. Felt piano and electric piano.",
    "Arrangement",
    "Instrument Lifecycle Description: Soft felt piano single notes with long decay over warm electric piano chords, "
    "a light round synth bass holding the root, sparse glassy plucks every few bars.",
    "Groove & Foundation Progression: The pulse comes only from the piano and the sustained chords.",
])

for seed in [int(s) for s in sys.argv[1:]] or [7]:
    out = ROOT / f"assets/audio/bgm-seed{seed}.mp3"
    music3.generate(CAPTION, out, duration=150, seed=seed, min_frames=140 * music3.FRAME_RATE, quiet=True)
    print(f"done {out}", flush=True)
