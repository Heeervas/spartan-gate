from __future__ import annotations

import json
import logging
import os
import signal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from residency import ResidentSynthesizer, idle_seconds
from runtime import LANGUAGES, selected_profile, validate_active_cache


HOST = "0.0.0.0"
PORT = int(os.environ.get("POCKET_TTS_PORT", "8000"))
MAX_REQUEST_BYTES = 65536
MAX_TEXT_LENGTH = int(os.environ.get("POCKET_TTS_MAX_TEXT_LENGTH", "5000"))
CACHE_ROOT = Path(os.environ.get("POCKET_TTS_CACHE_ROOT", "/models"))

logging.basicConfig(
    level=os.environ.get("POCKET_TTS_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("spartan-pocket-tts")

synthesizer: ResidentSynthesizer | None = None
startup_error: str | None = None


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _validated_payload(body: bytes) -> tuple[str, object]:
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"text exceeds {MAX_TEXT_LENGTH} characters")
    return text, payload.get("language", "auto")


class PocketTTSHandler(BaseHTTPRequestHandler):
    server_version = "SpartanPocketTTS/1"

    def log_message(self, message: str, *args) -> None:
        logger.info("%s - %s", self.client_address[0], message % args)

    def _respond_json(self, status: HTTPStatus, payload: dict) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._respond_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if startup_error is not None or synthesizer is None:
            self._respond_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"status": "unhealthy", "error": startup_error or "not initialized"},
            )
            return
        self._respond_json(
            HTTPStatus.OK,
            {
                "status": "healthy",
                "languages": list(LANGUAGES),
                "resident_languages": synthesizer.loaded_languages,
                "idle_seconds": synthesizer.idle_timeout,
                "last_worker_error": synthesizer.last_error,
                "voices": synthesizer.voices,
                "models": synthesizer.profile.models,
                "offline": True,
            },
        )

    def do_POST(self) -> None:
        if self.path != "/v1/tts":
            self._respond_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if startup_error is not None or synthesizer is None:
            self._respond_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": startup_error or "Pocket TTS is not initialized"},
            )
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._respond_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"request must be between 1 and {MAX_REQUEST_BYTES} bytes"},
            )
            return
        if self.headers.get_content_type() != "application/json":
            self._respond_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "Content-Type must be application/json"},
            )
            return

        try:
            text, language = _validated_payload(self.rfile.read(length))
            selected_language, audio = synthesizer.synthesize(text, language)
        except json.JSONDecodeError:
            self._respond_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return
        except ValueError as exc:
            self._respond_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except RuntimeError as exc:
            status = (
                HTTPStatus.TOO_MANY_REQUESTS
                if "busy" in str(exc).lower()
                else HTTPStatus.INTERNAL_SERVER_ERROR
            )
            self._respond_json(status, {"error": str(exc)})
            return
        except Exception:
            logger.exception("Pocket TTS synthesis failed")
            self._respond_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "Pocket TTS synthesis failed"},
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("X-Pocket-TTS-Language", selected_language)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(audio)


def initialize() -> None:
    global synthesizer, startup_error
    try:
        profile = selected_profile()
        validate_active_cache(CACHE_ROOT, profile)
        synthesizer = ResidentSynthesizer(profile, CACHE_ROOT, idle_seconds())
        startup_error = None
        logger.info("Pocket TTS listener ready; models wake on request, idle timeout=%ss",
                    synthesizer.idle_timeout)
    except Exception as exc:
        startup_error = str(exc)
        logger.exception("Pocket TTS initialization failed")


class PocketTTSServer(ThreadingHTTPServer):
    daemon_threads = True

    def service_actions(self) -> None:
        if synthesizer is not None:
            synthesizer.reap_idle()


def _stop_on_signal(signum, frame) -> None:
    raise SystemExit(0)


def main() -> None:
    initialize()
    signal.signal(signal.SIGTERM, _stop_on_signal)
    signal.signal(signal.SIGINT, _stop_on_signal)
    server = PocketTTSServer((HOST, PORT), PocketTTSHandler)
    logger.info("Pocket TTS listening on %s:%d", HOST, PORT)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if synthesizer is not None:
            synthesizer.close()


if __name__ == "__main__":
    main()
