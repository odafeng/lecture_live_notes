import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import uvicorn
from playwright.sync_api import expect, sync_playwright

import app
from tests.fakes import TRADITIONAL_TRANSCRIPT, fake_services


@contextmanager
def running_server():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app.app, log_level="error", loop="asyncio"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Test server did not start")
                time.sleep(0.05)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)


@contextmanager
def browser_page(url, width=1440, setup_script=""):
    with sync_playwright() as playwright:
        with playwright.chromium.launch(
            headless=True, channel=os.getenv("PLAYWRIGHT_CHANNEL") or None,
            args=["--autoplay-policy=no-user-gesture-required"],
        ) as browser:
            page = browser.new_page(viewport={"width": width, "height": 1000}, permissions=["microphone"])
            page.add_init_script("""
                navigator.mediaDevices.getUserMedia = async () => {
                    const context = new AudioContext({sampleRate: 48000});
                    const oscillator = context.createOscillator();
                    const destination = context.createMediaStreamDestination();
                    oscillator.connect(destination);
                    oscillator.start();
                    await context.resume();
                    window.testInputStream = destination.stream;
                    return destination.stream;
                };
            """ + setup_script)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            yield page
            assert not errors, errors


class BrowserTests(unittest.TestCase):
    def test_record_render_mark_stop_and_download_on_desktop_and_mobile(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output, failures=1, final_delay=1.5), \
                patch.object(app, "ROLLUP_EVERY_BLOCKS", 1), running_server() as url:
            for width in (1440, 390):
                with self.subTest(width=width), browser_page(url, width) as page:
                    if width < 960:
                        page.locator("#courseSettings summary").click()
                    page.locator("#courseTitle").fill("机器学习")
                    page.get_by_role("button", name="開始上課", exact=True).click()
                    expect(page.locator("body")).to_have_attribute("data-phase", "recording")
                    expect(page.locator("#transcript")).to_contain_text(TRADITIONAL_TRANSCRIPT)
                    expect(page.locator("#transcriptEmpty")).to_be_hidden()
                    expect(page.locator("#transcriptCount")).to_have_text("1 段")
                    if width < 960:
                        page.locator("#notesViewBtn").click()
                        expect(page.locator("#transcriptPanel")).to_be_hidden()
                        expect(page.locator("#notesPanel")).to_be_visible()
                    expect(page.locator("#notes strong")).to_have_text("主題：", timeout=15000)
                    expect(page.locator("#notes li")).to_have_text("類別變項 categorical variables")
                    expect(page.locator("#notes h2")).to_have_css("position", "static")
                    expect(page.locator("#notes")).to_contain_text("已完成背景章節整併")
                    expect(page.locator("#noteCount")).to_have_text("1 段筆記")
                    page.get_by_role("button", name="標記重點", exact=True).click()
                    expect(page.locator("#notes")).to_contain_text("使用者標記重點")
                    expect(page.locator("#notes strong")).to_have_count(1)

                    page.get_by_role("button", name="下課／停止").click()
                    expect(page.locator("body")).to_have_attribute("data-phase", "finalizing")
                    expect(page.locator("#startBtn")).to_be_disabled()
                    expect(page.locator("#markBtn")).to_be_disabled()
                    page.evaluate("() => { startRecording(); }")
                    expect(page.locator("#transcriptCount")).to_have_text("1 段")
                    expect(page.locator("#finalPanel")).to_be_visible(timeout=15000)
                    expect(page.locator("body")).to_have_attribute("data-phase", "complete")
                    expect(page.locator("#startBtn")).to_be_enabled()
                    expect(page.locator("#final h1")).to_have_text("機器學習")
                    expect(page.locator("#final strong")).to_have_text("重點")
                    expect(page.locator("#final table")).to_have_count(1)
                    expect(page.locator("#downloads a")).to_have_count(5)
                    expect(page.locator("#final img, #final script, #final a[href^='javascript:']")).to_have_count(0)
                    self.assertIsNone(page.evaluate("window.injected"))
                    self.assertTrue(page.evaluate("window.testInputStream.getTracks().every(t => t.readyState === 'ended')"))
                    with page.expect_download() as downloaded:
                        page.get_by_role("link", name="完整筆記", exact=True).click()
                    content = Path(downloaded.value.path()).read_text(encoding="utf-8")
                    self.assertIn("# 機器學習", content)
                    self.assertIn("**重點**", content)
                    self.assertNotIn("<h1>", content)
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)

    def test_failed_merge_can_be_rerun_from_the_finished_lecture(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output) as requests, \
                running_server() as url, browser_page(url) as page:
            requests.always_fail = True
            page.locator("#courseTitle").fill("机器学习")
            page.get_by_role("button", name="開始上課", exact=True).click()
            expect(page.locator("body")).to_have_attribute("data-phase", "recording")
            expect(page.locator("#transcript")).to_contain_text(TRADITIONAL_TRANSCRIPT)
            page.get_by_role("button", name="下課／停止").click()

            expect(page.locator("#finalPanel")).to_be_visible(timeout=15000)
            expect(page.locator("#final")).to_contain_text("最終整併失敗")
            expect(page.locator("#notice")).to_contain_text("完整筆記整併失敗")
            expect(page.locator("#remergeBtn")).to_be_visible()

            requests.always_fail = False
            page.locator("#remergeBtn").click()
            expect(page.locator("#final h1")).to_have_text("機器學習", timeout=15000)
            expect(page.locator("#final")).not_to_contain_text("最終整併失敗")
            expect(page.locator("#notice")).to_be_hidden()

    def test_unfinished_lecture_is_offered_for_merging_on_the_next_launch(self):
        with tempfile.TemporaryDirectory() as output:
            session = Path(output) / "20260908_090321"
            session.mkdir()
            (session / "session.json").write_text(json.dumps({
                "session_id": "20260908_090321", "course_title": "機器學習",
                "started_at": "20260908_090321", "final_notes_status": "failed",
            }, ensure_ascii=False), encoding="utf-8")
            (session / "finalize_input.json").write_text(json.dumps({
                "course_title": "機器學習", "chapters": "## 章節摘要", "remaining": "### 尚未整併",
            }, ensure_ascii=False), encoding="utf-8")
            (session / "final_notes.md").write_text(
                "# 機器學習\n\n最終整併失敗：RuntimeError: boom\n", encoding="utf-8")

            with fake_services(output), running_server() as url, browser_page(url) as page:
                expect(page.locator("#recovery")).to_be_visible()
                expect(page.locator("#recovery")).to_contain_text("機器學習")
                expect(page.locator("#recovery")).to_contain_text("9月8日 09:03")
                page.locator("#recoveryBtn").click()
                expect(page.locator("#finalPanel")).to_be_visible(timeout=15000)
                expect(page.locator("#final h1")).to_have_text("機器學習")
                expect(page.locator("#recovery")).to_be_hidden()
            self.assertNotIn("最終整併失敗",
                             (session / "final_notes.md").read_text(encoding="utf-8"))

    def test_settings_and_reading_controls_at_different_widths(self):
        with running_server() as url:
            for width in (320, 390, 768, 1024, 1440):
                with self.subTest(width=width), browser_page(url, width) as page:
                    page.keyboard.press("Tab")
                    expect(page.locator(".skip-link")).to_be_focused()
                    page.keyboard.press("Enter")
                    expect(page.locator("#main")).to_be_focused()
                    if width <= 960:
                        self.assertFalse(page.locator("#courseSettings").evaluate("e => e.open"))
                        page.locator("#courseSettings summary").click()
                    page.locator("#courseTitle").fill("MachineLearning" * 12)
                    expect(page.locator("#workspaceTitle")).to_have_text("MachineLearning" * 12)
                    page.locator("#transcriptionMode").select_option("VERBATIM")
                    expect(page.locator("#modeHelp")).to_have_text("盡量保留原始措辭與口頭語句。")
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    if width <= 960:
                        page.locator("#notesViewBtn").click()
                        expect(page.locator("#notesViewBtn")).to_have_attribute("aria-pressed", "true")
                        expect(page.locator("#notesPanel")).to_be_visible()
                        expect(page.locator("#transcriptPanel")).to_be_hidden()
                    page.locator("#followBtn").click()
                    expect(page.locator("#followBtn")).to_have_attribute("aria-pressed", "false")

    def test_connecting_prevents_duplicate_sessions_and_recovers_from_error(self):
        script = """
            window.socketCount = 0;
            window.WebSocket = class {
                static OPEN = 1;
                constructor() { this.readyState = 0; window.socketCount++; window.testSocket = this; }
                close() { this.readyState = 3; this.onclose?.({}); }
                send() {}
            };
        """
        with running_server() as url, browser_page(url, setup_script=script) as page:
            page.locator("#startBtn").click()
            expect(page.locator("body")).to_have_attribute("data-phase", "connecting")
            expect(page.locator("#startBtn")).to_be_disabled()
            expect(page.locator("#courseTitle")).to_be_disabled()
            page.evaluate("() => { startRecording(); }")
            self.assertEqual(page.evaluate("window.socketCount"), 1)
            page.evaluate("window.testSocket.onerror()")
            expect(page.locator("body")).to_have_attribute("data-phase", "error")
            expect(page.locator("#notice")).to_contain_text("無法連上伺服器")
            expect(page.locator("#startBtn")).to_be_enabled()
            expect(page.locator("#courseTitle")).to_be_enabled()

    def test_microphone_denial_restores_controls(self):
        script = 'navigator.mediaDevices.getUserMedia = async () => { throw new DOMException("denied", "NotAllowedError"); };'
        with tempfile.TemporaryDirectory() as output, fake_services(output), running_server() as url, \
                browser_page(url, setup_script=script) as page:
            page.locator("#startBtn").click()
            expect(page.locator("#notice")).to_contain_text("麥克風權限未開啟")
            expect(page.locator("body")).to_have_attribute("data-phase", "error")
            expect(page.locator("#startBtn")).to_be_enabled()
            expect(page.locator("#markBtn")).to_be_disabled()
            expect(page.locator("#audioMeter")).to_have_attribute("aria-valuenow", "0")

    def test_disconnect_stops_microphone_and_resets_controls(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output), running_server() as url, \
                browser_page(url) as page:
            page.locator("#startBtn").click()
            expect(page.locator("body")).to_have_attribute("data-phase", "recording")
            page.evaluate("ws.close()")
            expect(page.locator("body")).to_have_attribute("data-phase", "error")
            expect(page.locator("#notice")).to_contain_text("連線已中斷")
            expect(page.locator("#startBtn")).to_be_enabled()
            expect(page.locator("#markBtn")).to_be_disabled()
            expect(page.locator("#audioMeter")).to_have_attribute("aria-valuenow", "0")
            self.assertTrue(page.evaluate("window.testInputStream.getTracks().every(t => t.readyState === 'ended')"))

    def test_follow_can_pause_scrolling_without_losing_new_text(self):
        with running_server() as url, browser_page(url) as page:
            page.evaluate("""() => {
                for (let i = 0; i < 30; i++) appendTranscript('[00:00:01] 課堂內容 ' + i);
            }""")
            self.assertGreater(page.locator("#transcriptScroll").evaluate("e => e.scrollTop"), 0)
            page.locator("#followBtn").click()
            page.locator("#transcriptScroll").evaluate("e => e.scrollTop = 0")
            page.evaluate("appendTranscript('[00:00:02] 新的課堂內容')")
            self.assertEqual(page.locator("#transcriptScroll").evaluate("e => e.scrollTop"), 0)
            expect(page.locator("#transcript")).to_contain_text("新的課堂內容")
            page.locator("#followBtn").click()
            self.assertGreater(page.locator("#transcriptScroll").evaluate("e => e.scrollTop"), 0)


if __name__ == "__main__":
    unittest.main()
