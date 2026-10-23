import socket
import unittest

from pscp.constants import FLAG_DUP, FLAG_QOS, MsgType, Status
from pscp.wire import (
    Message, ProtocolError, decode, decode_delivery, encode, encode_delivery, read_message,
)

WORKED_EXAMPLE = bytes.fromhex("01 07 01 05 00 01 00 02 72 6F 6F 6D 31 48 69")


class WireTest(unittest.TestCase):
    def test_worked_example_encodes_exactly(self):
        msg = Message(MsgType.PUBLISH, FLAG_QOS, 1, "room1", b"Hi")
        self.assertEqual(encode(msg), WORKED_EXAMPLE)
        self.assertEqual(len(WORKED_EXAMPLE), 15)

    def test_round_trip(self):
        msg = Message(MsgType.PUBLISH, FLAG_QOS | FLAG_DUP, 65535, "café", "héllo".encode())
        out = decode(encode(msg))
        self.assertEqual(out, msg)
        self.assertTrue(out.dup)
        self.assertEqual(out.qos, 1)

    def test_bad_version(self):
        with self.assertRaises(ProtocolError) as cm:
            decode(b"\x02" + WORKED_EXAMPLE[1:])
        self.assertEqual(cm.exception.status, Status.BAD_VERSION)

    def test_unknown_type_and_reserved_flags_are_malformed(self):
        for bad in (b"\x01\x7f" + WORKED_EXAMPLE[2:], b"\x01\x07\x80" + WORKED_EXAMPLE[3:]):
            with self.assertRaises(ProtocolError) as cm:
                decode(bad)
            self.assertEqual(cm.exception.status, Status.MALFORMED)

    def test_read_message_handles_fragmented_stream(self):
        a, b = socket.socketpair()
        try:
            for byte in WORKED_EXAMPLE + WORKED_EXAMPLE:  # two messages, one byte per send
                a.send(bytes([byte]))
            self.assertEqual(read_message(b).topic, "room1")
            self.assertEqual(read_message(b).payload, b"Hi")
        finally:
            a.close()
            b.close()

    def test_delivery_payload(self):
        self.assertEqual(decode_delivery(encode_delivery("alice", b"Hi")), ("alice", b"Hi"))


if __name__ == "__main__":
    unittest.main()
