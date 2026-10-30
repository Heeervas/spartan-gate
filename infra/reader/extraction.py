"""One owned, killable offline converter at a time, inside the fetch budget."""

import json
from pathlib import Path
import subprocess
import sys
import threading
import time

MAX_TEXT_LENGTH = 100_000
EXTRACTION_SECONDS = 3
_SLOT = threading.BoundedSemaphore(1)
WORKER = Path(__file__).with_name("extract_worker.py")


class ExtractionError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def extract_markdown(content, url, deadline):
    expires = min(deadline.expires, time.monotonic() + EXTRACTION_SECONDS)
    if not _SLOT.acquire(timeout=max(0, expires - time.monotonic())):
        deadline.remaining()
        raise ExtractionError("reader_overloaded")
    try:
        budget = min(deadline.remaining(), expires - time.monotonic())
        if budget <= 0:
            raise TimeoutError("Extraction deadline exceeded")
        # run() kills and reaps its direct child on TimeoutExpired. The fixed
        # worker never launches descendants and emits only bounded JSON.
        result = subprocess.run(
            [sys.executable, "-I", str(WORKER)],
            input=json.dumps({"html": content, "url": url}).encode(),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=budget, check=False,
        )
        deadline.remaining()
        if time.monotonic() >= expires:
            raise TimeoutError("Extraction deadline exceeded")
        if result.returncode or len(result.stdout) > 1_000_000:
            raise ExtractionError("extraction_failed")
        data = json.loads(result.stdout)
        if data.get("error") in {"needs_render", "extraction_failed"}:
            raise ExtractionError(data["error"])
        if not isinstance(data.get("text"), str) or len(data["text"]) > MAX_TEXT_LENGTH:
            raise ExtractionError("extraction_failed")
        return data["text"], data["method"], bool(data["truncated"])
    except subprocess.TimeoutExpired:
        raise TimeoutError("Extraction deadline exceeded") from None
    finally:
        _SLOT.release()
