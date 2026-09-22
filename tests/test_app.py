import asyncio
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path
import runpy
import tempfile
import unittest
import zipfile
from unittest.mock import AsyncMock, call, patch

import httpx
from fastapi.testclient import TestClient

import app
from tests.fakes import sse, stream_response


def translation_target(prompt):
    """Read the language off the instruction line: the notes themselves may quote one."""
    first = prompt.splitlines()[0]
    return next(l for l in app.TRANSLATION_LANGUAGES.values()
                if first == f"Translate the lecture notes below into {l}.")


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


class UserInputTests(unittest.TestCase):
    NOTE = "[00:12:35] 老師說這頁會考"
    CORRECTION = "[00:20:01] 老師說的是 ResNet，不是 resonate"

    def test_handwritten_notes_reach_the_final_prompt_as_priority_material(self):
        prompt = app.final_prompt("章節摘要", "尚未整併", "機器學習", [self.NOTE], [])
        self.assertIn(self.NOTE, prompt)
        self.assertIn("手寫筆記", prompt)

    def test_final_notes_keep_the_handwritten_section_verbatim(self):
        notes = app.append_user_notes_section("# 機器學習\n\n## 本堂課總覽\n", [self.NOTE])
        self.assertIn(app.USER_NOTES_HEADING, notes)
        self.assertIn(f"- {self.NOTE}", notes)

    def test_without_handwritten_notes_no_section_is_added(self):
        self.assertEqual(app.append_user_notes_section("# 機器學習\n", []), "# 機器學習\n")

    def test_corrections_steer_every_later_generation(self):
        for stage, prompt in {
            "note": app.lecture_note_prompt("逐字稿", "00:00:00", "00:01:00", [self.CORRECTION]),
            "rollup": app.rollup_prompt("筆記區塊", "截至 00:10:00", [self.CORRECTION]),
            "final": app.final_prompt("章節摘要", "尚未整併", "機器學習", [], [self.CORRECTION]),
        }.items():
            with self.subTest(stage=stage):
                self.assertIn(self.CORRECTION, prompt)
                self.assertIn("使用者更正", prompt)

    def test_prompts_stay_unchanged_when_nothing_was_corrected(self):
        for stage, prompt in {
            "note": app.lecture_note_prompt("逐字稿", "00:00:00", "00:01:00", []),
            "rollup": app.rollup_prompt("筆記區塊", "截至 00:10:00", []),
            "final": app.final_prompt("章節摘要", "尚未整併", "機器學習", [], []),
        }.items():
            with self.subTest(stage=stage):
                self.assertNotIn("使用者更正", prompt)

    def test_a_correction_steers_the_model_but_is_not_review_material(self):
        notes = app.append_user_notes_section("# 機器學習\n", [self.NOTE])
        self.assertNotIn(self.CORRECTION, notes)


