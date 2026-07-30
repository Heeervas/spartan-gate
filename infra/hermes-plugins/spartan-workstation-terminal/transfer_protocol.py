"""Versioned binary framing for workstation transfer streams."""

from __future__ import annotations

import hashlib
import json
import socket
import struct
from typing import Any, BinaryIO


MAGIC = b"SGWT"
VERSION = 1
FRAME_CONTROL = 1
FRAME_DATA = 2
FRAME_END = 3
FRAME_RESULT = 4
FRAME_ERROR = 5
MAX_CONTROL_BYTES = 64 * 1024
MAX_DATA_BYTES = 1024 * 1024
HEADER = struct.Struct("!BI")
OPERATION_FIELDS = (
    "operation",
    "owner",
    "transfer_id",
    "local_workspace",
    "local_path",
    "remote_root",
    "remote_path",
    "replace",
    "allow_sensitive",
    "background",
    "timeout",
    "manifest_sha256",
    "total_bytes",
    "entry_count",
    "expected_destination",
)


class ProtocolError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def operation_hash(value: dict[str, Any]) -> str:
    selected = {field: value.get(field) for field in OPERATION_FIELDS}
    return hashlib.sha256(canonical_json(selected)).hexdigest()


def terminal_operation_hash(value: dict[str, Any]) -> str:
    selected = {
        "kind": "terminal",
        "owner": value.get("owner"),
        "command": value.get("command"),
        "workdir": value.get("workdir"),
        "background": value.get("background", False),
        "pty": value.get("pty", False),
        "timeout": value.get("timeout"),
    }
    return hashlib.sha256(canonical_json(selected)).hexdigest()

def read_exact(stream: BinaryIO | socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = stream.recv(length - len(result)) if isinstance(stream, socket.socket) else stream.read(length - len(result))
        if not chunk:
            raise ProtocolError("unexpected end of transfer stream")
        result.extend(chunk)
    return bytes(result)


def send_preamble(stream: BinaryIO | socket.socket) -> None:
    data = MAGIC + bytes((VERSION,))
    if isinstance(stream, socket.socket):
        stream.sendall(data)
    else:
        stream.write(data)


def recv_preamble(stream: BinaryIO | socket.socket) -> None:
    value = read_exact(stream, len(MAGIC) + 1)
    if value[: len(MAGIC)] != MAGIC or value[-1] != VERSION:
        raise ProtocolError("unsupported workstation transfer protocol")


def send_frame(
    stream: BinaryIO | socket.socket,
    frame_type: int,
    payload: bytes = b"",
) -> None:
    maximum = MAX_DATA_BYTES if frame_type == FRAME_DATA else MAX_CONTROL_BYTES
    if frame_type not in {
        FRAME_CONTROL,
        FRAME_DATA,
        FRAME_END,
        FRAME_RESULT,
        FRAME_ERROR,
    }:
        raise ProtocolError("unknown transfer frame type")
    if len(payload) > maximum:
        raise ProtocolError("transfer frame exceeds its size limit")
    encoded = HEADER.pack(frame_type, len(payload)) + payload
    if isinstance(stream, socket.socket):
        stream.sendall(encoded)
    else:
        stream.write(encoded)


def recv_frame(stream: BinaryIO | socket.socket) -> tuple[int, bytes]:
    frame_type, length = HEADER.unpack(read_exact(stream, HEADER.size))
    maximum = MAX_DATA_BYTES if frame_type == FRAME_DATA else MAX_CONTROL_BYTES
    if frame_type not in {
        FRAME_CONTROL,
        FRAME_DATA,
        FRAME_END,
        FRAME_RESULT,
        FRAME_ERROR,
    }:
        raise ProtocolError("unknown transfer frame type")
    if length > maximum:
        raise ProtocolError("transfer frame exceeds its size limit")
    return frame_type, read_exact(stream, length)


class FramedWriter:
    def __init__(self, stream: BinaryIO | socket.socket):
        self.stream = stream

    def write(self, value: bytes) -> int:
        view = memoryview(value)
        while view:
            chunk = bytes(view[:MAX_DATA_BYTES])
            send_frame(self.stream, FRAME_DATA, chunk)
            view = view[len(chunk) :]
        return len(value)

    def flush(self) -> None:
        flush = getattr(self.stream, "flush", None)
        if flush:
            flush()


class FramedReader:
    def __init__(self, stream: BinaryIO | socket.socket):
        self.stream = stream
        self.buffer = bytearray()
        self.ended = False
        self.result: dict[str, Any] | None = None

    def _next(self) -> None:
        frame_type, payload = recv_frame(self.stream)
        if frame_type == FRAME_DATA:
            self.buffer.extend(payload)
            return
        if frame_type == FRAME_ERROR:
            try:
                message = json.loads(payload.decode("utf-8")).get("error")
            except Exception:
                message = None
            raise ProtocolError(str(message or "remote transfer failed"))
        if frame_type == FRAME_END:
            self.ended = True
            return
        raise ProtocolError("unexpected frame in transfer data stream")

    def read(self, size: int = -1) -> bytes:
        while not self.ended and (size < 0 or len(self.buffer) < size):
            self._next()
        if size is None or size < 0:
            data = bytes(self.buffer)
            self.buffer.clear()
            return data
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data


def send_control(stream: BinaryIO | socket.socket, value: dict[str, Any]) -> None:
    send_frame(stream, FRAME_CONTROL, canonical_json(value))


def recv_control(stream: BinaryIO | socket.socket) -> dict[str, Any]:
    frame_type, payload = recv_frame(stream)
    if frame_type != FRAME_CONTROL:
        raise ProtocolError("transfer stream must begin with a control frame")
    try:
        value = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid transfer control JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("transfer control must be an object")
    return value
