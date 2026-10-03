"""Parse complete SSE frames as bytes so network splits cannot corrupt UTF-8."""
from __future__ import annotations

from typing import AsyncIterator


async def frames(source: AsyncIterator[bytes]) -> AsyncIterator[tuple[str | None, str, bytes]]:
    buffer = bytearray()
    searched = 0

    def parse(raw: bytes) -> tuple[str | None, str, bytes]:
        event = None
        data = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                value = line[5:]
                data.append(value[1:] if value.startswith(" ") else value)
        return event, "\n".join(data), raw

    async for chunk in source:
        buffer.extend(chunk)
        while True:
            lf = buffer.find(b"\n\n", searched)
            crlf = buffer.find(b"\r\n\r\n", searched)
            boundaries = [(index, size) for index, size in ((lf, 2), (crlf, 4)) if index >= 0]
            if not boundaries:
                searched = max(0, len(buffer) - 3)
                break
            index, size = min(boundaries)
            raw = bytes(buffer[:index + size])
            del buffer[:index + size]
            searched = 0
            event, data, raw = parse(raw)
            if data:
                yield event, data, raw
    if buffer.strip():
        event, data, raw = parse(bytes(buffer))
        if data:
            yield event, data, raw
