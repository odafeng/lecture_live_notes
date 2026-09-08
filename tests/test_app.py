import asyncio
import json
import os
from contextlib import contextmanager
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import AsyncMock, call, patch

import httpx
from fastapi.testclient import TestClient

import app
from tests.fakes import sse, stream_response


def text_response(text="### 课堂笔记\n- **重点**：machine learning 的类别变项。"):
    return stream_response([text])


class BrokenStream(httpx.AsyncByteStream):
    """A response that dies partway through, the way a dropped connection does."""

    def __init__(self, payload: bytes):
        self.payload = payload

    async def __aiter__(self):
        yield self.payload
        raise httpx.RemoteProtocolError("peer closed connection mid-stream")


def truncated_stream(text):
    event = {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": text}}
    body = f"event: content_block_delta\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          stream=BrokenStream(body.encode()))


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.key = patch.object(app, "ANTHROPIC_API_KEY", "test-key")
        self.key.start()
        self.addCleanup(self.key.stop)

    async def request_with_responses(self, responses, retry_delays=None):
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
                result = await app.call_anthropic_text("請整理課堂筆記。", retry_delays)
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
        self.assertTrue(payload["stream"])
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

    async def test_only_text_deltas_are_returned(self):
        result, _, _ = await self.request_with_responses([sse([
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "thinking_delta", "thinking": "internal"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "课堂"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "笔记"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        ])])
        self.assertEqual(result, "課堂筆記")

    async def test_truncated_summary_is_not_reported_as_complete(self):
        result, requests, sleep = await self.request_with_responses([
            stream_response(["未完成的笔记"], stop_reason="max_tokens"),
        ])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("輸出長度上限", str(result))
        self.assertEqual(len(requests), 1)
        sleep.assert_not_awaited()

    async def test_dropped_stream_is_retried_without_duplicating_partial_text(self):
        result, requests, sleep = await self.request_with_responses([
            truncated_stream("前半段筆記"), text_response("完整笔记"),
        ])
        self.assertEqual(result, "完整筆記")
        self.assertEqual(len(requests), 2)
        sleep.assert_awaited_once_with(2)

    async def test_error_event_inside_stream_is_retried(self):
        result, requests, sleep = await self.request_with_responses([
            sse([{"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}]),
            text_response(),
        ])
        self.assertIsInstance(result, str)
        self.assertEqual(len(requests), 2)
        sleep.assert_awaited_once_with(2)

    async def test_error_event_names_its_type_when_retries_run_out(self):
        result, _, _ = await self.request_with_responses(
            [sse([{"type": "error", "error": {"type": "overloaded_error"}}]) for _ in range(4)])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("overloaded_error", str(result))

    async def test_final_merge_waits_far_longer_than_a_live_note(self):
        result, requests, sleep = await self.request_with_responses(
            [httpx.Response(503) for _ in range(4)] + [text_response()],
            retry_delays=app.FINAL_RETRY_DELAYS)
        self.assertIsInstance(result, str)
        self.assertEqual(len(requests), 5)
        self.assertEqual(sleep.await_args_list, [call(5), call(15), call(45), call(120)])
        self.assertGreater(sum(app.FINAL_RETRY_DELAYS), 10 * sum(app.LIVE_RETRY_DELAYS))

    async def test_missing_anthropic_key_does_not_send_request(self):
        with patch.object(app, "ANTHROPIC_API_KEY", ""):
            result, requests, sleep = await self.request_with_responses([])
        self.assertIsInstance(result, RuntimeError)
        self.assertIn("ANTHROPIC_API_KEY", str(result))
        self.assertEqual(requests, [])
        sleep.assert_not_awaited()


class FinalizeTests(unittest.TestCase):
    SESSION = "20260908_090321"

    def setUp(self):
        output = tempfile.TemporaryDirectory()
        self.addCleanup(output.cleanup)
        self.output = Path(output.name)
        # One folder per day, one subfolder per recording: lectures/YYYYMMDD/HHMMSS/.
        self.session_dir = self.output / "20260908" / "090321"
        self.session_dir.mkdir(parents=True)
        for target, value in (("OUTPUT_DIR", self.output), ("ANTHROPIC_API_KEY", "test-key")):
            patcher = patch.object(app, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_session(self, session_id=None, status=app.FINAL_STATUS_FAILED, material=True):
        session_id = session_id or self.SESSION
        date, clock = session_id.split("_")
        directory = self.output / date / clock
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "session.json").write_text(json.dumps({
            "session_id": session_id, "course_title": "機器學習",
            "started_at": session_id, "final_notes_status": status,
        }, ensure_ascii=False), encoding="utf-8")
        (directory / "final_notes.md").write_text(
            "# 機器學習\n\n最終整併失敗：RuntimeError: boom\n", encoding="utf-8")
        if material:
            (directory / "finalize_input.json").write_text(json.dumps({
                "course_title": "機器學習", "chapters": "## 章節摘要 A",
                "remaining": "### 尚未整併的一段",
            }, ensure_ascii=False), encoding="utf-8")

    @contextmanager
    def anthropic(self, *responses):
        prompts = []
        outcomes = iter(responses)
        real_client = httpx.AsyncClient

        def respond(request):
            prompts.append(json.loads(request.content)["messages"][0]["content"])
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(app.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
                transport=httpx.MockTransport(respond), **kw)), \
                patch.object(app, "FINAL_RETRY_DELAYS", (0, 0, 0, 0)):
            yield prompts

    def status(self):
        return json.loads((self.session_dir / "session.json").read_text(encoding="utf-8"))

    def test_rerun_replaces_the_fallback_and_clears_the_failed_status(self):
        self.write_session()
        with self.anthropic(stream_response(["# 机器学习\n\n## 本堂课总览\n\n- 类别变项"])) as prompts, \
                TestClient(app.app) as client:
            response = client.post(f"/finalize/{self.SESSION}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("類別變項", response.json()["text"])
        self.assertIn("<h1>機器學習</h1>", response.json()["html"])
        notes = (self.session_dir / "final_notes.md").read_text(encoding="utf-8")
        self.assertIn("類別變項", notes)
        self.assertNotIn("最終整併失敗", notes)
        self.assertEqual(self.status()["final_notes_status"], app.FINAL_STATUS_OK)
        # The re-run reads the saved material, never the fallback notes it is replacing.
        self.assertIn("## 章節摘要 A", prompts[0])
        self.assertIn("### 尚未整併的一段", prompts[0])
        self.assertNotIn("最終整併失敗", prompts[0])

    def test_rerun_without_saved_material_is_not_offered(self):
        self.write_session(material=False)
        with TestClient(app.app) as client:
            self.assertEqual(client.post(f"/finalize/{self.SESSION}").status_code, 404)

    def test_rerun_rejects_malformed_session_ids(self):
        with TestClient(app.app) as client:
            for bad in ("not-a-session", "2026_0908", "20260908"):
                with self.subTest(session_id=bad):
                    self.assertEqual(client.post(f"/finalize/{bad}").status_code, 404)

    def test_failed_rerun_keeps_the_fallback_and_the_failed_status(self):
        self.write_session()
        with self.anthropic(*[httpx.Response(503) for _ in range(5)]), TestClient(app.app) as client:
            response = client.post(f"/finalize/{self.SESSION}")

        self.assertEqual(response.status_code, 502)
        self.assertIn("HTTP 503", response.json()["detail"])
        self.assertEqual(self.status()["final_notes_status"], app.FINAL_STATUS_FAILED)
        self.assertIn("最終整併失敗",
                      (self.session_dir / "final_notes.md").read_text(encoding="utf-8"))

    def test_a_day_gets_one_folder_holding_each_recording(self):
        self.write_session()
        self.write_session("20260908_143000", status=app.FINAL_STATUS_OK)
        self.write_session("20260909_090000", status=app.FINAL_STATUS_OK)
        self.assertEqual(sorted(p.name for p in self.output.iterdir() if p.is_dir()),
                         ["20260908", "20260909"])
        self.assertEqual(sorted(p.name for p in (self.output / "20260908").iterdir()),
                         ["090321", "143000"])

    def test_downloads_resolve_under_the_day_folder(self):
        self.write_session()
        (self.session_dir / "transcript.txt").write_text("逐字稿", encoding="utf-8")
        with TestClient(app.app) as client:
            self.assertEqual(client.get(f"/download/{self.SESSION}/transcript.txt").text, "逐字稿")
            self.assertEqual(client.get("/download/20260101_000000/transcript.txt").status_code, 404)

    def test_incomplete_lists_only_failed_sessions_that_can_still_be_merged(self):
        self.write_session()
        self.write_session("20260908_100000", status=app.FINAL_STATUS_OK)
        self.write_session("20260908_110000", material=False)
        with TestClient(app.app) as client:
            sessions = client.get("/sessions/incomplete").json()["sessions"]
        self.assertEqual([s["session_id"] for s in sessions], [self.SESSION])
        self.assertEqual(sessions[0]["course_title"], "機器學習")


class BackgroundFinalizeTests(unittest.IsolatedAsyncioTestCase):
    @contextmanager
    def finalize_returning(self, *outcomes, delays=(0, 0, 0)):
        with patch.object(app, "BACKGROUND_RETRY_DELAYS", delays), \
                patch.object(app, "finalize_session", new_callable=AsyncMock) as finalize:
            finalize.side_effect = list(outcomes)
            yield finalize

    async def test_background_retry_stops_at_the_first_success(self):
        with self.finalize_returning(RuntimeError("still offline"), "筆記",
                                     RuntimeError("never reached")) as finalize:
            await app.retry_finalize_in_background("20260908_090321")
        self.assertEqual(finalize.await_count, 2)

    async def test_background_retry_gives_up_after_the_last_delay(self):
        with self.finalize_returning(*[RuntimeError("still offline")] * 3) as finalize:
            await app.retry_finalize_in_background("20260908_090321")
        self.assertEqual(finalize.await_count, 3)

    async def test_spawned_task_is_referenced_so_it_is_not_garbage_collected(self):
        with self.finalize_returning("筆記") as finalize:
            app.spawn_background_finalize("20260908_090321")
            self.assertEqual(len(app._background_tasks), 1)
            await asyncio.sleep(0)
            await asyncio.gather(*app._background_tasks)
        self.assertEqual(finalize.await_count, 1)
        self.assertEqual(app._background_tasks, set())


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
