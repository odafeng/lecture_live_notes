import asyncio
import base64
import html
import json
import logging
import os
import re
import tempfile
import time
import wave
import zipfile
from contextlib import asynccontextmanager
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
from starlette.background import BackgroundTask

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

# One typed line per entry, so the cap only has to stop a runaway paste.
USER_NOTE_MAX_CHARS = 2000
USER_NOTES_HEADING = "## 我的課堂筆記（原文）"
USER_NOTE_MARKERS = {"note": "✍️", "correction": "⟲ 更正"}
TRANSLATION_LANGUAGES = {"en": "English", "de": "German", "pl": "Polish",
                         "es": "Latin American Spanish"}
# The key is the file suffix, which stays short. Where the document's real BCP-47 tag differs,
# it is spelled out here so screen readers and spellcheckers get the right variant.
HTML_LANG = {"es": "es-419"}
BUNDLE_FILES = ("final_notes.md", "final_notes.html",
                *(f"final_notes.{lang}.{ext}"
                  for lang in TRANSLATION_LANGUAGES for ext in ("md", "html")),
                "live_notes.md", "transcript.txt", "lecture.wav", "session.json")

NOTE_WINDOW_SECONDS = int(os.getenv("NOTE_WINDOW_SECONDS", "60"))
ROLLUP_EVERY_BLOCKS = int(os.getenv("ROLLUP_EVERY_BLOCKS", "10"))
SESSION_ROTATE_SECONDS = int(os.getenv("SESSION_ROTATE_SECONDS", "570"))
GEMINI_FINALIZE_GRACE_SECONDS = float(os.getenv("GEMINI_FINALIZE_GRACE_SECONDS", "1.5"))
# Keepalive pings queue behind audio frames, so a congested uplink delays the pong rather than
# the network dropping. A 20s deadline kept closing healthy sessions (1011 keepalive ping timeout).
GEMINI_PING_INTERVAL_SECONDS = int(os.getenv("GEMINI_PING_INTERVAL_SECONDS", "30"))
GEMINI_PING_TIMEOUT_SECONDS = int(os.getenv("GEMINI_PING_TIMEOUT_SECONDS", "60"))

# Live note blocks fail cheaply (the transcript fallback keeps the material), so they give up fast
# rather than stalling the note worker. The final merge is the expensive one to lose, so it waits.
LIVE_RETRY_DELAYS = (2, 4, 8)
FINAL_RETRY_DELAYS = (5, 15, 45, 120)
BACKGROUND_RETRY_DELAYS = (60, 300, 900)
# Responses are streamed, so `read` bounds the gap between chunks rather than the whole generation.
STREAM_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=60.0, pool=30.0)

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


def corrections_block(corrections: list[str]) -> str:
    """Corrections are instructions, not material: they override whatever the model read earlier."""
    if not corrections:
        return ""
    lines = "\n".join(f"- {c}" for c in corrections)
    return (
        "\n使用者更正（上課者本人當場輸入。這些更正的優先度高於逐字稿與先前的筆記；"
        "遇到衝突一律以更正為準，並依更正改寫受影響的內容。不要在輸出中重述這段說明）：\n"
        f"{lines}\n"
    )


def lecture_note_prompt(text: str, start_ts: str, end_ts: str,
                        corrections: list[str] | None = None) -> str:
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
{corrections_block(corrections or [])}
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


def rollup_prompt(blocks: str, range_label: str, corrections: list[str] | None = None) -> str:
    return f"""
你正在整理一堂長時間大學課程。以下是 {range_label} 的多個分鐘級課堂筆記區塊。
請把它們壓縮成一個『章節摘要』，以便之後整合整堂課。

規則：
- 只保留原筆記已出現的資訊。
- 合併重複內容，但不要丟掉定義、公式、重要數字、因果關係、老師明示的重要事項。
- 保留任何 [待確認]。
- 所有中文一律使用繁體中文（臺灣正體），禁止簡體字；英文術語保留原文。
{corrections_block(corrections or [])}
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


def final_prompt(chapters: str, remaining_blocks: str, course_title: str,
                 user_notes: list[str] | None = None,
                 corrections: list[str] | None = None) -> str:
    handwritten = "\n".join(f"- {n}" for n in (user_notes or [])) or "(無)"
    return f"""
請根據以下課堂摘要素材，整理成一份可複習的完整上課筆記。
課程：{course_title or '未命名課程'}

