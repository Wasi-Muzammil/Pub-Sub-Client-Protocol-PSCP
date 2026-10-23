import unittest

from pscp.constants import FLAG_QOS, MsgType
from pscp.reliability import DedupWindow, Outbox
from pscp.wire import Message


def pub(i):
    return Message(MsgType.PUBLISH, FLAG_QOS, i, "room", b"x")


class OutboxTest(unittest.TestCase):
    def test_retransmit_with_dup_then_link_dead(self):
        box = Outbox(retry_after=2, max_retries=3)
        box.add(1, pub(1), now=0)
        self.assertEqual(box.poll(now=1), ([], False))
        for t in (2, 4, 6):
            resend, dead = box.poll(now=t)
            self.assertFalse(dead)
            self.assertEqual(len(resend), 1)
            self.assertTrue(resend[0].dup)
        self.assertEqual(box.poll(now=8), ([], True))
        self.assertEqual(len(box), 1)  # kept for resume

    def test_ack_removes(self):
        box = Outbox()
        box.add(1, pub(1), now=0)
        self.assertTrue(box.ack(1))
        self.assertFalse(box.ack(1))
        self.assertEqual(box.poll(now=100), ([], False))

    def test_restart_keeps_order_and_resets_retries(self):
        box = Outbox(retry_after=2, max_retries=3)
        for i in (5, 3, 9):
            box.add(i, pub(i), now=0)
        box.poll(now=10)
        replay = box.restart(now=10)
        self.assertEqual([m.msg_id for m in replay], [5, 3, 9])
        self.assertTrue(all(m.dup for m in replay))
        self.assertEqual(box.poll(now=11), ([], False))


class DedupWindowTest(unittest.TestCase):
    def test_duplicate_detected(self):
        w = DedupWindow(size=4)
        self.assertFalse(w.check_and_add(("room", 1)))
        self.assertTrue(w.check_and_add(("room", 1)))
        self.assertFalse(w.check_and_add(("other", 1)))

    def test_old_keys_forgotten_so_16_bit_ids_can_wrap(self):
        w = DedupWindow(size=256)
        for seq in range(65000, 65536):
            w.check_and_add(seq)
        for seq in range(1, 300):  # after wrap-around, low IDs are new again
            self.assertFalse(w.check_and_add(seq))
        self.assertFalse(w.check_and_add(65000))  # long since evicted


if __name__ == "__main__":
    unittest.main()
