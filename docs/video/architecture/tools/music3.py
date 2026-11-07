#!/usr/bin/env python3
"""MiniMax Music 3 的本機推論後端（Apple Silicon / MLX）。

**為什麼不再打 API**：MiniMax 的 `POST /v1/music_generation` 在 2026-08 對新用戶關閉，
所有 model 一律回 `HTTP 410 / status_code 2153`（本專案兩把金鑰都試過，含付費那把）。
官方在錯誤訊息裡指向開源權重 `MiniMaxAI/MiniMax-Music3`，這支就是接那條路。

用的是社群量化的 `mlx-community/MiniMax-Music3-4bit`（9.2 GB，M4 Pro / 24 GB 跑得動）。
130 秒 / 30 steps 約 10–15 分鐘，全程離線、不計費。

**這個改動帶來一件以前做不到的事：`seed` 讓 BGM 可重生。**
舊 API 沒有 seed 參數，素材一刪就永久消失（見 CONVENTIONS「不可重生產物」那條）。
現在只要記住 (caption, lyrics, duration, steps, seed) 就能重跑出同一段音樂。
→ 所以每支素材都要把這五個值寫進 `<out>.json` sidecar，不要只留音檔。

一次性安裝：
    python3 -m venv ~/.venvs/mlxaudio
    ~/.venvs/mlxaudio/bin/pip install \\
      "mlx-audio @ git+https://github.com/Blaizzy/mlx-audio.git@784b29e2691a93ca7483147d86f61859dfaa6296"
    ~/.venvs/mlxaudio/bin/hf download mlx-community/MiniMax-Music3-4bit
（PyPI 上的 mlx-audio 還沒帶 Music3 支援，要裝上面那個 merge commit。）
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MODEL = os.environ.get("MUSIC3_MODEL", "mlx-community/MiniMax-Music3-4bit")
PYTHON = Path(os.environ.get("MUSIC3_PYTHON", Path.home() / ".venvs/mlxaudio/bin/python"))
MAX_STEPS = 30          # mlx-audio 硬上限，傳更大會 ValueError
FRAME_RATE = 25         # minimax_music3/config.py 的 FRAME_RATE；min_frames 用這個換算秒數

# 純樂器：只給段落標籤、不給任何字。有字它就會唱。
INSTRUMENTAL_LYRICS = "[intro]\n[instrumental]\n[instrumental]\n[instrumental]\n[outro]\n"


def structured_caption(prompt, *, bpm=None, key=None, instrumental=True):
    """把舊式一行 prompt 包成 Music 3 要的 Structured Caption。

    Music 3 吃的是「Global Metadata / Vocal Details / Arrangement」三段式描述，
    餵一行 tag 串會讓它自由發揮（包括自己加鼓、自己開始唱）。
    series.yaml 裡既有的 `bgm.*_prompt` 都是一行式，所以在這裡補齊骨架，
    不用回頭改每個系列的設定檔。想要完整控制就直接傳整段三段式進來。
    """
    if "Global Metadata" in prompt:      # 已經是結構化描述，原樣送
        return prompt

    basics = [b for b in (f"bpm is {bpm}." if bpm else None,
                          f"key is {key}." if key else None) if b]
    vocals = ("Instrumental only. There are no singers, no choir, no humming, "
              "no vocal samples and no spoken word at any point in the track."
              if instrumental else "Follow the description above.")
    return "\n".join([
        "Global Metadata",
        f"Basic Attributes: {' '.join(basics)} {prompt}".strip(),
        f"Global Emotional Progression: {prompt}",
        "Sonics & Production Profile: Even and unobtrusive. Wide gentle stereo field, "
        "soft low end, no harsh transients. Dynamics stay flat so nothing pokes through a voiceover.",
        "Vocal Details",
        f"Vocal Gender & Timbre: {vocals}",
        "Arrangement",
        f"Instrument Lifecycle Description: {prompt}",
        "Groove & Foundation Progression: No drums, no percussion, no hi-hats. "
        "The pulse comes only from the sustained instruments."
        if instrumental else "Follow the description above.",
    ])


def _check_backend():
    if not PYTHON.exists():
        sys.exit(f"找不到 mlx-audio 的 python：{PYTHON}\n"
                 f"裝法見 {Path(__file__).name} 的 docstring，或設 MUSIC3_PYTHON 指到別的 venv。")


def mask_end_token(orig_ar_one_frame, min_frames):
    """包住 mlx-audio 的 `ar_one_frame`：前 min_frames 幀不准取樣 end token。

    **為什麼需要**：`--duration` 只是上限，AR 迴圈取樣到 end token 就停，時長不在條件裡。
    EP01（mcp-ultimate）實測純樂器要 150–200 s、只拿到 26–55 s，五支全卡在 78 s 門檻下。
    做法：把 config.audio_end_token_id 暫時換成 -1。end token（151670）不在音訊碼區間
    （151675 起）裡，從 `allowed` 拿掉它就等於禁止收尾，其餘取樣邏輯一行不動。
    第 0 次呼叫只暖機、不產幀（generate_frame_hiddens 的 emit_frame=False），
    所以要遮 min_frames+1 次呼叫，才保證至少 min_frames 幀。
    """
    calls = [0]

    def masked(language_model, depth, config, *args, **kwargs):
        mask = calls[0] <= min_frames
        calls[0] += 1
        if not mask:
            return orig_ar_one_frame(language_model, depth, config, *args, **kwargs)
        end, config.audio_end_token_id = config.audio_end_token_id, -1
        try:
            return orig_ar_one_frame(language_model, depth, config, *args, **kwargs)
        finally:
            config.audio_end_token_id = end

    return masked


def _run_with_min_frames(argv):
    """在 mlx-audio venv 裡執行：裝上 mask_end_token 後交給 mlx_audio.music.generate 的 CLI。"""
    from mlx_audio.music.models.minimax_music3 import ar
    from mlx_audio.music import generate as mlx_generate
    ar.ar_one_frame = mask_end_token(ar.ar_one_frame, int(argv[0]))
    sys.argv = ["mlx_audio.music.generate", *argv[1:]]
    mlx_generate.main()


def generate(caption, out, *, duration=130, steps=MAX_STEPS, seed=7,
             lyrics=INSTRUMENTAL_LYRICS, quiet=False, min_frames=None):
    """生成音樂到 out（副檔名決定格式，.mp3 會用 ffmpeg 轉），並寫 out.json sidecar。

    min_frames：前 N 幀禁止收尾（25 幀 = 1 s），見 mask_end_token()。
    不傳就是原本的行為，sidecar 也不多寫欄位；有傳就記進 sidecar，否則重生不出同一支。

    回傳 (out_path, meta_dict)。meta 就是重生這支素材需要的全部參數。
    """
    _check_backend()
    if min_frames is not None and min_frames >= duration * FRAME_RATE:
        sys.exit(f"min_frames={min_frames} 必須小於 duration×{FRAME_RATE}={duration * FRAME_RATE:.0f}"
                 "（duration 是上限，到了一樣會停）")
    steps = min(steps, MAX_STEPS)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "lyrics.txt").write_text(lyrics, encoding="utf-8")
        wav = tmp / "raw.wav"
        entry = (["-m", "mlx_audio.music.generate"] if min_frames is None else
                 [str(Path(__file__).resolve()), "--_min-frames-runner", str(min_frames)])
        cmd = [str(PYTHON), *entry,
               "--model", MODEL, "--caption", caption,
               "--lyrics-file", str(tmp / "lyrics.txt"),
               "--duration", str(duration), "--steps", str(steps),
               "--seed", str(seed), "--output", str(wav)]
        if quiet:
            cmd.append("--quiet")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not wav.exists():
            sys.exit(f"MiniMax-Music3 生成失敗：\n{r.stdout[-800:]}\n{r.stderr[-800:]}")

        if out.suffix.lower() == ".wav":
            out.write_bytes(wav.read_bytes())
        else:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav),
                            "-b:a", "256k", "-ar", "44100", str(out)], check=True)

    meta = {"backend": "mlx-audio/MiniMax-Music3", "model": MODEL, "caption": caption,
            "lyrics": lyrics, "duration": duration, "steps": steps, "seed": seed}
    if min_frames is not None:
        meta["min_frames"] = min_frames
    out.with_suffix(out.suffix + ".json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return out, meta


# 只給 generate(min_frames=...) 用：它會用 mlx-audio venv 的 python 跑這支檔案本身。
if __name__ == "__main__" and sys.argv[1:2] == ["--_min-frames-runner"]:
    _run_with_min_frames(sys.argv[2:])
