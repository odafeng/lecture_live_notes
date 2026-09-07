import json
import os
from pathlib import Path
import runpy
import unittest
from unittest.mock import AsyncMock, call, patch

import httpx
from fastapi.testclient import TestClient

import app


def text_response(text="### 课堂笔记\n- **重点**：machine learning 的类别变项。"):
    return httpx.Response(200, json={
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
    })


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = patch.object(app, "ANTHROPIC_API_KEY", "test-key")
        self.key.start()
        self.addCleanup(self.key.stop)

    async def request_with_responses(self, responses):
        requests = []
        outcomes = iter(responses)

        def respond(request):
            requests.append(request)
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(app.httpx, "AsyncClient", return_value=client), \
                patch.object(app.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            try:
                result = await app.call_anthropic_text("請整理課堂筆記。")
            except Exception as exc:
                result = exc
        return result, requests, sleep

    async def test_success_converts_chinese_and_preserves_english_and_markdown(self):
        result, requests, sleep = await self.request_with_responses([text_response()])
        self.assertEqual(result, "### 課堂筆記\n- **重點**：machine learning 的類別變項。")
        self.assertEqual(len(requests), 1)
        sleep.assert_not_awaited()
        request = requests[0]
        self.assertEqual(str(request.url), "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.headers["x-api-key"], "test-key")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
        self.assertNotIn("x-goog-api-key", request.headers)
        payload = json.loads(request.content)
        self.assertEqual(payload["model"], app.SUMMARY_MODEL)
        self.assertEqual(payload["max_tokens"], 8192)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "請整理課堂筆記。"}])

    async def test_transient_http_failure_recovers(self):
        for status in (429, 500, 502, 503, 504, 529):
            with self.subTest(status=status):
                result, requests, sleep = await self.request_with_responses([
                    httpx.Response(status), text_response(),
                ])
                self.assertIsInstance(result, str)
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[0].content, requests[1].content)
                sleep.assert_awaited_once_with(2)

    async def test_exponential_backoff_recovers_on_last_attempt(self):
        result, requests, sleep = await self.request_with_responses([
            httpx.Response(503), httpx.Response(503), httpx.Response(503), text_response(),
        ])
        self.assertIsInstance(result, str)
        self.assertEqual(len(requests), 4)
        self.assertEqual(sleep.await_args_list, [call(2), call(4), call(8)])

    async def test_exhausted_retries_report_status_without_raw_url(self):
        result, requests, sleep = await self.request_with_responses([
            httpx.Response(503) for _ in range(4)
        ])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("HTTP 503", str(result))
        self.assertIn("4 次", str(result))
        self.assertNotIn("https://", str(result))
        self.assertEqual(len(requests), 4)
        self.assertEqual(sleep.await_count, 3)

    async def test_permanent_http_errors_are_not_retried(self):
        for status in (400, 401, 403, 404):
            with self.subTest(status=status):
                result, requests, sleep = await self.request_with_responses([
                    httpx.Response(status),
                ])
                self.assertIsInstance(result, httpx.HTTPStatusError)
                self.assertEqual(len(requests), 1)
                sleep.assert_not_awaited()

    async def test_transport_timeout_recovers(self):
        result, requests, sleep = await self.request_with_responses([
            httpx.ReadTimeout("timed out"), text_response(),
        ])
        self.assertIsInstance(result, str)
        self.assertEqual(len(requests), 2)
        sleep.assert_awaited_once_with(2)

    async def test_empty_response_is_not_retried(self):
        result, requests, sleep = await self.request_with_responses([text_response("")])
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(len(requests), 1)
        sleep.assert_not_awaited()

    async def test_only_text_blocks_are_returned(self):
        result, _, _ = await self.request_with_responses([httpx.Response(200, json={
            "content": [{"type": "thinking", "thinking": "internal"},
                        {"type": "text", "text": "课堂"},
                        {"type": "text", "text": "笔记"}],
            "stop_reason": "end_turn",
        })])
        self.assertEqual(result, "課堂筆記")

    async def test_truncated_summary_is_not_reported_as_complete(self):
        result, requests, sleep = await self.request_with_responses([httpx.Response(200, json={
            "content": [{"type": "text", "text": "未完成的笔记"}],
            "stop_reason": "max_tokens",
        })])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("輸出長度上限", str(result))
        self.assertEqual(len(requests), 1)
        sleep.assert_not_awaited()

    async def test_missing_anthropic_key_does_not_send_request(self):
        with patch.object(app, "ANTHROPIC_API_KEY", ""):
            result, requests, sleep = await self.request_with_responses([])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("ANTHROPIC_API_KEY", str(result))
        self.assertEqual(requests, [])
        sleep.assert_not_awaited()


class ConfigurationTests(unittest.TestCase):
    def test_relative_output_is_anchored_to_project_directory(self):
        for configured, expected in (("lectures", Path(app.__file__).parent / "lectures"),
                                     ("/tmp/lecture-test-output", Path("/tmp/lecture-test-output"))):
            with self.subTest(configured=configured), \
                    patch.dict(os.environ, {"OUTPUT_DIR": configured}), patch("pathlib.Path.mkdir"):
                configuration = runpy.run_path(app.__file__)
                self.assertEqual(configuration["OUTPUT_DIR"], expected)

    def test_project_anthropic_key_takes_precedence_over_shell_key(self):
        for file_key, expected in (("project-key", "project-key"), ("", "shell-key")):
            with self.subTest(file_key=file_key), \
                    patch("dotenv.dotenv_values", return_value={"ANTHROPIC_API_KEY": file_key}), \
                    patch.dict(os.environ, {"ANTHROPIC_API_KEY": "shell-key"}):
                configuration = runpy.run_path(app.__file__)
                self.assertEqual(configuration["ANTHROPIC_API_KEY"], expected)


class MarkdownTests(unittest.TestCase):
    def test_formats_headings_bold_lists_and_tables(self):
        html = app.MARKDOWN.render("## 課堂筆記\n\n- **重點**：machine learning\n\n"
                                   "| 變項 | 類型 |\n| --- | --- |\n| y | continuous |")
        self.assertIn("<h2>課堂筆記</h2>", html)
        self.assertIn("<li><strong>重點</strong>", html)
        self.assertIn("<table>", html)

    def test_raw_html_and_script_links_are_not_executable(self):
        html = app.MARKDOWN.render('<script>alert(1)</script>\n\n'
                                   '<img src=x onerror="alert(1)">\n\n'
                                   '[點擊](javascript:alert%281%29)')
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn("&lt;script&gt;", html)


class SmokeTests(unittest.TestCase):
    def test_app_starts_and_serves_home_and_health(self):
        with TestClient(app.app) as client:
            home = client.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("開始上課", home.text)
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertTrue(health.json()["ok"])
            self.assertEqual(health.json()["app_id"], "lecture-live-notes")
            self.assertEqual(health.json()["summary_provider"], "anthropic")
            self.assertIn("summary_api_key_configured", health.json())

    def test_missing_summary_key_is_reported_before_recording(self):
        with patch.object(app, "GEMINI_API_KEY", "test-gemini-key"), \
                patch.object(app, "ANTHROPIC_API_KEY", ""), TestClient(app.app) as client:
            with client.websocket_connect("/ws") as ws:
                error = ws.receive_json()
                self.assertEqual(error["type"], "error")
                self.assertIn("ANTHROPIC_API_KEY", error["text"])


if __name__ == "__main__":
    unittest.main()