硬性規則：
- 只能使用素材中已存在的內容，不補充課外知識。
- 不要把「可能是考點」自行推斷成考點；只有老師明示或原筆記記錄為強調時才列入。
- 保留重要定義、公式、數字、因果關係、例子與專有名詞。
- 所有 [待確認] 必須保留在最後的待釐清清單。
- 所有中文一律使用繁體中文（臺灣正體），禁止簡體字；英文術語保留原文。條理清楚，適合考前複習。
- 不要自行輸出「我的課堂筆記」章節；那一段由系統原文附加。
{corrections_block(corrections or [])}
使用者手寫筆記（上課者本人在課堂當下記下的。可信度高於自動整理的推測，
必須整合進對應段落，且不得改寫其判斷 —— 他寫「會考」就是會考）：
{handwritten}

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


def append_user_notes_section(notes: str, user_notes: list[str]) -> str:
    """Appended here rather than asked of the model, so nothing handwritten can be paraphrased away."""
    if not user_notes:
        return notes
    lines = "\n".join(f"- {n}" for n in user_notes)
    return f"{notes.rstrip()}\n\n{USER_NOTES_HEADING}\n\n{lines}"


NOTES_DOCUMENT_CSS = """
:root { color-scheme: light; }
body { margin: 0; background: #f7f7f2; color: #414c43; font-size: 16px; line-height: 1.75;
  font-family: "Avenir Next", "PingFang TC", "Microsoft JhengHei", sans-serif; }
main { max-width: 46rem; margin: 0 auto; padding: 3rem 1.25rem 5rem; }
h1, h2, h3, h4 { color: #283d33; font-family: "Iowan Old Style", "Songti TC", "Noto Serif TC", serif;
  font-weight: 500; line-height: 1.4; }
h1 { font-size: 2rem; margin: 0 0 2rem; padding-bottom: .75rem; border-bottom: 1px solid #dce0d4; }
h2 { font-size: 1.35rem; margin: 2.5rem 0 .75rem; }
h3 { font-size: 1.1rem; margin: 1.75rem 0 .5rem; }
ul, ol { padding-left: 1.4rem; }
li { margin: .35rem 0; }
strong { color: #283d33; }
code { background: #eeefe7; border-radius: 4px; padding: .1em .35em; font-size: .9em; }
blockquote { margin: 1rem 0; padding: .5rem 1rem; border-left: 3px solid #c2d0b6;
  background: #fffefa; color: #606d57; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; display: block; overflow-x: auto; }
th, td { border: 1px solid #dce0d4; padding: .5rem .7rem; text-align: left; }
th { background: #eeefe7; }
hr { border: 0; border-top: 1px solid #dce0d4; margin: 2.5rem 0; }
@media print { body { background: #fff; } main { padding: 0; max-width: none; } }
"""


def render_notes_document(course_title: str, notes: str, lang: str = "zh-Hant") -> str:
    """A standalone page: no network, no sibling files, so it survives being emailed or printed."""
    title = html.escape(course_title.strip() or "課堂筆記")
    return (
        "<!doctype html>\n"
        f'<html lang="{html.escape(lang)}">\n<head>\n<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1" />\n'
        f"<title>{title}</title>\n<style>{NOTES_DOCUMENT_CSS}</style>\n</head>\n"
        f"<body>\n<main>\n{MARKDOWN.render(notes)}</main>\n</body>\n</html>\n"
    )


class AnthropicStreamError(Exception):
    """Retryable error reported by the server inside an SSE stream."""


async def stream_anthropic_message(client: httpx.AsyncClient, headers: dict[str, str],
                                   payload: dict[str, Any]) -> tuple[str, str]:
    """Read one streamed Messages response and return its text and stop reason."""
    parts: list[str] = []
    stop_reason = ""
    async with client.stream("POST", ANTHROPIC_MESSAGES_URL, headers=headers, json=payload) as response:
        if response.status_code >= 400:
            await response.aread()
            response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    parts.append(str(delta.get("text", "")))
            elif kind == "message_delta":
                stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
            elif kind == "error":
                raise AnthropicStreamError((event.get("error") or {}).get("type", "stream_error"))
    return "".join(parts), stop_reason


