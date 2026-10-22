"""Acquisition-owned provider cleanup, including generators never started."""
from __future__ import annotations

from typing import Any, AsyncIterator
import inspect
import json

from .sse import frames
from .util import stable_json


async def notify(callback: Any) -> None:
    if callback is not None:
        outcome = callback()
        if inspect.isawaitable(outcome):
            await outcome


class UpstreamOwner:
    def __init__(self, response: Any, finish: Any = None):
        self.response, self.finish = response, finish
        self.closed = False

    def close(self, delivered: bool = False) -> None:
        if self.closed:
            return
        self.closed = True
        self.response.release()
        if self.finish is not None:
            self.finish(delivered)

    async def cleanup(self) -> None:
        self.close()


async def provider_stream(response: Any, result: Any, owner: UpstreamOwner, usage: Any, tools: Any) -> AsyncIterator[bytes]:
    try:
        async for _event, data, raw in frames(response.content.iter_chunked(8192)):
            if data == '[DONE]':
                result.stream_completed = True
            elif data:
                try:
                    parsed = json.loads(data)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    usage(result, parsed)
                    tools(result, parsed)
                    if parsed.get('error') and not result.error:
                        result.error = stable_json(parsed['error'])
            yield raw
    finally:
        owner.close(result.stream_completed)
