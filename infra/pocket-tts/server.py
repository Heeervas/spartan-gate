from __future__ import annotations

import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from runtime import (
    BilingualSynthesizer,
    PocketTTSConfigurationError,
    configure_huggingface_cache,
    validate_active_cache,
)


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

synthesizer: BilingualSynthesizer | None = None
startup_error: str | None = None


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


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
                "languages": synthesizer.loaded_languages,
                "voices": synthesizer.voices,
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
            payload = json.loads(self.rfile.read(length))
            text = payload.get("text")
            language = payload.get("language", "auto")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("text must be a non-empty string")
            if len(text) > MAX_TEXT_LENGTH:
                raise ValueError(f"text exceeds {MAX_TEXT_LENGTH} characters")
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
        validate_active_cache(CACHE_ROOT)
        configure_huggingface_cache(CACHE_ROOT / "active", offline=True)
        candidate = BilingualSynthesizer()
        candidate.load_all()
        synthesizer = candidate
        logger.info(
            "Pocket TTS ready: languages=%s voices=%s",
            synthesizer.loaded_languages,
            synthesizer.voices,
        )
    except Exception as exc:
        startup_error = str(exc)
        logger.exception("Pocket TTS initialization failed")


if __name__ == "__main__":
    initialize()
    server = ThreadingHTTPServer((HOST, PORT), PocketTTSHandler)
    server.daemon_threads = True
    logger.info("Pocket TTS listening on %s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if synthesizer is not None:
            synthesizer.close()
