import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import uvicorn
from playwright.sync_api import expect, sync_playwright

import app
from tests.fakes import TRADITIONAL_TRANSCRIPT, fake_services


@contextmanager
def running_server(asgi_app=None):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(asgi_app or app.app, log_level="error", loop="asyncio"))
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
                    # 7 originals, plus a Markdown and an HTML copy per translated language.
                    expect(page.locator("#downloads a")).to_have_count(15)
                    for name in ("English", "Deutsch", "Polski", "Español"):
                        expect(page.get_by_role("link", name=f"完整筆記 {name}",
                                                exact=True)).to_be_visible()
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

    def test_writing_a_note_and_a_correction_during_class(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output), running_server() as url, \
                browser_page(url) as page:
            page.locator("#courseTitle").fill("机器学习")
            expect(page.locator("#noteInput")).to_be_disabled()
            page.get_by_role("button", name="開始上課", exact=True).click()
            expect(page.locator("body")).to_have_attribute("data-phase", "recording")

            expect(page.locator("#noteInput")).to_be_enabled()
            page.locator("#noteInput").fill("这页会考")
            page.locator("#noteInput").press("Enter")
            expect(page.locator("#notes")).to_contain_text("✍️")
            expect(page.locator("#notes")).to_contain_text("這頁會考")
            expect(page.locator("#noteInput")).to_have_value("")
            # A handwritten note is not one of the generated blocks, so the count stays put.
            expect(page.locator("#noteCount")).to_have_text("0 段筆記")

            page.locator("#noteKindCorrection").click()
            expect(page.locator("#noteKindCorrection")).to_have_attribute("aria-pressed", "true")
            expect(page.locator("#noteKindNote")).to_have_attribute("aria-pressed", "false")
            page.locator("#noteInput").fill("老师说的是 ResNet")
            page.locator("#noteInput").press("Enter")
            expect(page.locator("#notes")).to_contain_text("⟲ 更正")

            page.get_by_role("button", name="下課／停止").click()
            expect(page.locator("#noteInput")).to_be_disabled()
            expect(page.locator("#finalPanel")).to_be_visible(timeout=15000)
            expect(page.locator("#final")).to_contain_text("我的課堂筆記（原文）")
            expect(page.locator("#final")).to_contain_text("這頁會考")
            expect(page.get_by_role("link", name="整包下載", exact=True)).to_be_visible()
            expect(page.get_by_role("link", name="完整筆記 HTML", exact=True)).to_be_visible()

    def test_blank_note_is_not_sent(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output), running_server() as url, \
                browser_page(url) as page:
            page.get_by_role("button", name="開始上課", exact=True).click()
            expect(page.locator("#noteInput")).to_be_enabled()
            page.locator("#noteInput").fill("   ")
            page.locator("#noteInput").press("Enter")
            expect(page.locator("#notesEmpty")).to_be_visible()
            expect(page.locator("#noteInput")).to_have_value("   ")

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
            session = Path(output) / "20260908" / "090321"
            session.mkdir(parents=True)
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


# Headless pages are always visible; this pretends the user switched apps and back.
SET_VISIBILITY = ("(state => Object.defineProperty(document, 'visibilityState', "
                  "{value: state, configurable: true}))")


class Gateway:
    """Stands in for `tailscale serve`: it keeps answering after the server behind it stops."""

    def __init__(self, upstream):
        self.upstream = upstream
        self.upstream_down = False

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.upstream_down:
            await send({"type": "http.response.start", "status": 502,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"Bad Gateway"})
            return
        await self.upstream(scope, receive, send)


# Records every wake lock request and release; the sentinel mimics WakeLockSentinel.
FAKE_WAKE_LOCK = """
    window.wakeLockLog = [];
    Object.defineProperty(navigator, "wakeLock", {value: {request: async type => {
        const sentinel = new EventTarget();
        sentinel.released = false;
        sentinel.release = async () => {
            if (sentinel.released) return;
            sentinel.released = true;
            wakeLockLog.push("release");
            sentinel.dispatchEvent(new Event("release"));
        };
        wakeLockLog.push("request:" + type);
        window.lastWakeLock = sentinel;
        return sentinel;
    }}});
"""


