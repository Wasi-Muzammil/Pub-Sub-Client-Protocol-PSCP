import unittest

from pscp.broker.rooms import Rooms
from pscp.broker.session import Session
from pscp.constants import MAX_MSG_ID


class RoomsTest(unittest.TestCase):
    def setUp(self):
        self.rooms = Rooms()
        self.a, self.b = Session("a"), Session("b")
        for s in (self.a, self.b):
            s.conn = object()  # live

    def test_sequence_and_order_identical_for_all_subscribers(self):
        self.rooms.subscribe(self.a, "r", 1)
        self.rooms.subscribe(self.b, "r", 1)
        for i in range(5):
            self.rooms.publish("r", bytes([i]), 1)
        for s in (self.a, self.b):
            self.assertEqual([m.msg_id for m in s.pending], [1, 2, 3, 4, 5])

    def test_delivery_qos_is_minimum(self):
        self.rooms.subscribe(self.a, "r", 0)
        self.rooms.subscribe(self.b, "r", 1)
        self.rooms.publish("r", b"x", 1)
        self.rooms.publish("r", b"y", 0)
        self.assertEqual([m.qos for m in self.a.pending], [0, 0])
        self.assertEqual([m.qos for m in self.b.pending], [1, 0])

    def test_sequence_wraps_skipping_zero(self):
        self.rooms.subscribe(self.a, "r", 1)
        self.rooms._room("r").last_seq = MAX_MSG_ID - 1
        for _ in range(3):
            self.rooms.publish("r", b"x", 1)
        self.assertEqual([m.msg_id for m in self.a.pending], [MAX_MSG_ID, 1, 2])

    def test_retained_session_keeps_only_qos1(self):
        self.rooms.subscribe(self.a, "r", 1)
        self.a.conn = None
        self.rooms.publish("r", b"x", 0)
        self.rooms.publish("r", b"y", 1)
        self.assertEqual([m.payload for m in self.a.pending], [b"y"])

    def test_unsubscribe(self):
        self.rooms.subscribe(self.a, "r", 1)
        self.assertTrue(self.rooms.unsubscribe(self.a, "r"))
        self.assertFalse(self.rooms.unsubscribe(self.a, "r"))
        self.rooms.publish("r", b"x", 1)
        self.assertEqual(len(self.a.pending), 0)


if __name__ == "__main__":
    unittest.main()
