import asyncio
import base64
import json
import logging
import os
import re
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import websockets
from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from opencc import OpenCC

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ANTHROPIC_API_KEY = (
    dotenv_values(BASE_DIR / ".env").get("ANTHROPIC_API_KEY")
    or os.getenv("ANTHROPIC_API_KEY", "")
).strip()
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "claude-haiku-4-5-20251001")
TRANSCRIPTION_MODE = os.getenv("TRANSCRIPTION_MODE", "SMART").upper()
DEFAULT_CUSTOM_VOCABULARY = [
    x.strip() for x in os.getenv("CUSTOM_VOCABULARY", "").split(",") if x.strip()
]

NOTE_WINDOW_SECONDS = int(os.getenv("NOTE_WINDOW_SECONDS", "60"))
ROLLUP_EVERY_BLOCKS = int(os.getenv("ROLLUP_EVERY_BLOCKS", "10"))
SESSION_ROTATE_SECONDS = int(os.getenv("SESSION_ROTATE_SECONDS", "570"))
GEMINI_FINALIZE_GRACE_SECONDS = float(os.getenv("GEMINI_FINALIZE_GRACE_SECONDS", "1.5"))

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "lectures")).expanduser()
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = BASE_DIR / OUTPUT_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

GEMINI_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

app = FastAPI(title="Lecture Live Notes — Gemini Transcribe Live + Claude Haiku")
logger = logging.getLogger(__name__)
TRADITIONAL_CHINESE = OpenCC("s2tw")
MARKDOWN = MarkdownIt("js-default")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def format_elapsed(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def dedupe_terms(terms: list[str], limit: int = 100) -> list[str]:
    seen = set()
    result = []
    for term in terms:
        term = normalize_text(term)
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            result.append(term)
        if len(result) >= limit:
            break
    return result


def lecture_note_prompt(text: str, start_ts: str, end_ts: str) -> str:
    return f"""
你正在替一堂大學課程製作『即時課堂筆記』。
以下是 {start_ts}–{end_ts} 的逐字稿片段。

規則：
- 只能根據逐字稿寫，不補充課外知識，不自行猜測。
- 忽略口頭贅詞、重複與無意義片段。
- 保留重要定義、因果關係、公式、數字、日期、人名與專有名詞。
- 若逐字稿本身看起來可能有辨識錯誤，請標記「[待確認]」，不要自行改成你認為正確的內容。
- 老師如果明確說「重要、會考、記住、重點」或反覆強調，請記錄。
- 所有中文一律使用繁體中文（臺灣正體），禁止簡體字；英文術語保留原文。
- 不需要把逐字稿重新抄一次。

逐字稿：
{text}

輸出格式：
### {start_ts}–{end_ts}
**主題：** 一句話
- 重點：...
- 重點：...
- 定義／公式／數字：...（沒有就省略）
- 老師特別強調：...（沒有就省略）
- 待確認：...（沒有就省略）
""".strip()


def rollup_prompt(blocks: str, range_label: str) -> str:
    return f"""
你正在整理一堂長時間大學課程。以下是 {range_label} 的多個分鐘級課堂筆記區塊。
請把它們壓縮成一個『章節摘要』，以便之後整合整堂課。

規則：
- 只保留原筆記已出現的資訊。
- 合併重複內容，但不要丟掉定義、公式、重要數字、因果關係、老師明示的重要事項。
- 保留任何 [待確認]。
- 所有中文一律使用繁體中文（臺灣正體），禁止簡體字；英文術語保留原文。

筆記區塊：
{blocks}

輸出：
## 章節 {range_label}
### 核心概念
- ...
### 關鍵細節
- ...
### 老師強調／明示考點
- ...
### 待確認
- ...
沒有內容的段落可省略。
""".strip()


def final_prompt(chapters: str, remaining_blocks: str, course_title: str) -> str:
    return f"""
請根據以下課堂摘要素材，整理成一份可複習的完整上課筆記。
課程：{course_title or '未命名課程'}

硬性規則：
- 只能使用素材中已存在的內容，不補充課外知識。
- 不要把「可能是考點」自行推斷成考點；只有老師明示或原筆記記錄為強調時才列入。
- 保留重要定義、公式、數字、因果關係、例子與專有名詞。
- 所有 [待確認] 必須保留在最後的待釐清清單。
- 所有中文一律使用繁體中文（臺灣正體），禁止簡體字；英文術語保留原文。條理清楚，適合考前複習。

章節摘要：
{chapters or '(尚無章節摘要)'}

尚未整併的最後筆記：
{remaining_blocks or '(無)'}

輸出格式：
# {course_title or '課堂筆記'}
## 本堂課總覽
## 核心概念與架構
## 詳細重點
## 重要定義／公式／數字
## 老師特別強調或明示考點
## 待釐清事項
""".strip()


async def call_anthropic_text(prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    payload = {
        "model": SUMMARY_MODEL,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }

    async with httpx.AsyncClient(timeout=180) as client:
        for attempt in range(4):
            try:
                response = await client.post(ANTHROPIC_MESSAGES_URL, headers=headers, json=payload)
                response.raise_for_status()
                break
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                if isinstance(e, httpx.HTTPStatusError):
                    status = e.response.status_code
                    if status not in {429, 500, 502, 503, 504, 529}:
                        raise
                    reason = f"HTTP {status}"
                else:
                    reason = type(e).__name__
                if attempt == 3:
                    raise RuntimeError(f"Anthropic 摘要請求失敗（{reason}），已嘗試 4 次。") from e
                delay = 2 ** (attempt + 1)
                logger.warning("Anthropic summary failed (%s); retrying in %s seconds (%s/3)",
                               reason, delay, attempt + 1)
                await asyncio.sleep(delay)
        data = response.json()

    if data.get("stop_reason") == "max_tokens":
        raise RuntimeError("Anthropic 摘要達到輸出長度上限，未產生完整筆記。")
    parts = data.get("content") or []
    text = "".join(part.get("text", "") for part in parts
                   if isinstance(part, dict) and part.get("type") == "text").strip()
    if not text:
        raise RuntimeError("Anthropic 未回傳可用的摘要文字。")
    return TRADITIONAL_CHINESE.convert(text)


def session_paths(session_id: str):
    session_dir = OUTPUT_DIR / session_id
    return {
        "dir": session_dir,
        "wav": session_dir / "lecture.wav",
        "transcript": session_dir / "transcript.txt",
        "notes": session_dir / "live_notes.md",
        "final": session_dir / "final_notes.md",
        "meta": session_dir / "session.json",
    }


@app.get("/download/{session_id}/{filename}")
async def download(session_id: str, filename: str):
    allowed = {"lecture.wav", "transcript.txt", "live_notes.md", "final_notes.md", "session.json"}
    if not re.fullmatch(r"\d{8}_\d{6}", session_id) or filename not in allowed:
        raise HTTPException(status_code=404)
    path = OUTPUT_DIR / session_id / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, filename=filename)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "app_id": "lecture-live-notes",
        "transcribe_model": TRANSCRIBE_MODEL,
        "summary_provider": "anthropic",
        "summary_model": SUMMARY_MODEL,
        "api_key_configured": bool(GEMINI_API_KEY),
        "summary_api_key_configured": bool(ANTHROPIC_API_KEY),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()

    send_lock = asyncio.Lock()
    notes_file_lock = asyncio.Lock()

    async def safe_send(payload: dict[str, Any]):
        try:
            async with send_lock:
                await ws.send_json(payload)
        except Exception:
            pass

    if not GEMINI_API_KEY:
        await safe_send({
            "type": "error",
            "text": "伺服器尚未設定 GEMINI_API_KEY。請先在 .env 填入 Gemini API key。",
        })
        await ws.close()
        return

    if not ANTHROPIC_API_KEY:
        await safe_send({
            "type": "error",
            "text": "伺服器尚未設定 ANTHROPIC_API_KEY。請先在 .env 填入 Anthropic API key。",
        })
        await ws.close()
        return

    # Browser sends session metadata before microphone audio starts.
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=15)
        first_payload = json.loads(first)
    except Exception:
        await safe_send({"type": "error", "text": "沒有收到課程設定資料。"})
        await ws.close()
        return

    if first_payload.get("type") != "meta":
        await safe_send({"type": "error", "text": "第一個訊息必須是 meta。"})
        await ws.close()
        return

    course_title = TRADITIONAL_CHINESE.convert(str(first_payload.get("course_title", "")).strip()[:200])
    requested_mode = str(first_payload.get("transcription_mode", TRANSCRIPTION_MODE)).upper()
    transcription_mode = requested_mode if requested_mode in {"SMART", "VERBATIM"} else "SMART"

    user_vocab = first_payload.get("custom_vocabulary", [])
    if isinstance(user_vocab, str):
        user_vocab = re.split(r"[,，\n]", user_vocab)
    if not isinstance(user_vocab, list):
        user_vocab = []
    custom_vocabulary = dedupe_terms(DEFAULT_CUSTOM_VOCABULARY + [str(x) for x in user_vocab], 100)

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = session_paths(session_id)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["transcript"].write_text("", encoding="utf-8")
    paths["notes"].write_text("# 即時課堂筆記\n\n", encoding="utf-8")

    wav_file = wave.open(str(paths["wav"]), "wb")
    wav_file.setnchannels(1)
    wav_file.setsampwidth(BYTES_PER_SAMPLE)
    wav_file.setframerate(SAMPLE_RATE)

    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=500)
    transcript_queue: asyncio.Queue[tuple[str, float] | None] = asyncio.Queue()
    asr_failed = asyncio.Event()

    audio_bytes_received = 0
    state_lock = asyncio.Lock()

    note_blocks: list[str] = []
    chapter_summaries: list[str] = []

    await safe_send({"type": "session", "session_id": session_id})
    await safe_send({
        "type": "ready",
        "text": f"Gemini Live 已就緒：{TRANSCRIBE_MODEL} / {transcription_mode}",
        "custom_vocabulary_count": len(custom_vocabulary),
    })

    async def get_audio_elapsed() -> float:
        async with state_lock:
            return audio_bytes_received / (SAMPLE_RATE * BYTES_PER_SAMPLE)

    async def append_final_transcript(text: str):
        text = normalize_text(TRADITIONAL_CHINESE.convert(text))
        if not text:
            return

        elapsed = await get_audio_elapsed()
        stamp = format_elapsed(elapsed)
        line = f"[{stamp}] {text}"
        with paths["transcript"].open("a", encoding="utf-8") as f:
            f.write(line + "\n")

        await transcript_queue.put((text, elapsed))
        await safe_send({"type": "transcript", "line": line})
        await safe_send({"type": "interim", "text": ""})

    def parse_server_content(message: dict[str, Any]) -> dict[str, Any]:
        value = message.get("serverContent")
        return value if isinstance(value, dict) else {}

    async def gemini_receiver(gemini_ws):
        async for raw in gemini_ws:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            server_content = parse_server_content(message)

            interim = server_content.get("interimInputTranscription")
            if isinstance(interim, dict):
                text = normalize_text(TRADITIONAL_CHINESE.convert(str(interim.get("text", ""))))
                if text:
                    await safe_send({"type": "interim", "text": text})

            final = server_content.get("inputTranscription")
            if isinstance(final, dict):
                text = normalize_text(str(final.get("text", "")))
                if text:
                    await append_final_transcript(text)

    async def wait_for_setup(gemini_ws):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(gemini_ws.recv(), timeout=15)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            message = json.loads(raw)
            if "setupComplete" in message:
                return
            # Normally transcription does not arrive before setupComplete, but don't discard it silently.
            server_content = parse_server_content(message)
            if server_content.get("inputTranscription"):
                final = server_content["inputTranscription"]
                if isinstance(final, dict):
                    await append_final_transcript(str(final.get("text", "")))
        raise TimeoutError("Gemini Live setup timed out")

    async def gemini_asr_worker():
        url = f"{GEMINI_WS_URL}?key={GEMINI_API_KEY}"
        carry_chunk: bytes | None = None
        stop_requested = False
        failures = 0
        session_number = 0

        while not stop_requested:
            session_number += 1
            receiver_task = None
            gemini_ws = None
            should_rotate = False

            try:
                gemini_ws = await websockets.connect(
                    url,
                    max_size=8 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                )

                setup_message = {
                    "setup": {
                        "model": f"models/{TRANSCRIBE_MODEL}",
                        "generationConfig": {"responseModalities": ["TEXT"]},
                        "inputAudioTranscription": {
                            "languageCodes": [],
                            "customVocabulary": custom_vocabulary,
                            "mode": transcription_mode,
                        },
                    }
                }
                await gemini_ws.send(json.dumps(setup_message))
                await wait_for_setup(gemini_ws)
                failures = 0

                await safe_send({
                    "type": "asr_session",
                    "session_number": session_number,
                    "text": f"Gemini ASR session #{session_number} 已連線",
                })

                receiver_task = asyncio.create_task(gemini_receiver(gemini_ws))
                started = time.monotonic()

                while True:
                    if receiver_task.done():
                        exc = receiver_task.exception()
                        if exc:
                            raise exc
                        raise ConnectionError("Gemini Live connection ended unexpectedly")

                    if time.monotonic() - started >= SESSION_ROTATE_SECONDS:
                        should_rotate = True
                        break

                    if carry_chunk is not None:
                        chunk = carry_chunk
                        carry_chunk = None
                    else:
                        try:
                            chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue

                    if chunk is None:
                        stop_requested = True
                        break

                    realtime_message = {
                        "realtimeInput": {
                            "audio": {
                                "data": base64.b64encode(chunk).decode("ascii"),
                                "mimeType": "audio/pcm;rate=16000",
                            }
                        }
                    }
                    try:
                        await gemini_ws.send(json.dumps(realtime_message))
                    except Exception:
                        carry_chunk = chunk
                        raise

                try:
                    await gemini_ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                except Exception:
                    pass

                # Give Gemini a short window to emit the authoritative final transcript for the last utterance.
                if receiver_task and not receiver_task.done():
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(receiver_task),
                            timeout=GEMINI_FINALIZE_GRACE_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        pass

                if should_rotate and not stop_requested:
                    await safe_send({
                        "type": "status",
                        "text": "Gemini Live session 自動輪替中，錄音會持續緩衝。",
                    })

            except asyncio.CancelledError:
                raise
            except Exception as e:
                failures += 1
                await safe_send({
                    "type": "asr_error",
                    "text": f"Gemini Live 連線錯誤：{type(e).__name__}: {e}",
                })
                if failures >= 5:
                    asr_failed.set()
                    await safe_send({
                        "type": "error",
                        "text": "Gemini Live 連續連線失敗 5 次。錄音仍會保存，但本堂課不再產生即時逐字稿。",
                    })
                    return
                await asyncio.sleep(min(1.5 * failures, 6))
            finally:
                if receiver_task and not receiver_task.done():
                    receiver_task.cancel()
                    try:
                        await receiver_task
                    except BaseException:
                        pass
                if gemini_ws is not None:
                    try:
                        await gemini_ws.close()
                    except Exception:
                        pass

    async def note_worker():
        note_buffer: list[str] = []
        note_start_seconds: float | None = None
        last_elapsed = 0.0

        async def create_note_block(force: bool = False):
            nonlocal note_buffer, note_start_seconds, last_elapsed
            if not note_buffer or note_start_seconds is None:
                return
            if not force and (last_elapsed - note_start_seconds) < NOTE_WINDOW_SECONDS:
                return

            text = " ".join(note_buffer).strip()
            start_ts = format_elapsed(note_start_seconds)
            end_ts = format_elapsed(last_elapsed)

            try:
                block = await call_anthropic_text(lecture_note_prompt(text, start_ts, end_ts))
            except Exception as e:
                block = TRADITIONAL_CHINESE.convert(
                    f"### {start_ts}–{end_ts}\n"
                    f"[筆記摘要失敗：{type(e).__name__}: {e}]\n"
                    f"逐字稿片段：{text}"
                )

            note_blocks.append(block)
            async with notes_file_lock:
                with paths["notes"].open("a", encoding="utf-8") as f:
                    f.write(block + "\n\n")
            await safe_send({"type": "note_block", "text": block, "html": MARKDOWN.render(block)})

            note_buffer = []
            note_start_seconds = None

            if len(note_blocks) >= ROLLUP_EVERY_BLOCKS:
                group = note_blocks[:ROLLUP_EVERY_BLOCKS]
                del note_blocks[:ROLLUP_EVERY_BLOCKS]
                range_label = f"截至 {end_ts}"
                try:
                    chapter = await call_anthropic_text(rollup_prompt("\n\n".join(group), range_label))
                    chapter_summaries.append(chapter)
                    await safe_send({"type": "chapter", "text": chapter})
                except Exception as e:
                    note_blocks[0:0] = group
                    await safe_send({"type": "summary_error", "text": f"章節整併失敗：{e}"})

        while True:
            item = await transcript_queue.get()
            if item is None:
                await create_note_block(force=True)
                return

            text, elapsed = item
            if note_start_seconds is None:
                note_start_seconds = elapsed
            last_elapsed = elapsed
            note_buffer.append(text)
            await create_note_block(force=False)

    asr_task = asyncio.create_task(gemini_asr_worker())
    notes_task = asyncio.create_task(note_worker())

    stopped = False
    disconnected = False

    try:
        while not stopped:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                disconnected = True
                stopped = True
                break

            if message.get("bytes") is not None:
                data = message["bytes"]
                wav_file.writeframesraw(data)
                async with state_lock:
                    audio_bytes_received += len(data)

                if not asr_failed.is_set():
                    await audio_queue.put(data)

            elif message.get("text") is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    payload = {}

                msg_type = payload.get("type")
                if msg_type == "mark":
                    sec = await get_audio_elapsed()
                    marker = f"> ⭐ 使用者標記重點：{format_elapsed(sec)}"
                    async with notes_file_lock:
                        with paths["notes"].open("a", encoding="utf-8") as f:
                            f.write(marker + "\n\n")
                    await safe_send({"type": "marker", "text": marker})
                elif msg_type == "stop":
                    stopped = True

    except WebSocketDisconnect:
        disconnected = True
        stopped = True
    except Exception as e:
        await safe_send({"type": "error", "text": f"{type(e).__name__}: {e}"})
        stopped = True
    finally:
        # End ASR and wait for final transcript events before closing the note worker.
        if not asr_failed.is_set():
            await audio_queue.put(None)
        else:
            if not asr_task.done():
                asr_task.cancel()

        try:
            await asyncio.wait_for(asr_task, timeout=15)
        except asyncio.TimeoutError:
            asr_task.cancel()
            try:
                await asr_task
            except BaseException:
                pass
        except BaseException:
            pass

        await transcript_queue.put(None)
        try:
            await notes_task
        except BaseException:
            pass

        wav_file.close()

        remaining = "\n\n".join(note_blocks)
        chapters = "\n\n".join(chapter_summaries)
        try:
            final_notes = await call_anthropic_text(final_prompt(chapters, remaining, course_title))
        except Exception as e:
            final_notes = TRADITIONAL_CHINESE.convert(
                f"# {course_title or '課堂筆記'}\n\n"
                f"最終整併失敗：{type(e).__name__}: {e}\n\n"
                f"## 章節摘要\n{chapters}\n\n"
                f"## 尚未整併筆記\n{remaining}\n"
            )

        paths["final"].write_text(final_notes + "\n", encoding="utf-8")
        duration = await get_audio_elapsed()
        meta = {
            "session_id": session_id,
            "course_title": course_title,
            "started_at": session_id,
            "audio_seconds": round(duration, 1),
            "transcribe_model": TRANSCRIBE_MODEL,
            "summary_provider": "anthropic",
            "summary_model": SUMMARY_MODEL,
            "transcription_mode": transcription_mode,
            "custom_vocabulary": custom_vocabulary,
            "note_window_seconds": NOTE_WINDOW_SECONDS,
            "gemini_session_rotate_seconds": SESSION_ROTATE_SECONDS,
            "disconnected": disconnected,
        }
        paths["meta"].write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        if not disconnected:
            await safe_send({"type": "final", "text": final_notes, "html": MARKDOWN.render(final_notes)})
            await safe_send({
                "type": "saved",
                "session_id": session_id,
                "files": {
                    "audio": f"/download/{session_id}/lecture.wav",
                    "transcript": f"/download/{session_id}/transcript.txt",
                    "live_notes": f"/download/{session_id}/live_notes.md",
                    "final_notes": f"/download/{session_id}/final_notes.md",
                    "metadata": f"/download/{session_id}/session.json",
                },
            })
            await safe_send({"type": "status", "text": "本堂課筆記整理完成。"})
            try:
                await ws.close()
            except Exception:
                pass


app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")