async def call_anthropic_text(prompt: str, retry_delays: tuple[int, ...] | None = None) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    # Resolved at call time so the module-level default stays patchable.
    if retry_delays is None:
        retry_delays = LIVE_RETRY_DELAYS

    payload = {
        "model": SUMMARY_MODEL,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }

    last_attempt = len(retry_delays)
    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        for attempt in range(last_attempt + 1):
            try:
                text, stop_reason = await stream_anthropic_message(client, headers, payload)
                break
            except (httpx.HTTPStatusError, httpx.TransportError, AnthropicStreamError) as e:
                if isinstance(e, httpx.HTTPStatusError):
                    status = e.response.status_code
                    if status not in {429, 500, 502, 503, 504, 529}:
                        raise
                    reason = f"HTTP {status}"
                elif isinstance(e, AnthropicStreamError):
                    reason = str(e)
                else:
                    reason = type(e).__name__
                if attempt == last_attempt:
                    raise RuntimeError(
                        f"Anthropic 摘要請求失敗（{reason}），已嘗試 {last_attempt + 1} 次。") from e
                delay = retry_delays[attempt]
                logger.warning("Anthropic summary failed (%s); retrying in %s seconds (%s/%s)",
                               reason, delay, attempt + 1, last_attempt)
                await asyncio.sleep(delay)

    if stop_reason == "max_tokens":
        raise RuntimeError("Anthropic 摘要達到輸出長度上限，未產生完整筆記。")
    text = text.strip()
    if not text:
        raise RuntimeError("Anthropic 未回傳可用的摘要文字。")
    return TRADITIONAL_CHINESE.convert(text)


def session_dir_for(session_id: str) -> Path:
    """Recordings are filed under the day they happened: lectures/YYYYMMDD/HHMMSS/."""
    date, _, clock = session_id.partition("_")
    return OUTPUT_DIR / date / clock


def session_paths(session_id: str):
    session_dir = session_dir_for(session_id)
    return {
        "dir": session_dir,
        "wav": session_dir / "lecture.wav",
        "transcript": session_dir / "transcript.txt",
        "notes": session_dir / "live_notes.md",
        "final": session_dir / "final_notes.md",
        "final_html": session_dir / "final_notes.html",
        "meta": session_dir / "session.json",
        "finalize_input": session_dir / "finalize_input.json",
    }


FINAL_STATUS_OK = "ok"
FINAL_STATUS_FAILED = "failed"
SESSION_ID_PATTERN = r"\d{8}_\d{6}"

_background_tasks: set[asyncio.Task] = set()


def final_fallback_notes(course_title: str, chapters: str, remaining: str, error: BaseException) -> str:
    return TRADITIONAL_CHINESE.convert(
        f"# {course_title or '課堂筆記'}\n\n"
        f"最終整併失敗：{type(error).__name__}: {error}\n\n"
        f"## 章節摘要\n{chapters}\n\n"
        f"## 尚未整併筆記\n{remaining}\n"
    )


def write_final_outputs(paths: dict[str, Path], course_title: str, notes: str) -> None:
    """Markdown stays the source of truth; the HTML is the copy that reads and prints anywhere."""
    paths["final"].write_text(notes + "\n", encoding="utf-8")
    paths["final_html"].write_text(render_notes_document(course_title, notes), encoding="utf-8")


def translation_prompt(notes: str, language: str) -> str:
    return f"""
Translate the lecture notes below into {language}.

Rules:
- Translate only. Do not add, remove, explain or summarise anything.
- Keep the Markdown structure exactly: the same headings, lists, tables and emphasis.
- Leave English technical terms in English. They are what the reader will look up.
- Keep every unresolved marker. 「待確認」 becomes the {language} equivalent, but the item stays.
- Output the translated notes only, with no preamble.

Notes:
{notes}
""".strip()


