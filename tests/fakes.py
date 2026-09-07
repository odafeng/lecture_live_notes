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


@contextmanager
def fake_services(output_dir, failures=0, final_delay=0):
    requests = []
    real_client = httpx.AsyncClient

    async def respond(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        prompt = json.loads(request.content)["messages"][0]["content"]
        requests.append(prompt)
        if len(requests) <= failures:
            return httpx.Response(503)
        if "完整上課筆記" in prompt:
            if final_delay:
                await asyncio.sleep(final_delay)
            text = FINAL
        elif "壓縮成一個『章節摘要』" in prompt:
            text = "## 章节摘要\n\n- 类别变项与连续变项。"
        else:
            text = NOTE
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        })

    with ExitStack() as stack:
        for name, value in {
            "GEMINI_API_KEY": "test-key",
            "ANTHROPIC_API_KEY": "test-anthropic-key",
            "SUMMARY_MODEL": "claude-haiku-4-5-20251001",
            "OUTPUT_DIR": Path(output_dir),
            "NOTE_WINDOW_SECONDS": 0,
            "GEMINI_FINALIZE_GRACE_SECONDS": 0.01,
        }.items():
            stack.enter_context(patch.object(app, name, value))
        stack.enter_context(patch.object(app.websockets, "connect",
                                         AsyncMock(side_effect=lambda *a, **kw: FakeGeminiSocket())))
        stack.enter_context(patch.object(app.httpx, "AsyncClient", side_effect=lambda **kw:
                                         real_client(transport=httpx.MockTransport(respond), **kw)))
        yield requests
