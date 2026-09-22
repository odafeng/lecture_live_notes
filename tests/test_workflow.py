import io
import json
import tempfile
import unittest
import wave
import zipfile
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


def merge_prompt(requests):
    """The final merge, named rather than indexed: translation calls follow it."""
    return next(p for p in reversed(requests) if "完整上課筆記" in p)


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
            # 1 note + 1 chapter + 1 merge (after 1 retry), then one call per language.
            self.assertEqual(len(requests), 4 + len(app.TRANSLATION_LANGUAGES))
            self.assertIn(TRADITIONAL_TRANSCRIPT, requests[0])
            self.assertIn("章節摘要", merge_prompt(requests))

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

    def test_handwritten_notes_and_corrections_reach_the_final_notes(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output) as requests, \
                TestClient(app.app) as client:
            messages = []
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "meta", "course_title": "机器学习"})
                receive_until(ws, "ready", messages)
                # Corrected before the first block exists, so the correction shapes it.
                ws.send_json({"type": "user_note", "kind": "correction", "text": "老师说的是 ResNet"})
                correction = receive_until(ws, "user_note", messages)
                ws.send_bytes(b"\x00\x00" * 1600)
                receive_until(ws, "note_block", messages)
                ws.send_json({"type": "user_note", "kind": "note", "text": "这页会考"})
                note = receive_until(ws, "user_note", messages)
                ws.send_json({"type": "stop"})
                final = receive_until(ws, "final", messages)
                saved = receive_until(ws, "saved", messages)

            self.assertEqual(correction["kind"], "correction")
            self.assertIn("老師說的是 ResNet", correction["text"])
            self.assertEqual(note["kind"], "note")
            self.assertIn("這頁會考", note["text"])

            note_prompt, final_prompt = requests[0], merge_prompt(requests)
            self.assertIn("老師說的是 ResNet", note_prompt)
            self.assertIn("使用者更正", note_prompt)
            self.assertIn("這頁會考", final_prompt)
            self.assertIn("老師說的是 ResNet", final_prompt)

            # The handwritten text is appended by the server, so the model cannot paraphrase it away.
            self.assertIn(app.USER_NOTES_HEADING, final["text"])
            self.assertIn("這頁會考", final["text"])
            self.assertNotIn("老師說的是 ResNet", final["text"].split(app.USER_NOTES_HEADING)[1])

            files = {name: client.get(url) for name, url in saved["files"].items()}
            live_notes = files["live_notes"].text
            self.assertIn("✍️ [00:00:00] 這頁會考", live_notes)
            self.assertIn("⟲ 更正 [00:00:00] 老師說的是 ResNet", live_notes)
            self.assertIn(app.USER_NOTES_HEADING, files["final_notes"].text)
            self.assertIn("<h1>機器學習</h1>", files["final_html"].text)

            metadata = files["metadata"].json()
            self.assertEqual(metadata["user_note_count"], 1)
            self.assertEqual(metadata["correction_count"], 1)
            date, clock = metadata["session_id"].split("_")
            material = json.loads(
                (Path(output) / date / clock / "finalize_input.json").read_text("utf-8"))
            self.assertEqual(material["user_notes"], ["[00:00:00] 這頁會考"])
            self.assertEqual(material["corrections"], ["[00:00:00] 老師說的是 ResNet"])

    def test_empty_and_oversized_user_input_is_handled_without_polluting_the_notes(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output), \
                TestClient(app.app) as client:
            messages = []
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "meta", "course_title": "机器学习"})
                receive_until(ws, "ready", messages)
                ws.send_json({"type": "user_note", "text": "   "})
                ws.send_json({"type": "user_note", "text": "字" * (app.USER_NOTE_MAX_CHARS + 50)})
                note = receive_until(ws, "user_note", messages)
                ws.send_json({"type": "stop"})
                saved = receive_until(ws, "saved", messages)

            # The blank one is dropped outright; the long one is cut, not rejected.
            self.assertEqual(note["text"].count("字"), app.USER_NOTE_MAX_CHARS)
            metadata = client.get(saved["files"]["metadata"]).json()
            self.assertEqual(metadata["user_note_count"], 1)

    def test_groupmates_get_the_notes_in_every_language_the_group_reads(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output), \
                TestClient(app.app) as client:
            messages = []
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "meta", "course_title": "机器学习"})
                receive_until(ws, "ready", messages)
                ws.send_bytes(b"\x00\x00" * 1600)
                receive_until(ws, "transcript", messages)
                ws.send_json({"type": "stop"})
                saved = receive_until(ws, "saved", messages)

            for key, language in (("final_en", "English"), ("final_de", "German"),
                                  ("final_pl", "Polish"), ("final_es", "Latin American Spanish")):
                copy = client.get(saved["files"][key])
                self.assertEqual(copy.status_code, 200)
                self.assertIn(f"Notatki ({language})", copy.text)
            # The Chinese original is untouched by the translation step.
            self.assertIn("機器學習", client.get(saved["files"]["final_notes"]).text)

    def test_a_failed_merge_is_not_translated(self):
        """Translating the fallback stub costs money and gives groupmates nothing to read."""
        with tempfile.TemporaryDirectory() as output, fake_services(output) as requests, \
                TestClient(app.app) as client:
            requests.always_fail = True
            messages = []
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "meta", "course_title": "机器学习"})
                receive_until(ws, "ready", messages)
                ws.send_bytes(b"\x00\x00" * 1600)
                receive_until(ws, "transcript", messages)
                ws.send_json({"type": "stop"})
                saved = receive_until(ws, "saved", messages)

            self.assertNotIn("final_pl", saved["files"])
            self.assertFalse(any(p.startswith("Translate the lecture notes") for p in requests))

    def test_bundle_download_is_offered_once_the_lecture_is_saved(self):
        with tempfile.TemporaryDirectory() as output, fake_services(output), \
                TestClient(app.app) as client:
            messages = []
            with client.websocket_connect("/ws") as ws:
                ws.send_json({"type": "meta", "course_title": "机器学习"})
                receive_until(ws, "ready", messages)
                ws.send_bytes(b"\x00\x00" * 1600)
                receive_until(ws, "transcript", messages)
                ws.send_json({"type": "stop"})
                saved = receive_until(ws, "saved", messages)

            response = client.get(saved["files"]["bundle"])
            self.assertEqual(response.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
                self.assertEqual(sorted(bundle.namelist()), sorted(app.BUNDLE_FILES))
                self.assertIn("機器學習", bundle.read("final_notes.html").decode("utf-8"))
                # Groupmates get one file, not a folder of links to chase. Each translated file
                # is named here, so dropping one from BUNDLE_FILES cannot go unnoticed.
                for suffix, language in (("en", "English"), ("de", "German"),
                                         ("pl", "Polish"), ("es", "Latin American Spanish")):
                    self.assertIn(f"Notatki ({language})",
                                  bundle.read(f"final_notes.{suffix}.md").decode("utf-8"))
                self.assertIn('lang="pl"',
                              bundle.read("final_notes.pl.html").decode("utf-8"))
                # Latin American Spanish files stay short; the document carries the full tag.
                self.assertIn('lang="es-419"',
                              bundle.read("final_notes.es.html").decode("utf-8"))

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
            date, clock = metadata["session_id"].split("_")
            material = json.loads(
                (Path(output) / date / clock / "finalize_input.json").read_text("utf-8"))
            self.assertEqual(material["course_title"], "機器學習")
            self.assertIn(TRADITIONAL_TRANSCRIPT, material["remaining"])
            self.assertEqual(client.get("/sessions/incomplete").json()["sessions"][0]["session_id"],
                             metadata["session_id"])


if __name__ == "__main__":
    unittest.main()
