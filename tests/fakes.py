import asyncio
import json
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

import app

SIMPLIFIED_TRANSCRIPT = "今天我们讲 machine learning，类别变项与连续变项。"
TRADITIONAL_TRANSCRIPT = "今天我們講 machine learning，類別變項與連續變項。"
NOTE = "### 00:00:00–00:00:01\n\n**主题：** 变项\n\n## 核心概念\n\n- 类别变项 categorical variables"
FINAL = ("# 机器学习\n\n## 核心概念\n\n- **重点**：连续变项 continuous variables\n\n"
         "| 变项 | 类型 |\n| --- | --- |\n| y | continuous |\n\n"
         '<img src=x onerror="window.injected = true">\n\n'
         "[危险](javascript:alert%281%29)")


def sse(chunks):
    """Encode streamed generateContent chunks the way the Gemini API sends them with alt=sse."""
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)


def candidate(parts, finish_reason=None):
    """One streamed chunk. The real stream puts the finish reason on its last chunk only."""
    chunk = {"candidates": [{"content": {"parts": parts, "role": "model"}, "index": 0}],
             "modelVersion": "gemini-3.6-flash"}
    if finish_reason:
        chunk["candidates"][0]["finishReason"] = finish_reason
    return chunk


def stream_response(chunks, finish_reason="STOP"):
    body = [candidate([{"text": chunk}]) for chunk in chunks]
    # As the API actually closes a stream: an empty text part carrying the finish reason.
    body.append(candidate([{"text": "", "thoughtSignature": "sig"}], finish_reason))
    return sse(body)


class FakeGeminiSocket:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.transcribed = False

    async def recv(self):
        return json.dumps({"setupComplete": {}})

    async def send(self, raw):
        message = json.loads(raw)
        if "audio" in message.get("realtimeInput", {}) and not self.transcribed:
            self.transcribed = True
            await self.messages.put(json.dumps({"serverContent": {
                "interimInputTranscription": {"text": SIMPLIFIED_TRANSCRIPT},
                "inputTranscription": {"text": SIMPLIFIED_TRANSCRIPT},
            }}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.messages.get()

    async def close(self):
        pass


class Recorded(list):
    """Prompts sent to Gemini, plus knobs the test can turn partway through a scenario."""

    connect = None
    always_fail = False


@contextmanager
def fake_services(output_dir, failures=0, final_delay=0):
    requests = Recorded()
    real_client = httpx.AsyncClient

    async def respond(request):
        assert str(request.url) == ("https://generativelanguage.googleapis.com/v1beta/models/"
                                    "gemini-3.5-flash:streamGenerateContent?alt=sse")
        prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        requests.append(prompt)
        if requests.always_fail or len(requests) <= failures:
            return httpx.Response(503)
        if prompt.startswith("Translate the lecture notes"):
            # Read off the instruction line only: the rules mention English in every
            # prompt, and the notes themselves may quote an instruction.
            first = prompt.splitlines()[0]
            language = next(l for l in app.TRANSLATION_LANGUAGES.values()
                            if first == f"Translate the lecture notes below into {l}.")
            return stream_response([f"# Notatki ({language})\n\n- kategoriale Variablen"])
        if "完整上課筆記" in prompt:
            if final_delay:
                await asyncio.sleep(final_delay)
            text = FINAL
        elif "壓縮成一個『章節摘要』" in prompt:
            text = "## 章节摘要\n\n- 类别变项与连续变项。"
        else:
            text = NOTE
        return stream_response([text])

    with ExitStack() as stack:
        for name, value in {
            "GEMINI_API_KEY": "test-key",
            "SUMMARY_MODEL": "gemini-3.5-flash",
            "OUTPUT_DIR": Path(output_dir),
            "NOTE_WINDOW_SECONDS": 0,
            "GEMINI_FINALIZE_GRACE_SECONDS": 0.01,
            "LIVE_RETRY_DELAYS": (0, 0, 0),
            "FINAL_RETRY_DELAYS": (0, 0, 0, 0),
            "BACKGROUND_RETRY_DELAYS": (),
        }.items():
            stack.enter_context(patch.object(app, name, value))
        requests.connect = AsyncMock(side_effect=lambda *a, **kw: FakeGeminiSocket())
        stack.enter_context(patch.object(app.websockets, "connect", requests.connect))
        stack.enter_context(patch.object(app.httpx, "AsyncClient", side_effect=lambda **kw:
                                         real_client(transport=httpx.MockTransport(respond), **kw)))
        yield requests
