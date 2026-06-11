"""
Start the local Flask app and verify basic HTTP endpoints.

Usage:
    python scripts/smoke_check.py
    python scripts/smoke_check.py --port 5050
"""

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def fetch(url: str, timeout: float = 3.0) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body


def wait_for_server(base_url: str, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"server exited early with code {process.returncode}\n"
                f"stdout:\n{process.stdout.read() if process.stdout else ''}\n"
                f"stderr:\n{process.stderr.read() if process.stderr else ''}"
            )
        try:
            status, _ = fetch(base_url, timeout=2.0)
            if status == 200:
                return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"server did not become ready within {timeout}s: {last_error}")


def run_smoke_check(port: int, timeout: float) -> int:
    env = dict(os.environ)
    env["FLASK_HOST"] = "127.0.0.1"
    env["FLASK_PORT"] = str(port)
    env["FLASK_DEBUG"] = "0"
    env.setdefault("PYTHONUTF8", "1")

    process = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_for_server(base_url, process, timeout)

        checks = [
            ("/", "StyleMuse"),
            ("/api/models", '"ok":true'),
            ("/api/authors", '"ok":true'),
        ]
        for path, expected in checks:
            status, body = fetch(base_url + path)
            if status != 200:
                raise RuntimeError(f"{path} returned HTTP {status}")
            if expected not in body.replace(" ", ""):
                raise RuntimeError(f"{path} response did not contain {expected!r}")
            print(f"PASS {path} HTTP {status}")
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a local Flask smoke check.")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    try:
        return run_smoke_check(args.port, args.timeout)
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
