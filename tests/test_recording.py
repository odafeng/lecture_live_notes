"""The recording survives whatever happens to transcription."""

import asyncio
import json
import socket
import tempfile
import time
import unittest
import wave
from unittest.mock import patch

from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

import app
from tests.fakes import FakeGeminiSocket, fake_services

CHUNK = b"\x01\x00" * 1365  # ~85 ms of 16 kHz PCM, the size the browser sends
FRAMES_PER_CHUNK = len(CHUNK) // app.BYTES_PER_SAMPLE


class FakeBrowser:
    """The browser's end of /ws, scripted up front and driven from the test's own event loop.

    Calling the endpoint directly rather than through TestClient lets the test put a deadline on
    it, so a receive loop that hangs fails the test instead of hanging the whole suite.
    """

    def __init__(self, script, on_receive=None):
        self.script = list(script)
        self.on_receive = on_receive
        self.sent = []

    async def accept(self):
        pass

    async def receive_text(self):
        message = await self.receive()
        assert message is not None
        return message["text"]

    async def receive(self):
        message = self.script.pop(0) if self.script else None
        if self.on_receive:
            await self.on_receive(message)
        if message is None:
            await asyncio.Event().wait()  # nothing left to say; the browser just stays open
        return message

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self):
        pass


def meta():
    return {"type": "websocket.receive", "text": json.dumps({"type": "meta", "course_title": "錄音"})}


def audio():
    return {"type": "websocket.receive", "bytes": CHUNK}


def stop():
    return {"type": "websocket.receive", "text": json.dumps({"type": "stop"})}


def recorded_frames():
    [path] = list(app.OUTPUT_DIR.glob("*/*/lecture.wav"))
    with wave.open(str(path), "rb") as recording:
        return recording.getnframes()


