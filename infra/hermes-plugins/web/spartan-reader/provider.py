"""Hermes web_extract provider backed by Spartan Gate Reader."""

from __future__ import annotations

import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from agent.web_search_provider import WebSearchProvider


PROVIDER_NAME = "spartan-reader"
DISPLAY_NAME = "Spartan Reader"
DEFAULT_READER_URL = "http://reader:3000"
UNTRUSTED_HEADER = "untrusted-web-content"
UNTRUSTED_BEGIN = "BEGIN_UNTRUSTED_WEB_CONTENT"
UNTRUSTED_END = "END_UNTRUSTED_WEB_CONTENT"
DEFAULT_TIMEOUT_SECONDS = 20


class SpartanReaderWebExtractProvider(WebSearchProvider):
    """Extract static public page text through the internal Reader service."""

    def __init__(
        self,
        *,
        reader_url: str | None = None,
        timeout_seconds: int | None = None,
        allow_test_reader_url: bool = False,
    ) -> None:
        self._reader_url_override = reader_url
        self._timeout_seconds = timeout_seconds
        self._allow_test_reader_url = allow_test_reader_url

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def display_name(self) -> str:
        return DISPLAY_NAME

    def is_available(self) -> bool:
        return self._normalized_reader_url() is not None

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: list[str], **_kwargs: Any) -> list[dict[str, Any]]:
        reader_url = self._normalized_reader_url()
        if reader_url is None:
            return [self._error_result(url, "Spartan Reader is not available") for url in urls]

        return [self._extract_one(reader_url, url) for url in urls]

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": DISPLAY_NAME,
            "badge": "local",
            "tag": "Uses Spartan Gate's internal GET-only Reader service.",
            "env_vars": [],
        }

    def _extract_one(self, reader_url: str, url: str) -> dict[str, Any]:
        endpoint = f"{reader_url}/fetch?url={urllib.parse.quote(url, safe='')}"
        request = urllib.request.Request(
            endpoint,
            method="GET",
            headers={"Accept": "text/plain"},
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout()) as response:
                body = response.read().decode(_response_charset(response), errors="replace")
                trust = response.headers.get("X-Spartan-Content-Trust", "")
        except urllib.error.HTTPError as exc:
            return self._error_result(url, _message_for_reader_status(exc.code))
        except TimeoutError:
            return self._error_result(url, "Reader request timed out")
        except socket.timeout:
            return self._error_result(url, "Reader request timed out")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                return self._error_result(url, "Reader request timed out")
            return self._error_result(url, "Could not reach Reader service")
        except OSError:
            return self._error_result(url, "Could not reach Reader service")

        if trust != UNTRUSTED_HEADER or UNTRUSTED_BEGIN not in body or UNTRUSTED_END not in body:
            return self._error_result(url, "Reader returned an invalid Reader trust contract")

        return {
            "url": url,
            "title": "",
            "content": body,
            "raw_content": body,
            "metadata": {
                "source": PROVIDER_NAME,
                "content_trust": UNTRUSTED_HEADER,
            },
        }

    def _error_result(self, url: str, error: str) -> dict[str, Any]:
        return {
            "url": url,
            "title": "",
            "content": "",
            "raw_content": "",
            "metadata": {
                "source": PROVIDER_NAME,
                "content_trust": UNTRUSTED_HEADER,
            },
            "error": error,
        }

    def _timeout(self) -> int:
        if self._timeout_seconds is not None:
            return max(1, min(int(self._timeout_seconds), 120))
        raw = _env_value("READER_TIMEOUT")
        if raw:
            try:
                return max(5, min(int(raw) + 5, 125))
            except ValueError:
                return DEFAULT_TIMEOUT_SECONDS
        return DEFAULT_TIMEOUT_SECONDS

    def _normalized_reader_url(self) -> str | None:
        raw = (self._reader_url_override or _env_value("READER_PROXY_URL")).strip()
        if not raw:
            return None
        normalized = raw.rstrip("/")
        if self._allow_test_reader_url:
            parsed = urllib.parse.urlparse(normalized)
            if parsed.scheme == "http" and parsed.hostname and parsed.port:
                return normalized
            return None
        return normalized if normalized == DEFAULT_READER_URL else None


def _env_value(name: str) -> str:
    try:
        from hermes_cli.config import get_env_value

        value = get_env_value(name)
    except Exception:
        value = None
    if value is None:
        value = os.getenv(name, "")
    return (value or "").strip()


def _response_charset(response: Any) -> str:
    content_type = response.headers.get("Content-Type", "")
    if "charset=" not in content_type:
        return "utf-8"
    return content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"


def _message_for_reader_status(status: int) -> str:
    if status == 400:
        return "Reader rejected the URL"
    if status == 403:
        return "Reader blocked the URL"
    if status == 502:
        return "Reader could not reach the URL"
    if status == 504:
        return "Reader request timed out"
    return f"Reader returned HTTP {status}"
