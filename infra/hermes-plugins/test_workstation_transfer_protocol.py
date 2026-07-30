from __future__ import annotations

import importlib.util
import io
import pathlib
import unittest


PATH = (
    pathlib.Path(__file__).parent
    / "spartan-workstation-terminal"
    / "transfer_protocol.py"
)


def load_protocol():
    spec = importlib.util.spec_from_file_location("workstation_transfer_protocol", PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class WorkstationTransferProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol = load_protocol()

    def test_round_trip_preamble_control_and_binary_frames(self):
        stream = io.BytesIO()
        self.protocol.send_preamble(stream)
        self.protocol.send_control(stream, {"operation": "upload", "value": "\u00f1"})
        writer = self.protocol.FramedWriter(stream)
        payload = bytes(range(256)) * 5000
        writer.write(payload)
        self.protocol.send_frame(stream, self.protocol.FRAME_END)
        stream.seek(0)

        self.protocol.recv_preamble(stream)
        self.assertEqual(
            self.protocol.recv_control(stream),
            {"operation": "upload", "value": "\u00f1"},
        )
        reader = self.protocol.FramedReader(stream)
        self.assertEqual(reader.read(), payload)

    def test_truncated_unknown_and_oversized_frames_fail_closed(self):
        cases = [
            b"SGWT\x01\x02",
            b"SGWT\x01" + self.protocol.HEADER.pack(99, 0),
            b"SGWT\x01"
            + self.protocol.HEADER.pack(
                self.protocol.FRAME_DATA,
                self.protocol.MAX_DATA_BYTES + 1,
            ),
        ]
        for encoded in cases:
            with self.subTest(encoded=encoded[:12]):
                stream = io.BytesIO(encoded)
                self.protocol.recv_preamble(stream)
                with self.assertRaises(self.protocol.ProtocolError):
                    self.protocol.recv_frame(stream)

    def test_operation_hash_ignores_unapproved_extra_fields(self):
        base = {
            "operation": "upload",
            "owner": "a" * 32 + ":g0",
            "local_workspace": "default",
            "local_path": "artifact.bin",
        }
        altered = dict(base, host="attacker", private_bytes="secret")
        self.assertEqual(
            self.protocol.operation_hash(base),
            self.protocol.operation_hash(altered),
        )


if __name__ == "__main__":
    unittest.main()
