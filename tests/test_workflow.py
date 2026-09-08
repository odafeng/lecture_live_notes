import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from tests.fakes import TRADITIONAL_TRANSCRIPT, fake_services


def receive_until(ws, kind, messages):
    for _ in range(30):
        message = ws.receive_json()
        messages.append(message)
        if message["type"] == kind:
            return message
    raise AssertionError(f"Did not receive {kind}")


class WorkflowTests(unittest.TestCase):
    def test_transcription_retry_notes_chapter_stop_and_downloads(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output, failures=1) as requests, \
                patch.object(app, "ROLLUP_EVERY_BLOCKS", 1), TestClient(app.app) as client:
            messages = []
            pcm = b"\x00\x00" * 1600
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "meta", "course_title": "机器学习"})
                receive_until(ws, "ready", messages)
                ws.send_bytes(pcm)
                block = receive_until(ws, "note_block", messages)
                self.assertIn("<strong>主題：</strong>", block["html"])
                self.assertNotIn("摘要失敗", block["text"])
                chapter = receive_until(ws, "chapter", messages)
                self.assertIn("章節摘要", chapter["text"])
                ws.send_json({"type": "mark"})
                receive_until(ws, "marker", messages)
                ws.send_json({"type": "stop"})
                final = receive_until(ws, "final", messages)
                saved = receive_until(ws, "saved", messages)

            # Pings share the uplink with audio frames, so the pong deadline must survive a backlog.
            keepalive = requests.connect.call_args.kwargs
            self.assertGreaterEqual(keepalive["ping_timeout"], 60)
            self.assertLess(keepalive["ping_interval"], keepalive["ping_timeout"])

            interim = next(m for m in messages if m["type"] == "interim" and m["text"])
            transcript = next(m for m in messages if m["type"] == "transcript")
            self.assertEqual(interim["text"], TRADITIONAL_TRANSCRIPT)
            self.assertIn(TRADITIONAL_TRANSCRIPT, transcript["line"])
            self.assertIn("<h1>機器學習</h1>", final["html"])
            self.assertNotIn("<img", final["html"])
            self.assertEqual(len(requests), 4)
            self.assertIn(TRADITIONAL_TRANSCRIPT, requests[0])
            self.assertIn("章節摘要", requests[-1])

            files = {name: client.get(url) for name, url in saved["files"].items()}
            self.assertTrue(all(response.status_code == 200 for response in files.values()))
            self.assertIn(TRADITIONAL_TRANSCRIPT, files["transcript"].text)
            self.assertIn("**主題：** 變項", files["live_notes"].text)
            self.assertIn("使用者標記重點", files["live_notes"].text)
            self.assertTrue(files["final_notes"].text.startswith("# 機器學習\n"))
            self.assertNotIn("<h1>", files["final_notes"].text)
            self.assertEqual(files["metadata"].json()["course_title"], "機器學習")
            self.assertEqual(files["metadata"].json()["summary_provider"], "anthropic")
            self.assertEqual(files["metadata"].json()["summary_model"], "claude-haiku-4-5-20251001")
            with wave.open(io.BytesIO(files["audio"].content)) as recording:
                self.assertEqual(recording.readframes(recording.getnframes()), pcm)

    def test_persistent_503_preserves_traditional_transcript_and_final_fallback(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output, failures=100) as requests, \
                TestClient(app.app) as client:
            messages = []
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "meta", "course_title": "机器学习"})
                receive_until(ws, "ready", messages)
                ws.send_bytes(b"\x00\x00" * 1600)
                receive_until(ws, "transcript", messages)
                ws.send_json({"type": "stop"})
                block = receive_until(ws, "note_block", messages)
                final = receive_until(ws, "final", messages)
                saved = receive_until(ws, "saved", messages)

            # 4 live-note attempts plus 5 for the final merge, which retries harder.
            self.assertEqual(len(requests), 9)
            self.assertEqual(final["status"], app.FINAL_STATUS_FAILED)
            self.assertIn("HTTP 503", block["text"])
            self.assertIn("已嘗試 4 次", block["text"])
            self.assertIn("已嘗試 5 次", final["text"])
            self.assertIn(TRADITIONAL_TRANSCRIPT, block["text"])
            self.assertIn(TRADITIONAL_TRANSCRIPT, final["text"])
            self.assertIn("<h1>機器學習</h1>", final["html"])
            self.assertNotIn("https://", final["text"])
            self.assertIn(TRADITIONAL_TRANSCRIPT, client.get(saved["files"]["transcript"]).text)
            self.assertIn(TRADITIONAL_TRANSCRIPT, client.get(saved["files"]["final_notes"]).text)

            # The material for a later re-merge survives the failure.
            metadata = client.get(saved["files"]["metadata"]).json()
            self.assertEqual(metadata["final_notes_status"], app.FINAL_STATUS_FAILED)
            material = json.loads(
                (Path(output) / metadata["session_id"] / "finalize_input.json").read_text("utf-8"))
            self.assertEqual(material["course_title"], "機器學習")
            self.assertIn(TRADITIONAL_TRANSCRIPT, material["remaining"])
            self.assertEqual(client.get("/sessions/incomplete").json()["sessions"][0]["session_id"],
                             metadata["session_id"])


if __name__ == "__main__":
    unittest.main()
