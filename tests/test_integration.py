"""Broker in-process on 127.0.0.1 with real TCP clients."""
import random
import socket
import threading
import time
import unittest

from pscp.broker import Broker
from pscp.client import ConnectError, PSCPClient
from pscp.constants import PRESENCE_TOPIC, MsgType, Presence, Status
from pscp.log import setup
from pscp.wire import Message, encode, encode_username, read_message

QUIET = "WARNING"


def wait_until(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class Inbox:
    def __init__(self):
        self.messages = []
        self.presence = []
        self.lock = threading.Lock()

    def on_message(self, room, sender, text, seq):
        with self.lock:
            self.messages.append((room, sender, text, seq))

    def on_presence(self, event, user):
        with self.lock:
            self.presence.append((event, user))

    def texts(self):
        with self.lock:
            return [m[2] for m in self.messages]


class BrokerTestCase(unittest.TestCase):
    broker_kwargs = {}

    def setUp(self):
        self.broker = Broker("127.0.0.1", 0, logger=setup("broker-test", QUIET), **self.broker_kwargs)
        self.port = self.broker.start()
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.reconnect = False
            c.close()
        self.broker.stop()

    def client(self, name, **kw):
        inbox = Inbox()
        c = PSCPClient("127.0.0.1", name, self.port, logger=setup(f"client-{name}", QUIET),
                       on_message=inbox.on_message, on_presence=inbox.on_presence, **kw)
        c.inbox = inbox
        self.clients.append(c)
        return c


class ChatTest(BrokerTestCase):
    def test_messages_delivered_once_in_order(self):
        alice, bob = self.client("alice"), self.client("bob")
        for c in (alice, bob):
            c.connect()
            self.assertEqual(c.subscribe("room1"), Status.OK)
        for i in range(10):
            alice.publish("room1", f"m{i}")
        expected = [f"m{i}" for i in range(10)]
        self.assertTrue(wait_until(lambda: bob.inbox.texts() == expected))
        self.assertEqual([m[3] for m in bob.inbox.messages], list(range(1, 11)))
        self.assertEqual(bob.inbox.messages[0][1], "alice")
        self.assertTrue(wait_until(lambda: alice.pending_count() == 0))

    def test_publish_without_subscription_is_rejected(self):
        alice = self.client("alice")
        alice.connect()
        self.assertEqual(alice.publish("nowhere", "x", wait=True), Status.NOT_SUBSCRIBED)
        self.assertEqual(alice.subscribe("$secret"), Status.INVALID_TOPIC)

    def test_username_taken(self):
        self.client("alice").connect()
        with self.assertRaises(ConnectError) as cm:
            self.client("alice", reconnect=False).connect()
        self.assertEqual(cm.exception.status, Status.USERNAME_TAKEN)

    def test_first_message_must_be_connect(self):
        with socket.create_connection(("127.0.0.1", self.port)) as s:
            s.sendall(encode(Message(MsgType.PING)))
            reply = read_message(s)
            self.assertEqual((reply.type, reply.status()), (MsgType.ERROR, Status.NOT_CONNECTED))
            self.assertEqual(s.recv(1), b"")  # broker closed the connection

    def test_presence_join_and_leave(self):
        watcher, bob = self.client("watcher"), self.client("bob")
        watcher.connect()
        watcher.subscribe(PRESENCE_TOPIC)
        bob.connect()
        bob.close()
        self.assertTrue(wait_until(lambda: (Presence.LEAVE, "bob") in watcher.inbox.presence))
        self.assertIn((Presence.JOIN, "bob"), watcher.inbox.presence)


class LossyAckTest(BrokerTestCase):
    broker_kwargs = {"drop_ack": 0.3}

    def test_exactly_once_delivery_with_dropped_acks_on_both_legs(self):
        random.seed(7)
        alice = self.client("alice")
        bob = self.client("bob", drop_ack=0.3)
        for c in (alice, bob):
            c.connect()
            c.subscribe("room1")
        expected = [f"m{i}" for i in range(10)]
        for text in expected:
            alice.publish("room1", text)
        self.assertTrue(wait_until(lambda: alice.pending_count() == 0, 30))
        self.assertTrue(wait_until(lambda: sorted(bob.inbox.texts()) == sorted(expected), 30))
        time.sleep(2.5)  # give the broker time to retransmit to bob; still no duplicates
        self.assertEqual(sorted(bob.inbox.texts()), sorted(expected))


class ResumeTest(BrokerTestCase):
    def _gap(self, sub_qos):
        alice = self.client("alice")
        bob = self.client("bob", reconnect=False)
        alice.connect()
        alice.subscribe("room1")
        bob.connect()
        bob.subscribe("room1", qos=sub_qos)
        bob._drop_link("test: simulated link loss")
        self.assertTrue(wait_until(lambda: not bob.connected.is_set()))
        self.assertTrue(wait_until(lambda: self.broker.registry.live_sessions().__len__() == 1))
        expected = [f"m{i}" for i in range(20)]
        for text in expected:
            alice.publish("room1", text)
        self.assertTrue(wait_until(lambda: alice.pending_count() == 0))
        bob.connect(resume=True)
        return bob, expected

    def test_qos1_resume_delivers_everything_in_order(self):
        bob, expected = self._gap(1)
        self.assertTrue(wait_until(lambda: bob.inbox.texts() == expected))

    def test_qos0_loses_messages_during_gap(self):
        bob, _ = self._gap(0)
        time.sleep(1)
        self.assertEqual(bob.inbox.texts(), [])


class KeepAliveTest(BrokerTestCase):
    broker_kwargs = {"keepalive": 1.0}

    def test_silent_client_times_out_and_session_is_retained(self):
        s = socket.create_connection(("127.0.0.1", self.port))
        s.sendall(encode(Message(MsgType.CONNECT, 0, 0, "", encode_username("ghost"))))
        self.assertEqual(read_message(s).status(), Status.OK)
        s.settimeout(5)
        t0 = time.monotonic()
        try:
            self.assertEqual(s.recv(1), b"")  # never pings -> closed after 1.5 x keepalive
        finally:
            s.close()
        self.assertGreaterEqual(time.monotonic() - t0, 1.5)
        self.assertTrue(wait_until(lambda: not self.broker.registry.live_sessions()))
        self.assertIn("ghost", self.broker.registry._sessions)  # retained for RESUME


if __name__ == "__main__":
    unittest.main()
