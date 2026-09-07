from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import launcher


class LauncherTests(unittest.TestCase):
    def test_health_rejects_other_services_and_old_gemini_server(self):
        for health in ({"ok": True}, {"ok": True, "app_id": "another-app"},
                       {"ok": True, "transcribe_model": "gemini-3.5-transcribe-live"}, []):
            with self.subTest(health=health), patch.object(launcher.HTTP, "open",
                    return_value=io.BytesIO(json.dumps(health).encode())):
                self.assertFalse(launcher.server_ready(8000))

    def test_missing_environment_gives_readable_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "找不到專案的 Python 環境"):
                launcher.ensure_server(root, root / "runtime", preferred_port=0)

    def test_failed_start_does_not_save_dead_server(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(launcher.subprocess, "Popen", return_value=MagicMock(poll=lambda: 1)):
            runtime = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "課堂服務啟動失敗"):
                launcher.ensure_server(runtime_dir=runtime, preferred_port=0)
            self.assertFalse((runtime / "server.json").exists())

    def test_timeout_cleans_up_only_the_process_it_started(self):
        process = MagicMock()
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(launcher.subprocess, "Popen", return_value=process), \
                patch.object(launcher.time, "monotonic", side_effect=[0, 21]):
            with self.assertRaisesRegex(RuntimeError, "課堂服務啟動逾時"):
                launcher.ensure_server(runtime_dir=Path(temporary), preferred_port=0)
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=5)

    def test_concurrent_launches_use_one_server_and_skip_occupied_port(self):
        children = []
        real_popen = subprocess.Popen

        def start(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with tempfile.TemporaryDirectory() as temporary, socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            occupied_port = occupied.getsockname()[1]
            runtime = Path(temporary)
            (runtime / "server.json").write_text("stale invalid state")
            try:
                with patch.object(launcher.subprocess, "Popen", side_effect=start), \
                        ThreadPoolExecutor(max_workers=2) as workers:
                    calls = [workers.submit(launcher.ensure_server,
                                            runtime_dir=runtime, preferred_port=occupied_port)
                             for _ in range(2)]
                    urls = [call.result(timeout=30) for call in calls]
                self.assertEqual(len(children), 1)
                self.assertEqual(urls[0], urls[1])
                state = json.loads((runtime / "server.json").read_text())
                self.assertNotEqual(state["port"], occupied_port)
                self.assertEqual(state["pid"], children[0].pid)
                with launcher.HTTP.open(urls[0] + "/health") as response:
                    self.assertEqual(json.load(response)["summary_provider"], "anthropic")
                for path, expected in (("/", "課堂筆記工作台"), ("/styles.css", ":root"),
                                       ("/app.js", "startRecording")):
                    with launcher.HTTP.open(urls[0] + path) as response:
                        self.assertIn(expected, response.read().decode())
            finally:
                for child in children:
                    child.terminate()
                    child.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
