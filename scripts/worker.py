"""Optional independent worker process sharing the application's SQLite store."""
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skills.author_style import AuthorStyleSkill
from skills.author_style.jobs import JobService


def main():
    service = JobService(ROOT, AuthorStyleSkill)
    signal.signal(signal.SIGINT, lambda *args: service.stop.set())
    signal.signal(signal.SIGTERM, lambda *args: service.stop.set())
    service.start()
    try:
        while not service.stop.wait(1):
            pass
    finally:
        service.close()


if __name__ == "__main__":
    main()
