"""The recording survives whatever happens to transcription."""

import asyncio
import json
import tempfile
import unittest
import wave
from unittest.mock import patch

import app
from tests.fakes import fake_services

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
            third_chunk_taken = asyncio.Event()
            taken = 0

            async def watch(message):
                nonlocal taken
                if message is not None and "bytes" in message:
                    taken += 1
                    if taken == 3:
                        third_chunk_taken.set()

            async def unreachable(*_args, **_kwargs):
                # Hold every attempt until the queue (two chunks) is full and the receive loop
                # is waiting in put() for the third, then fail like a connect that timed out.
                await third_chunk_taken.wait()
                await asyncio.sleep(0.05)
                raise ConnectionError("Gemini Live unreachable")

            browser = FakeBrowser([meta(), audio(), audio(), audio(), stop()], on_receive=watch)
            with patch.object(app.websockets, "connect", unreachable):
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

if __name__ == "__main__":
    unittest.main()
