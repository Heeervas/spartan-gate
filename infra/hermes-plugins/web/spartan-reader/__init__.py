"""Spartan Gate Reader web extract backend for Hermes."""

from __future__ import annotations

from .provider import SpartanReaderWebExtractProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(SpartanReaderWebExtractProvider())