class NotesDocumentTests(unittest.TestCase):
    def test_html_output_is_a_standalone_document_carrying_the_rendered_notes(self):
        document = app.render_notes_document("機器學習", "# 機器學習\n\n- **重點**：變項")
        self.assertTrue(document.startswith("<!doctype html>"))
        self.assertIn("<title>機器學習</title>", document)
        self.assertIn("<strong>重點</strong>", document)
        # Self-contained: printable and readable with no network and no sibling files.
        self.assertIn("<style>", document)
        self.assertNotIn("<link", document)
        self.assertNotIn("<script", document)

    def test_html_output_escapes_a_title_that_looks_like_markup(self):
        document = app.render_notes_document("<script>x</script>", "# 課")
        self.assertNotIn("<script>x</script>", document)
        self.assertIn("&lt;script&gt;", document)

    def test_untitled_lecture_still_gets_a_document_title(self):
        self.assertIn("<title>課堂筆記</title>", app.render_notes_document("", "# 課"))


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

    def write_session(self, session_id=None, status=app.FINAL_STATUS_FAILED, material=True,
                      user_notes=(), corrections=()):
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
                "user_notes": list(user_notes), "corrections": list(corrections),
            }, ensure_ascii=False), encoding="utf-8")

    @contextmanager
    def anthropic(self, *responses):
        prompts = []
        outcomes = iter(responses)
        real_client = httpx.AsyncClient

        def respond(request):
            prompt = json.loads(request.content)["messages"][0]["content"]
            prompts.append(prompt)
            if prompt.startswith("Translate the lecture notes"):
                language = translation_target(prompt)
                return stream_response([f"# Notes in {language}"])
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

    def test_rerun_reapplies_handwritten_notes_and_corrections(self):
        self.write_session(user_notes=["[00:12:35] 老師說這頁會考"],
                           corrections=["[00:20:01] 老師說的是 ResNet，不是 resonate"])
        with self.anthropic(stream_response(["# 机器学习\n\n## 本堂课总览\n\n- 类别变项"])) as prompts, \
                TestClient(app.app) as client:
            response = client.post(f"/finalize/{self.SESSION}")

        self.assertEqual(response.status_code, 200)
        # Both survive the round trip through finalize_input.json, so a re-run is not a downgrade.
        self.assertIn("老師說這頁會考", prompts[0])
        self.assertIn("ResNet，不是 resonate", prompts[0])
        notes = (self.session_dir / "final_notes.md").read_text(encoding="utf-8")
        self.assertIn(app.USER_NOTES_HEADING, notes)
        self.assertIn("- [00:12:35] 老師說這頁會考", notes)

    def test_rerun_refreshes_the_html_output_alongside_the_markdown(self):
        self.write_session()
        (self.session_dir / "final_notes.html").write_text("<p>舊的</p>", encoding="utf-8")
        with self.anthropic(stream_response(["# 机器学习\n\n## 本堂课总览"])), TestClient(app.app) as client:
            self.assertEqual(client.post(f"/finalize/{self.SESSION}").status_code, 200)
        document = (self.session_dir / "final_notes.html").read_text(encoding="utf-8")
        self.assertNotIn("舊的", document)
        self.assertIn("<h1>機器學習</h1>", document)

    def test_rerun_refreshes_the_translated_copies(self):
        """A re-merge replaces the notes, so translations of the old text must not survive."""
        self.write_session()
        (self.session_dir / "final_notes.pl.md").write_text(
            "# Stare notatki\n", encoding="utf-8")

        with self.anthropic(stream_response(["# 机器学习\n\n- 类别变项"])), \
                TestClient(app.app) as client:
            self.assertEqual(client.post(f"/finalize/{self.SESSION}").status_code, 200)

        polish = (self.session_dir / "final_notes.pl.md").read_text(encoding="utf-8")
        self.assertNotIn("Stare notatki", polish)
        self.assertIn("Polish", polish)
        self.assertTrue((self.session_dir / "final_notes.de.md").exists())

    def test_rerun_hands_back_the_links_including_the_new_translations(self):
        """The panel is rebuilt from this response; without it the copies stay invisible."""
        self.write_session()
        with self.anthropic(stream_response(["# 机器学习\n\n- 类别变项"])), \
                TestClient(app.app) as client:
            body = client.post(f"/finalize/{self.SESSION}").json()

        self.assertIn("final_pl", body["files"])
        self.assertIn("final_de_html", body["files"])
        self.assertEqual(body["files"]["final_notes"],
                         f"/download/{self.SESSION}/final_notes.md")
        self.assertEqual(client.get(body["files"]["final_pl"]).status_code, 200)

    def test_background_retry_leaves_an_already_merged_session_alone(self):
        """A manual re-run may have succeeded while the retry was still sleeping."""
        self.write_session(status=app.FINAL_STATUS_OK)
        merges = []

        async def record(session_id):
            merges.append(session_id)
            return ""

        # Patched below finalize_session, so the real skip guard still runs. Patching
        # finalize_session itself hid a TypeError and passed for the wrong reason.
        with patch.object(app, "BACKGROUND_RETRY_DELAYS", (0,)), \
                patch.object(app, "_finalize_session", record):
            asyncio.run(app.retry_finalize_in_background(self.SESSION))

        self.assertEqual(merges, [])

    def test_two_merges_of_one_session_do_not_overlap(self):
        """Interleaved runs left the notes on one version and the translations on another."""
        depth = 0
        peak = 0
        real_client = httpx.AsyncClient

        async def respond(request):
            nonlocal depth, peak
            depth += 1
            peak = max(peak, depth)
            try:
                await asyncio.sleep(0)
                return stream_response(["# 机器学习"])
            finally:
                depth -= 1

        async def both():
            with patch.object(app.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
                    transport=httpx.MockTransport(respond), **kw)):
                await asyncio.gather(app.finalize_session(self.SESSION),
                                     app.finalize_session(self.SESSION))

        self.write_session()
        asyncio.run(both())
        self.assertEqual(peak, 1)

    def test_background_retry_rechecks_after_it_gets_the_lock(self):
        """It may have waited behind a manual re-run that already succeeded."""
        self.write_session()
        merges = []

        async def manual():
            await app.finalize_session(self.SESSION)

        async def both():
            real_client = httpx.AsyncClient

            async def respond(request):
                merges.append(json.loads(request.content)["messages"][0]["content"])
                await asyncio.sleep(0)
                return stream_response(["# 机器学习"])

            with patch.object(app.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
                    transport=httpx.MockTransport(respond), **kw)), \
                    patch.object(app, "BACKGROUND_RETRY_DELAYS", (0,)):
                await asyncio.gather(manual(), app.retry_finalize_in_background(self.SESSION))

        asyncio.run(both())
        self.assertEqual(sum("完整上課筆記" in m for m in merges), 1)

    def test_locks_do_not_pile_up_one_per_lecture(self):
        """A local server runs for weeks; a dict keyed by session must not grow forever."""
        self.write_session()
        with self.anthropic(stream_response(["# 机器学习"])), TestClient(app.app) as client:
            client.post(f"/finalize/{self.SESSION}")
        self.assertNotIn(self.SESSION, app._session_locks)

    def test_a_late_failure_does_not_bury_notes_that_already_merged(self):
        """The first wrap-up can finish after a re-merge succeeded; its stub must not win."""
        self.write_session(status=app.FINAL_STATUS_OK)
        paths = app.session_paths(self.SESSION)
        paths["final"].write_text("# 機器學習\n\n- 真正的筆記\n", encoding="utf-8")
        stub = "# 機器學習\n\n最終整併失敗：RuntimeError: boom\n"

        notes, status = asyncio.run(app.publish_final_notes(
            self.SESSION, paths, "機器學習", stub, app.FINAL_STATUS_FAILED))

        self.assertEqual(status, app.FINAL_STATUS_OK)
        self.assertIn("真正的筆記", notes)
        self.assertIn("真正的筆記", paths["final"].read_text(encoding="utf-8"))

    def test_a_translation_older_than_the_notes_is_not_offered(self):
        """A copy that outlived the notes it translated must not be handed out as current."""
        self.write_session(status=app.FINAL_STATUS_OK)
        session_dir = self.session_dir
        for name in ("final_notes.pl.md", "final_notes.pl.html",
                     "final_notes.de.md", "final_notes.de.html"):
            (session_dir / name).write_text("stale", encoding="utf-8")
        merged_at = (session_dir / "final_notes.md").stat().st_mtime
        for name in ("final_notes.pl.md", "final_notes.pl.html"):
            os.utime(session_dir / name, (merged_at - 60, merged_at - 60))

        links = app.download_links(self.SESSION)
        self.assertNotIn("final_pl", links)
        self.assertNotIn("final_pl_html", links)
        self.assertIn("final_de", links)

        with TestClient(app.app) as client:
            with zipfile.ZipFile(io.BytesIO(client.get(f"/bundle/{self.SESSION}").content)) as z:
                self.assertNotIn("final_notes.pl.md", z.namelist())
                self.assertIn("final_notes.de.md", z.namelist())
            # A link kept from before the re-merge must not reach the stale copy either.
            self.assertEqual(
                client.get(f"/download/{self.SESSION}/final_notes.pl.md").status_code, 404)
            self.assertEqual(
                client.get(f"/download/{self.SESSION}/final_notes.de.md").status_code, 200)

    def test_html_output_can_be_downloaded(self):
        self.write_session()
        (self.session_dir / "final_notes.html").write_text("<h1>機器學習</h1>", encoding="utf-8")
        with TestClient(app.app) as client:
            response = client.get(f"/download/{self.SESSION}/final_notes.html")
        self.assertEqual(response.status_code, 200)
        self.assertIn("機器學習", response.text)

    def test_bundle_zips_every_file_the_lecture_produced(self):
        self.write_session()
        for name in ("lecture.wav", "transcript.txt", "live_notes.md", "final_notes.html",
                     "final_notes.en.md", "final_notes.en.html",
                     "final_notes.de.md", "final_notes.de.html",
                     "final_notes.pl.md", "final_notes.pl.html",
                     "final_notes.es.md", "final_notes.es.html"):
            (self.session_dir / name).write_text(f"內容 {name}", encoding="utf-8")
        with TestClient(app.app) as client:
            response = client.get(f"/bundle/{self.SESSION}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            # Spelled out rather than compared against BUNDLE_FILES: the constant defines the
            # zip, so checking one against the other passes even when a file goes missing.
            self.assertEqual(sorted(bundle.namelist()), sorted([
                "lecture.wav", "transcript.txt", "live_notes.md", "session.json",
                "final_notes.md", "final_notes.html",
                "final_notes.en.md", "final_notes.en.html",
                "final_notes.de.md", "final_notes.de.html",
                "final_notes.pl.md", "final_notes.pl.html",
                "final_notes.es.md", "final_notes.es.html"]))
            self.assertEqual(bundle.read("transcript.txt").decode("utf-8"), "內容 transcript.txt")
        # The download is named after the lecture, not the opaque session id.
        self.assertIn("20260908", response.headers["content-disposition"])

    def test_bundle_skips_files_the_lecture_never_wrote(self):
        self.write_session()
        with TestClient(app.app) as client:
            response = client.get(f"/bundle/{self.SESSION}")
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            self.assertEqual(sorted(bundle.namelist()), ["final_notes.md", "session.json"])

    def test_bundle_filename_survives_an_awkward_course_title(self):
        for title, expected in (
            ("機器學習", "機器學習_20260908_090321.zip"),
            ("A/B: 統計*方法?", "AB 統計方法_20260908_090321.zip"),
            ("兩行\n標題", "兩行 標題_20260908_090321.zip"),
            ("", "lecture_20260908_090321.zip"),
            ("///", "lecture_20260908_090321.zip"),
        ):
            with self.subTest(title=title):
                (self.session_dir / "session.json").write_text(
                    json.dumps({"course_title": title}, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(app.bundle_filename(self.SESSION), expected)

    def test_bundle_filename_falls_back_when_metadata_is_unreadable(self):
        self.assertEqual(app.bundle_filename(self.SESSION), "lecture_20260908_090321.zip")

    def test_bundle_rejects_malformed_and_unknown_sessions(self):
        with TestClient(app.app) as client:
            self.assertEqual(client.get("/bundle/not-a-session").status_code, 404)
            self.assertEqual(client.get("/bundle/20260101_000000").status_code, 404)

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


class Translations(unittest.TestCase):
    """The finished notes get Polish and German copies, for groupmates who read neither Chinese."""

    NOTES = "# 機器學習\n\n- 類別變項 categorical variables\n- 待確認：老師說的那個年份"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.paths = app.session_paths("20260922_101500")
        patcher = patch.object(app, "OUTPUT_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.paths = app.session_paths("20260922_101500")
        self.paths["dir"].mkdir(parents=True, exist_ok=True)

    @contextmanager
    def anthropic(self, by_language):
        """Answer each translation request with the text this test wants for that language."""
        prompts = []
        real_client = httpx.AsyncClient

        def respond(request):
            prompt = json.loads(request.content)["messages"][0]["content"]
            prompts.append(prompt)
            lang = next(k for k, name in app.TRANSLATION_LANGUAGES.items()
                        if name == translation_target(prompt))
            # Languages the test says nothing about still answer, so each test only has to
            # describe the behaviour it is actually about.
            outcome = by_language.get(lang, f"# Notes in {lang}")
            if isinstance(outcome, Exception):
                raise outcome
            return stream_response([outcome])

        with patch.object(app.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
                transport=httpx.MockTransport(respond), **kw)), \
                patch.object(app, "LIVE_RETRY_DELAYS", (0, 0, 0)):
            yield prompts

    def test_every_language_the_group_reads_is_produced(self):
        self.assertEqual(list(app.TRANSLATION_LANGUAGES), ["en", "de", "pl", "es"])
        self.assertEqual(app.TRANSLATION_LANGUAGES["es"], "Latin American Spanish")
        # The file suffix stays short; the document still declares the real BCP-47 tag.
        with self.anthropic({lang: f"# Notes {lang}" for lang in app.TRANSLATION_LANGUAGES}):
            written = asyncio.run(app.write_translations(self.paths, "機器學習", self.NOTES))

        self.assertEqual(written, ["en", "de", "pl", "es"])
        self.assertIn('lang="es-419"',
                      (self.paths["dir"] / "final_notes.es.html").read_text(encoding="utf-8"))
        self.assertIn('lang="pl"',
                      (self.paths["dir"] / "final_notes.pl.html").read_text(encoding="utf-8"))

    def test_finished_notes_get_one_copy_per_language(self):
        with self.anthropic({"pl": "# Uczenie maszynowe\n\n- zmienne kategorialne",
                             "de": "# Maschinelles Lernen\n\n- kategoriale Variablen"}):
            written = asyncio.run(app.write_translations(self.paths, "機器學習", self.NOTES))

        self.assertEqual(written, list(app.TRANSLATION_LANGUAGES))
        polish = (self.paths["dir"] / "final_notes.pl.md").read_text(encoding="utf-8")
        german = (self.paths["dir"] / "final_notes.de.md").read_text(encoding="utf-8")
        self.assertIn("Uczenie maszynowe", polish)
        self.assertIn("Maschinelles Lernen", german)
        self.assertIn("<h1>Uczenie maszynowe</h1>",
                      (self.paths["dir"] / "final_notes.pl.html").read_text(encoding="utf-8"))
        self.assertIn('lang="de"',
                      (self.paths["dir"] / "final_notes.de.html").read_text(encoding="utf-8"))

    def test_a_translation_that_cannot_be_written_does_not_sink_the_session(self):
        """Disk trouble on one copy must not cost the other language, or the whole wrap-up."""
        real_write = Path.write_text

        def refuse_polish(self, *a, **kw):
            if self.name.startswith("final_notes.pl"):
                raise OSError("disk full")
            return real_write(self, *a, **kw)

        with self.anthropic({"pl": "# Uczenie maszynowe", "de": "# Maschinelles Lernen"}), \
                patch.object(Path, "write_text", refuse_polish):
            written = asyncio.run(app.write_translations(self.paths, "機器學習", self.NOTES))

        self.assertEqual(written, [l for l in app.TRANSLATION_LANGUAGES if l != "pl"])
        self.assertIn("Maschinelles Lernen",
                      (self.paths["dir"] / "final_notes.de.md").read_text(encoding="utf-8"))

    def test_a_failed_translation_removes_the_copy_it_could_not_refresh(self):
        """A stale translation of notes that no longer exist is worse than no translation."""
        (self.paths["dir"] / "final_notes.pl.md").write_text("# Stare\n", encoding="utf-8")
        (self.paths["dir"] / "final_notes.pl.html").write_text("<p>Stare</p>", encoding="utf-8")

        with self.anthropic({"pl": httpx.ConnectError("no route"), "de": "# Maschinelles Lernen"}):
            asyncio.run(app.write_translations(self.paths, "機器學習", self.NOTES))

        self.assertFalse((self.paths["dir"] / "final_notes.pl.md").exists())
        self.assertFalse((self.paths["dir"] / "final_notes.pl.html").exists())

    def test_a_copy_that_cannot_be_deleted_does_not_sink_the_session(self):
        """unlink raises more than FileNotFoundError; the cleanup needs isolating too."""
        (self.paths["dir"] / "final_notes.pl.md").write_text("# Stare\n", encoding="utf-8")
        real_unlink = Path.unlink

        def refuse(self, **kw):
            if self.name.startswith("final_notes.pl"):
                raise PermissionError("read-only")
            return real_unlink(self, **kw)

        with self.anthropic({"pl": httpx.ConnectError("no route"), "de": "# Maschinelles"}), \
                patch.object(Path, "unlink", refuse):
            written = asyncio.run(app.write_translations(self.paths, "機器學習", self.NOTES))

        self.assertEqual(written, [l for l in app.TRANSLATION_LANGUAGES if l != "pl"])

    def test_the_model_is_asked_to_translate_the_whole_notes(self):
        """Without this, dropping {notes} from the prompt leaves every test green."""
        with self.anthropic({"pl": "# Uczenie", "de": "# Maschinelles"}) as prompts:
            asyncio.run(app.write_translations(self.paths, "機器學習", self.NOTES))

        self.assertEqual(len(prompts), len(app.TRANSLATION_LANGUAGES))
        for prompt in prompts:
            self.assertIn(self.NOTES, prompt)

    def test_a_failed_translation_does_not_cost_the_other_language(self):
        with self.anthropic({"pl": httpx.ConnectError("no route"),
                             "de": "# Maschinelles Lernen"}):
            written = asyncio.run(app.write_translations(self.paths, "機器學習", self.NOTES))

        self.assertEqual(written, [l for l in app.TRANSLATION_LANGUAGES if l != "pl"])
        self.assertTrue((self.paths["dir"] / "final_notes.de.md").exists())
        self.assertFalse((self.paths["dir"] / "final_notes.pl.md").exists())


if __name__ == "__main__":
    unittest.main()
