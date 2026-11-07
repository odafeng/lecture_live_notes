#!/usr/bin/env python3
"""Synthesize one MiniMax clone-voice clip per storyboard frame from SCRIPT.md.

Idempotent: a clip is re-synthesized only when its hash (model, voice settings, spoken text)
changes. Writes assets/voice/frame-NN.mp3 and voice_manifest.json (frame -> path, duration).
"""
import hashlib, json, re, subprocess, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = "speech-2.8-hd"
VOICE = {"voice_id": "moss_audio_ae939d41-6788-11f1-a909-feb3e5c18eb0",
         "speed": 1.15, "vol": 1.0, "pitch": 0, "emotion": "happy"}


def read_key():
    for env in (ROOT / ".env", Path.home() / ".claude/series-studio/.env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if line.strip().startswith("MINIMAX_API_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("MINIMAX_API_KEY not found")


def lines():
    text = (ROOT / "SCRIPT.md").read_text(encoding="utf-8")
    for n, body in re.findall(r"## Line (\d+) .*?\n(.*?)(?=\n## Line|\Z)", text, re.S):
        spoken = " ".join(l.strip() for l in body.splitlines() if l.startswith("    "))
        yield int(n), spoken


def synth(key, text):
    payload = {"model": MODEL, "text": text, "stream": False, "voice_setting": VOICE,
               "audio_setting": {"sample_rate": 44100, "format": "mp3", "channel": 1}}
    req = urllib.request.Request("https://api.minimax.io/v1/t2a_v2", data=json.dumps(payload).encode(),
                                 method="POST", headers={"Authorization": "Bearer " + key,
                                                         "Content-Type": "application/json"})
    for attempt in range(6):
        r = json.loads(urllib.request.urlopen(req, timeout=180).read())
        status = r.get("base_resp", {}).get("status_code")
        if status in (0, None):
            return bytes.fromhex(r["data"]["audio"])
        if status != 1002:
            sys.exit(f"MiniMax error: {r['base_resp']}")
        time.sleep(15 + attempt * 5)
    sys.exit("MiniMax rate limit did not clear")


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nk=1:nw=1", str(path)], capture_output=True, text=True)
    return round(float(out.stdout.strip()), 3)


def main():
    manifest_path = ROOT / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    key = None
    for n, spoken in lines():
        digest = hashlib.sha1(json.dumps([MODEL, VOICE, spoken], ensure_ascii=False).encode()).hexdigest()[:12]
        rel = f"assets/voice/frame-{n:02d}.mp3"
        entry = manifest.get(str(n))
        if entry and entry["hash"] == digest and (ROOT / rel).exists():
            print(f"= frame {n:02d} cached ({entry['duration_s']}s)")
            continue
        key = key or read_key()
        (ROOT / rel).write_bytes(synth(key, spoken))
        manifest[str(n)] = {"path": rel, "hash": digest, "text": spoken, "duration_s": duration(ROOT / rel)}
        print(f"+ frame {n:02d} {manifest[str(n)]['duration_s']}s")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    total = sum(v["duration_s"] for v in manifest.values())
    print(f"total narration {total:.1f}s")


if __name__ == "__main__":
    main()
