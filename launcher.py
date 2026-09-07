"""Start the local classroom from the macOS app, without a Terminal window."""

import errno
import fcntl
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / ".runtime"
HTTP = build_opener(ProxyHandler({}))


def server_ready(port):
    try:
        with HTTP.open(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
            health = json.load(response)
        return (isinstance(health, dict) and health.get("ok") is True
                and health.get("app_id") == "lecture-live-notes")
    except (OSError, URLError, ValueError):
        return False


def ensure_server(project_dir=BASE_DIR, runtime_dir=RUNTIME_DIR, preferred_port=8000):
    runtime_dir.mkdir(parents=True, exist_ok=True)
    state_path = runtime_dir / "server.json"
    log_path = runtime_dir / "server.log"
    with (runtime_dir / "launch.lock").open("a") as lock:
        # Serialize simultaneous double-clicks, including the server startup wait.
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(state_path.read_text())
            port = state["port"]
            if isinstance(port, int) and 0 < port < 65536 and server_ready(port):
                return f"http://127.0.0.1:{port}"
        except (OSError, ValueError, KeyError, TypeError):
            pass

        python = project_dir / ".venv" / "bin" / "python"
        if not python.is_file():
            raise RuntimeError("找不到專案的 Python 環境。請保留 Lecture.app 與 .venv 在原本的專案資料夾內。")

        with socket.socket() as listener, log_path.open("a") as log:
            try:
                listener.bind(("127.0.0.1", preferred_port))
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
                listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            process = subprocess.Popen(
                [str(python), "-m", "uvicorn", "app:app", "--fd", str(listener.fileno()),
                 "--host", "127.0.0.1"],
                cwd=project_dir, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True, pass_fds=(listener.fileno(),),
            )

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"課堂服務啟動失敗。詳細記錄位於：\n{log_path}")
            if server_ready(port):
                state_path.write_text(json.dumps({"port": port, "pid": process.pid}))
                return f"http://127.0.0.1:{port}"
            time.sleep(0.2)

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise RuntimeError(f"課堂服務啟動逾時，請再開啟一次。詳細記錄位於：\n{log_path}")


def main():
    try:
        url = ensure_server()
        subprocess.run(["/usr/bin/open", url], check=True)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