class PwaTests(unittest.TestCase):
    def setUp(self):
        key = patch.object(app, "ANTHROPIC_API_KEY", "test-key")
        key.start()
        self.addCleanup(key.stop)

    def test_a_dead_server_gets_an_explanation_instead_of_a_browser_error(self):
        with ExitStack() as server:
            url = server.enter_context(running_server())
            with browser_page(url) as page:
                page.wait_for_function("navigator.serviceWorker.controller !== null")
                page.reload()
                expect(page.get_by_role("button", name="開始上課", exact=True)).to_be_visible()

                server.close()
                page.reload()
                expect(page.get_by_role("heading", name="連不上 Mac 上的課堂服務")).to_be_visible()
                expect(page.get_by_role("link", name="重新連線")).to_have_attribute("href", "/")

    def test_a_proxy_answering_for_a_dead_server_gets_the_explanation_too(self):
        gateway = Gateway(app.app)
        with running_server(gateway) as url, browser_page(url) as page:
            page.wait_for_function("navigator.serviceWorker.controller !== null")
            # The app's own errors on a page load still reach the page.
            self.assertEqual(page.goto(url + "/no-such-page").status, 404)
            expect(page.get_by_role("heading", name="連不上 Mac 上的課堂服務")).to_have_count(0)

            gateway.upstream_down = True
            page.goto(url)
            expect(page.get_by_role("heading", name="連不上 Mac 上的課堂服務")).to_be_visible()
            # Requests that are not page loads still see the proxy's own answer.
            self.assertEqual(page.evaluate("fetch('/health', {cache: 'no-store'}).then(r => r.status)"), 502)

    def test_requests_other_than_page_loads_are_never_answered_from_cache(self):
        with ExitStack() as server:
            url = server.enter_context(running_server())
            with browser_page(url) as page:
                page.wait_for_function("navigator.serviceWorker.controller !== null")
                server.close()
                for path in ("/health", "/app.js", "/offline.html", "/download/20260101/000000/transcript.txt"):
                    with self.subTest(path=path):
                        # no-store skips the HTTP cache, so only the service worker could answer.
                        outcome = page.evaluate("""path => fetch(path, {cache: "no-store"})
                            .then(r => 'answered ' + r.status, () => 'failed')""", path)
                        self.assertEqual(outcome, "failed")

    def test_screen_stays_awake_only_while_recording(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output), running_server() as url, \
                browser_page(url, 390, setup_script=FAKE_WAKE_LOCK) as page:
            page.get_by_role("button", name="開始上課", exact=True).click()
            expect(page.locator("body")).to_have_attribute("data-phase", "recording")
            page.wait_for_function("wakeLockLog.length === 1")

            # Android can revoke the lock while the page is still visible: take it straight back.
            page.evaluate("lastWakeLock.release()")
            page.wait_for_function("wakeLockLog.length === 3")

            # The browser drops it when the page is hidden; asking then would only fail.
            page.evaluate(f"{SET_VISIBILITY}('hidden'); lastWakeLock.release()")
            page.wait_for_timeout(200)
            self.assertEqual(page.evaluate("wakeLockLog.length"), 4)
            # Coming back takes it again.
            page.evaluate(f"{SET_VISIBILITY}('visible'); document.dispatchEvent(new Event('visibilitychange'))")
            page.wait_for_function("wakeLockLog.length === 5")

            page.get_by_role("button", name="下課／停止").click()
            page.wait_for_function("wakeLockLog.length === 6")
            self.assertEqual(page.evaluate("wakeLockLog"), ["request:screen", "release"] * 3)
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
            expect(page.locator("body")).not_to_have_attribute("data-phase", "recording")
            self.assertEqual(page.evaluate("wakeLockLog.length"), 6)

    def test_a_wake_lock_lost_to_a_page_going_hidden_is_not_reported(self):
        script = """Object.defineProperty(navigator, "wakeLock", {value: {request: async () => {
            (%s)('hidden');
            throw new DOMException("page hidden", "NotAllowedError");
        }}});""" % SET_VISIBILITY
        with tempfile.TemporaryDirectory() as output, fake_services(output), running_server() as url, \
                browser_page(url, 390, setup_script=script) as page:
            page.get_by_role("button", name="開始上課", exact=True).click()
            expect(page.locator("#transcript")).to_contain_text(TRADITIONAL_TRANSCRIPT)
            expect(page.locator("#notice")).to_be_hidden()

    def test_a_refused_wake_lock_warns_but_keeps_recording(self):
        script = """Object.defineProperty(navigator, "wakeLock", {value: {
            request: async () => { throw new DOMException("low battery", "NotAllowedError"); }}});"""
        with tempfile.TemporaryDirectory() as output, fake_services(output), running_server() as url, \
                browser_page(url, 390, setup_script=script) as page:
            page.get_by_role("button", name="開始上課", exact=True).click()
            expect(page.locator("#notice")).to_contain_text("無法讓螢幕保持亮著")
            expect(page.locator("body")).to_have_attribute("data-phase", "recording")
            expect(page.locator("#transcript")).to_contain_text(TRADITIONAL_TRANSCRIPT)


if __name__ == "__main__":
    unittest.main()
