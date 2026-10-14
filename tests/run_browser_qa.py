"""Portable QA server lifecycle; subprocess output goes to files, not pipe buffers."""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.getenv("STYLEMUSE_QA_OUTPUT", str(ROOT / ".tmp" / "browser-qa")))


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (ROOT / ".tmp").mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONUTF8":"1"}
    with (OUTPUT / "server.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen([sys.executable, "tests/browser_server.py"], cwd=ROOT, env=env,
            stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise RuntimeError("QA server failed; inspect " + str(OUTPUT / "server.log"))
                try:
                    with urllib.request.urlopen("http://127.0.0.1:5367/api/access", timeout=1):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("QA server did not become ready")
                    time.sleep(0.2)
            return subprocess.run([sys.executable, "tests/browser_flow.py"], cwd=ROOT, env=env).returncode
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