async def write_translations(paths: dict[str, Path], course_title: str,
                             notes: str) -> list[str]:
    """Translated copies for groupmates who do not read Chinese."""
    written = []
    for lang, language in TRANSLATION_LANGUAGES.items():
        markdown = paths["dir"] / f"final_notes.{lang}.md"
        document = paths["dir"] / f"final_notes.{lang}.html"
        # The whole per-language job is isolated, writes included: the Chinese notes are already
        # on disk, so nothing here is worth losing the other language or failing the session.
        try:
            text = await call_anthropic_text(translation_prompt(notes, language),
                                             retry_delays=LIVE_RETRY_DELAYS)
            markdown.write_text(text + "\n", encoding="utf-8")
            document.write_text(
                render_notes_document(course_title, text, lang=HTML_LANG.get(lang, lang)),
                encoding="utf-8")
        except Exception as e:
            logger.warning("%s translation failed: %s: %s", lang, type(e).__name__, e)
            # Whatever is on disk translates notes this run has already replaced. Handing that
            # to a groupmate as the current version is worse than handing them nothing.
            for stale in (markdown, document):
                try:
                    stale.unlink(missing_ok=True)
                except OSError as cleanup_failure:
                    # Losing the wrap-up over a file that will not delete is the worse
                    # trade. It is withheld by its timestamp instead; see translations_on_disk.
                    logger.error("stale copy %s could not be removed (%s)",
                                 stale, cleanup_failure)
            continue
        written.append(lang)
    return written


