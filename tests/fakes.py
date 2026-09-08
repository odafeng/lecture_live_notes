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


def sse(events):
    """Encode Anthropic streaming events the way the Messages API sends them."""
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
                   for e in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)


def stream_response(chunks, stop_reason="end_turn"):
    events = [{"type": "message_start", "message": {"id": "msg_test", "content": []}},
              {"type": "content_block_start", "index": 0,
               "content_block": {"type": "text", "text": ""}}]
    events += [{"type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": chunk}} for chunk in chunks]
    events += [{"type": "content_block_stop", "index": 0},
               {"type": "message_delta", "delta": {"stop_reason": stop_reason},
                "usage": {"output_tokens": 9}},
               {"type": "message_stop"}]
    return sse(events)


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
    """Prompts sent to Anthropic, plus knobs the test can turn partway through a scenario."""

    connect = None
    always_fail = False


@contextmanager
def fake_services(output_dir, failures=0, final_delay=0):
    requests = Recorded()
    real_client = httpx.AsyncClient

    async def respond(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        prompt = json.loads(request.content)["messages"][0]["content"]
        requests.append(prompt)
        if requests.always_fail or len(requests) <= failures:
            return httpx.Response(503)
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
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "SUMMARY_MODEL": "claude-haiku-4-5-20251001",
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