class RecordingTests(unittest.TestCase):
    def test_lecture_wraps_up_when_asr_gives_up_on_a_full_queue(self):
        """#10: the receive loop was parked in put() on a queue nobody would ever drain again."""

        async def scenario():
            async def refused(*_args, **_kwargs):
                # Only a refusal gives up: Gemini closing on us with a reason. A network error
                # alone keeps retrying for the rest of the class.
                raise ConnectionClosedError(Close(1008, "API key not valid"), None)

            async def hold_stop_until_given_up(message):
                if message == stop():
                    deadline = time.monotonic() + 5
                    while (not any(m["type"] == "error" for m in browser.sent)
                           and time.monotonic() < deadline):
                        await asyncio.sleep(0.01)

            # Three chunks for a queue of two that nobody takes from.
            browser = FakeBrowser([meta(), audio(), audio(), audio(), stop()],
                                  on_receive=hold_stop_until_given_up)
            with patch.object(app.websockets, "connect", refused):
                await asyncio.wait_for(app.websocket_endpoint(browser), timeout=10)
            return browser

        with tempfile.TemporaryDirectory() as output, fake_services(output), \
                patch.object(app, "AUDIO_QUEUE_CHUNKS", 2), \
                patch.object(app, "ASR_RETRY_DELAYS", (0, 0, 0, 0)):
            browser = asyncio.run(scenario())

            kinds = [message["type"] for message in browser.sent]
            gave_up = [m for m in browser.sent if m["type"] == "error"]
            self.assertTrue(gave_up and "連續連線失敗 5 次" in gave_up[0]["text"], kinds)
            self.assertIn("saved", kinds)
            # Every chunk reached the recording, the one the loop was stuck on included.
            self.assertEqual(recorded_frames(), 3 * FRAMES_PER_CHUNK)
            self.assertTrue(list(app.OUTPUT_DIR.glob("*/*/finalize_input.json")))
            # No transcript means nothing to merge (#12), but the audio is there and the page
            # must not read as if the recording were lost.
            [final] = [m for m in browser.sent if m["type"] == "final"]
            self.assertEqual(final["status"], app.FINAL_STATUS_EMPTY)
            self.assertIn("錄音已保存", final["text"])

    def test_transcription_comes_back_after_the_network_does(self):
        """The Mac losing its connection for longer than a few retries ended transcription for
        the rest of the class: offline, every connect fails at once on DNS, so the five attempts
        were spent in about 15 seconds and the worker never tried again."""

        async def scenario():
            attempts = 0

            async def offline_then_back(*_args, **_kwargs):
                nonlocal attempts
                attempts += 1
                if attempts <= 2 * (len(app.ASR_RETRY_DELAYS) + 1):
                    raise socket.gaierror(8, "nodename nor servname provided, or not known")
                return FakeGeminiSocket()

            async def hold_stop_until_transcribed(message):
                if message == stop():
                    deadline = time.monotonic() + 5
                    while (not any(m["type"] == "transcript" for m in browser.sent)
                           and time.monotonic() < deadline):
                        await asyncio.sleep(0.01)

            browser = FakeBrowser([meta(), audio(), audio(), stop()],
                                  on_receive=hold_stop_until_transcribed)
            with patch.object(app.websockets, "connect", offline_then_back):
                await asyncio.wait_for(app.websocket_endpoint(browser), timeout=10)
            return browser

        with tempfile.TemporaryDirectory() as output, fake_services(output), \
                patch.object(app, "ASR_RETRY_DELAYS", (0, 0, 0, 0)):
            browser = asyncio.run(scenario())

            kinds = [message["type"] for message in browser.sent]
            self.assertIn("transcript", kinds)
            self.assertNotIn("error", kinds)
            # One notice per outage, not one per attempt: each lands in the notes panel.
            self.assertEqual(kinds.count("asr_error"), 1, kinds)

    def test_stopping_while_offline_wraps_up_without_waiting_out_the_retries(self):
        """With the network gone for good, the receive loop must keep writing the recording
        past a full queue, and stop must not wait for a reconnect that cannot happen."""

        async def offline(*_args, **_kwargs):
            raise socket.gaierror(8, "nodename nor servname provided, or not known")

        async def scenario():
            browser = FakeBrowser([meta(), *[audio() for _ in range(5)], stop()])
            with patch.object(app.websockets, "connect", offline):
                started = time.monotonic()
                await asyncio.wait_for(app.websocket_endpoint(browser), timeout=10)
                return browser, time.monotonic() - started

        with tempfile.TemporaryDirectory() as output, fake_services(output), \
                patch.object(app, "AUDIO_QUEUE_CHUNKS", 2), \
                patch.object(app, "ASR_RETRY_DELAYS", (5,)):
            browser, took = asyncio.run(scenario())

            self.assertLess(took, 3)
            self.assertEqual(recorded_frames(), 5 * FRAMES_PER_CHUNK)
            self.assertIn("saved", [message["type"] for message in browser.sent])

    def test_an_unclosed_recording_already_says_how_long_it_is(self):
        """#11: a server that stopped before close() left a WAV that could not be read."""

        async def scenario():
            snapshots = []
            chunks_handed_out = 0

            async def watch(message):
                nonlocal chunks_handed_out
                if chunks_handed_out:
                    # The loop asks for the next message only once the last chunk is written, and
                    # close() has not run: the file as a crash at this moment would leave it.
                    try:
                        frames = recorded_frames()
                    except (EOFError, wave.Error) as unreadable:
                        frames = f"unreadable ({type(unreadable).__name__})"
                    snapshots.append((chunks_handed_out, frames))
                if message is not None and "bytes" in message:
                    chunks_handed_out += 1

            browser = FakeBrowser([meta(), audio(), audio(), audio(), stop()], on_receive=watch)
            await asyncio.wait_for(app.websocket_endpoint(browser), timeout=10)
            return snapshots

        with tempfile.TemporaryDirectory() as output, fake_services(output):
            snapshots = asyncio.run(scenario())
            # From the very first chunk, not only once the header has needed a patch.
            self.assertEqual(snapshots, [(n, n * FRAMES_PER_CHUNK) for n in (1, 2, 3)])
            self.assertEqual(recorded_frames(), 3 * FRAMES_PER_CHUNK)

    def test_a_lecture_with_nothing_to_merge_is_not_merged(self):
        """#12: a page that closed before the microphone opened still billed a merge and four
        translations against empty material."""
        browser = FakeBrowser([meta(), {"type": "websocket.disconnect"}])
        with tempfile.TemporaryDirectory() as output, fake_services(output) as requests, \
                patch.object(app, "spawn_background_finalize") as retry:
            asyncio.run(asyncio.wait_for(app.websocket_endpoint(browser), timeout=10))

            self.assertEqual(requests, [])
            retry.assert_not_called()
            [session_dir] = [p.parent for p in app.OUTPUT_DIR.glob("*/*/session.json")]
            meta_saved = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(meta_saved["final_notes_status"], app.FINAL_STATUS_EMPTY)
            self.assertFalse(list(session_dir.glob("final_notes*")))

if __name__ == "__main__":
    unittest.main()