def final_status_of(session_id: str) -> str | None:
    try:
        meta = json.loads(session_paths(session_id)["meta"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return meta.get("final_notes_status")


def set_final_status(session_id: str, status: str) -> None:
    """Record whether final_notes.md holds a real merge or the raw fallback."""
    meta_path = session_paths(session_id)["meta"]
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    meta["final_notes_status"] = status
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# Both write paths take this: the websocket wrap-up and the re-merge can otherwise interleave,
# leaving one run's notes on disk beside the other run's translations.
_session_locks: dict[str, tuple[asyncio.Lock, int]] = {}


@asynccontextmanager
async def session_lock(session_id: str):
    lock, waiting = _session_locks.get(session_id, (asyncio.Lock(), 0))
    _session_locks[session_id] = (lock, waiting + 1)
    try:
        async with lock:
            yield
    finally:
        # Counted rather than popped on release: a waiter is holding this same object.
        held, waiting = _session_locks[session_id]
        if waiting <= 1:
            del _session_locks[session_id]
        else:
            _session_locks[session_id] = (held, waiting - 1)


async def publish_final_notes(session_id: str, paths: dict[str, Path], course_title: str,
                              notes: str, status: str) -> tuple[str, str]:
    """Write the notes and their translations, unless a better merge landed while we waited."""
    if status == FINAL_STATUS_FAILED and final_status_of(session_id) == FINAL_STATUS_OK:
        # Something merged this session meanwhile. Its notes are real; ours are a stub.
        return paths["final"].read_text(encoding="utf-8"), FINAL_STATUS_OK
    write_final_outputs(paths, course_title, notes)
    # A failed merge leaves a stub, not notes. Translating it would bill for text nobody can
    # revise from; the re-merge translates once it has something real.
    if status != FINAL_STATUS_FAILED:
        await write_translations(paths, course_title, notes)
    return notes, status


async def finalize_session(session_id: str, *, skip_if_merged: bool = False) -> str | None:
    """Re-run the final merge from saved material and overwrite final_notes.md."""
    async with session_lock(session_id):
        # Checked here rather than before the wait: a manual re-run may have succeeded while
        # the background retry was queued behind it.
        if skip_if_merged and final_status_of(session_id) == FINAL_STATUS_OK:
            logger.info("Background finalize skipped for %s: already merged", session_id)
            return None
        return await _finalize_session(session_id)


async def _finalize_session(session_id: str) -> str:
    paths = session_paths(session_id)
    material = json.loads(paths["finalize_input"].read_text(encoding="utf-8"))
    course_title = material.get("course_title", "")
    user_notes = material.get("user_notes") or []
    notes = await call_anthropic_text(
        final_prompt(material.get("chapters", ""), material.get("remaining", ""),
                     course_title, user_notes, material.get("corrections") or []),
        retry_delays=FINAL_RETRY_DELAYS,
    )
    notes = append_user_notes_section(notes, user_notes)
    # The copies on disk translate the notes this run just replaced, so they are refreshed too.
    notes, _ = await publish_final_notes(session_id, paths, course_title, notes,
                                         FINAL_STATUS_OK)
    set_final_status(session_id, FINAL_STATUS_OK)
    return notes


DOWNLOAD_LINK_KEYS = {
    "lecture.wav": "audio", "transcript.txt": "transcript", "live_notes.md": "live_notes",
    "final_notes.md": "final_notes", "final_notes.html": "final_html",
    "session.json": "metadata",
    **{f"final_notes.{lang}.md": f"final_{lang}" for lang in TRANSLATION_LANGUAGES},
    **{f"final_notes.{lang}.html": f"final_{lang}_html" for lang in TRANSLATION_LANGUAGES},
}


TRANSLATION_FILES = frozenset(f"final_notes.{lang}.{ext}"
                              for lang in TRANSLATION_LANGUAGES for ext in ("md", "html"))


def is_current(session_dir: Path, name: str) -> bool:
    """A translation older than final_notes.md describes notes that have been replaced.

    Deleting a superseded copy can fail (a read-only file, a locked one), so the timestamp
    decides what may be handed out rather than trusting that cleanup succeeded.
    """
    if name not in TRANSLATION_FILES:
        return True
    try:
        return (session_dir / name).stat().st_mtime >= (session_dir / "final_notes.md").stat().st_mtime
    except OSError:
        return False


def session_files(session_id: str, names) -> list[str]:
    session_dir = session_dir_for(session_id)
    return [name for name in names
            if (session_dir / name).exists() and is_current(session_dir, name)]


def download_links(session_id: str) -> dict[str, str]:
    """Built from the files on disk, so a copy written by a later re-run still shows up."""
    links = {DOWNLOAD_LINK_KEYS[name]: f"/download/{session_id}/{name}"
             for name in session_files(session_id, DOWNLOAD_LINK_KEYS)}
    links["bundle"] = f"/bundle/{session_id}"
    return links


async def retry_finalize_in_background(session_id: str) -> None:
    """Keep retrying after the browser is gone; the server outlives the websocket."""
    for delay in BACKGROUND_RETRY_DELAYS:
        await asyncio.sleep(delay)
        try:
            await finalize_session(session_id, skip_if_merged=True)
        except Exception as e:
            logger.warning("Background finalize failed for %s (%s)", session_id, e)
        else:
            logger.info("Background finalize succeeded for %s", session_id)
            return


def spawn_background_finalize(session_id: str) -> None:
    task = asyncio.create_task(retry_finalize_in_background(session_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.post("/finalize/{session_id}")
async def finalize(session_id: str):
    if not re.fullmatch(SESSION_ID_PATTERN, session_id):
        raise HTTPException(status_code=404)
    if not session_paths(session_id)["finalize_input"].exists():
        raise HTTPException(status_code=404, detail="這堂課沒有可重新整併的素材。")
    try:
        notes = await finalize_session(session_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"重新整併失敗：{type(e).__name__}: {e}")
    return {"session_id": session_id, "text": notes, "html": MARKDOWN.render(notes),
            "files": download_links(session_id)}


@app.get("/sessions/incomplete")
async def incomplete_sessions():
    """Sessions whose final merge never succeeded, so the browser can offer a re-run."""
    sessions = []
    for meta_path in sorted(OUTPUT_DIR.glob("*/*/session.json"), reverse=True):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("final_notes_status") != FINAL_STATUS_FAILED:
            continue
        if not (meta_path.parent / "finalize_input.json").exists():
            continue
        sessions.append({
            "session_id": meta.get(
                "session_id", f"{meta_path.parent.parent.name}_{meta_path.parent.name}"),
            "course_title": meta.get("course_title", ""),
            "started_at": meta.get("started_at", ""),
        })
        if len(sessions) >= 5:
            break
    return {"sessions": sessions}


def bundle_filename(session_id: str) -> str:
    """Named after the lecture, so a folder of downloads stays readable."""
    title = ""
    try:
        meta = json.loads(session_paths(session_id)["meta"].read_text(encoding="utf-8"))
        title = str(meta.get("course_title", ""))
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    slug = re.sub(r'[\\/:*?"<>|]+', "", normalize_text(title))[:60].strip()
    return f"{slug}_{session_id}.zip" if slug else f"lecture_{session_id}.zip"


@app.get("/bundle/{session_id}")
async def bundle(session_id: str):
    """Everything this lecture produced, in one file."""
    if not re.fullmatch(SESSION_ID_PATTERN, session_id):
        raise HTTPException(status_code=404)
    session_dir = session_dir_for(session_id)
    present = session_files(session_id, BUNDLE_FILES)
    if not present:
        raise HTTPException(status_code=404)

    # Zipped to a temp file rather than memory: an hour of lecture audio is not small.
    archive = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    archive.close()
    try:
        with zipfile.ZipFile(archive.name, "w", zipfile.ZIP_DEFLATED) as bundled:
            for name in present:
                bundled.write(session_dir / name, arcname=name)
    except BaseException:
        os.unlink(archive.name)
        raise
    return FileResponse(archive.name, media_type="application/zip",
                        filename=bundle_filename(session_id),
                        background=BackgroundTask(os.unlink, archive.name))


@app.get("/download/{session_id}/{filename}")
async def download(session_id: str, filename: str):
    allowed = {"lecture.wav", "transcript.txt", "live_notes.md", "final_notes.md",
               "final_notes.html", "session.json"}
    allowed |= {f"final_notes.{lang}.{ext}"
                for lang in TRANSLATION_LANGUAGES for ext in ("md", "html")}
    if not re.fullmatch(r"\d{8}_\d{6}", session_id) or filename not in allowed:
        raise HTTPException(status_code=404)
    session_dir = session_dir_for(session_id)
    path = session_dir / filename
    # is_current keeps a superseded translation from being served through a link kept
    # from before the re-merge.
    if not path.exists() or not is_current(session_dir, filename):
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
    # Typed by the person in the room: notes become review material, corrections steer the model.
    user_notes: list[str] = []
    corrections: list[str] = []

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
                    ping_interval=GEMINI_PING_INTERVAL_SECONDS,
                    ping_timeout=GEMINI_PING_TIMEOUT_SECONDS,
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
                if receiver_task:
                    if not receiver_task.done():
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
                block = await call_anthropic_text(
                    lecture_note_prompt(text, start_ts, end_ts, corrections))
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
                    chapter = await call_anthropic_text(
                        rollup_prompt("\n\n".join(group), range_label, corrections))
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
                elif msg_type == "user_note":
                    kind = "correction" if payload.get("kind") == "correction" else "note"
                    text = normalize_text(TRADITIONAL_CHINESE.convert(
                        str(payload.get("text", ""))))[:USER_NOTE_MAX_CHARS]
                    if text:
                        entry = f"[{format_elapsed(await get_audio_elapsed())}] {text}"
                        (corrections if kind == "correction" else user_notes).append(entry)
                        display = f"{USER_NOTE_MARKERS[kind]} {entry}"
                        async with notes_file_lock:
                            with paths["notes"].open("a", encoding="utf-8") as f:
                                f.write(f"> {display}\n\n")
                        await safe_send({"type": "user_note", "kind": kind, "text": display})
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
        # Written before the merge runs so a failed session can be re-merged without the websocket.
        paths["finalize_input"].write_text(json.dumps(
            {"course_title": course_title, "chapters": chapters, "remaining": remaining,
             "user_notes": user_notes, "corrections": corrections},
            ensure_ascii=False, indent=2), encoding="utf-8")

        # The merge runs inside the lock, not just the writing. The user can press "re-merge"
        # while this one is still waiting on the API; if that run succeeds and this one then
        # fails, a fallback written afterwards would bury the good notes.
        async with session_lock(session_id):
            final_status = FINAL_STATUS_OK
            try:
                final_notes = await call_anthropic_text(
                    final_prompt(chapters, remaining, course_title, user_notes, corrections),
                    retry_delays=FINAL_RETRY_DELAYS)
            except Exception as e:
                final_status = FINAL_STATUS_FAILED
                final_notes = final_fallback_notes(course_title, chapters, remaining, e)

            # Appended on both paths: a failed merge must not cost the user their own words.
            final_notes = append_user_notes_section(final_notes, user_notes)
            final_notes, final_status = await publish_final_notes(
                session_id, paths, course_title, final_notes, final_status)
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
            "user_note_count": len(user_notes),
            "correction_count": len(corrections),
            "note_window_seconds": NOTE_WINDOW_SECONDS,
            "gemini_session_rotate_seconds": SESSION_ROTATE_SECONDS,
            "disconnected": disconnected,
            "final_notes_status": final_status,
        }
        paths["meta"].write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        if final_status == FINAL_STATUS_FAILED:
            spawn_background_finalize(session_id)

        if not disconnected:
            await safe_send({"type": "final", "text": final_notes,
                             "html": MARKDOWN.render(final_notes), "status": final_status})
            await safe_send({
                "type": "saved",
                "session_id": session_id,
                "files": download_links(session_id),
            })
            await safe_send({"type": "status", "text": "本堂課筆記整理完成。"})
            try:
                await ws.close()
            except Exception:
                pass


app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")
