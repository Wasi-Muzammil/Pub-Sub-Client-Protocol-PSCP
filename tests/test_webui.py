"""Tests for the browser bridge (tools/webui.py) against a real in-process broker.

The page itself is not executed here; these tests cover what it receives:
the room list, the history, session reuse on refresh, and the disconnect rules.
"""
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from pscp.broker import Broker
from pscp.client import PSCPClient
from pscp.log import setup

QUIET = "WARNING"
ROOT = Path(__file__).resolve().parent.parent


def load_bridge():
    spec = importlib.util.spec_from_file_location("webui_under_test", ROOT / "tools" / "webui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait_until(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


BRIDGE = load_bridge()


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "broker.db")
        self.broker = Broker("127.0.0.1", 0, logger=setup("broker-web", QUIET), db=self.db)
        self.port = self.broker.start()
        BRIDGE.DB = self.db
        BRIDGE._sessions.clear()
        self.clients = []

    def tearDown(self):
        for user in list(BRIDGE._sessions):
            BRIDGE._drop(user)
        for client in self.clients:
            client.reconnect = False
            client.close()
        self.broker.stop()
        self.tmp.cleanup()

    # helpers

    def state(self, user, room=""):
        return BRIDGE._api({"action": "state", "user": user,
                            "host": "127.0.0.1", "port": self.port, "room": room})

    def send(self, user, room, text):
        return BRIDGE._api({"action": "send", "user": user, "room": room, "text": text})

    def cli(self, name):
        inbox = []
        client = PSCPClient("127.0.0.1", name, self.port, logger=setup(f"web-cli-{name}", QUIET),
                            on_message=lambda room, sender, text, seq: inbox.append(text))
        client.connect()
        client.inbox = inbox
        self.clients.append(client)
        return client

    def room_names(self, data):
        return [room["name"] for room in data["rooms"]]


class RoomListTest(BridgeTestCase):
    def test_rooms_and_history_are_read_while_the_broker_writes(self):
        bob = self.cli("bob")
        bob.subscribe("room1")
        self.assertEqual(bob.publish("room1", "hello", wait=True).name, "OK")

        data = self.state("alice", "room1")
        self.assertTrue(data["ok"])
        rooms = {room["name"]: room for room in data["rooms"]}
        self.assertIn("room1", rooms)
        self.assertEqual(rooms["room1"]["last_text"], "hello")
        self.assertEqual([message["text"] for message in data["messages"]], ["hello"])

    def test_a_room_other_users_joined_is_visible_without_joining(self):
        bob = self.cli("bob")
        bob.subscribe("room1")

        data = self.state("alice")
        self.assertIn("room1", self.room_names(data))
        self.assertEqual(data["messages"], [])

    def test_opening_a_room_adds_it_to_the_same_response(self):
        data = self.state("alice", "fresh-room")
        self.assertIn("fresh-room", self.room_names(data))
        self.assertIn("fresh-room", BRIDGE._sessions["alice"].client.subscriptions)

    def test_presence_topic_is_not_a_room(self):
        data = self.state("alice")
        self.assertNotIn("$presence", self.room_names(data))

    def test_room_list_falls_back_to_the_database(self):
        bob = self.cli("bob")
        bob.subscribe("room1")
        bob.publish("room1", "old", wait=True)

        ghost = BRIDGE.Session("ghost", "127.0.0.1", self.port)
        self.assertIn("room1", [room["name"] for room in BRIDGE._rooms(ghost)])

    def test_missing_database_is_not_an_error(self):
        BRIDGE.DB = os.path.join(self.tmp.name, "missing.db")
        data = self.state("alice")
        self.assertTrue(data["ok"])
        self.assertEqual(data["rooms"], [])


class HistoryTest(BridgeTestCase):
    def test_messages_are_not_duplicated_between_database_and_memory(self):
        bob = self.cli("bob")
        bob.subscribe("room1")
        self.state("alice", "room1")

        for i in range(3):
            bob.publish("room1", f"m{i}", wait=True)
        alice = BRIDGE._sessions["alice"]
        self.assertTrue(wait_until(lambda: len(alice.events) >= 3))

        first = self.state("alice", "room1")
        second = self.state("alice", "room1")
        self.assertEqual([message["text"] for message in first["messages"]], ["m0", "m1", "m2"])
        self.assertEqual([message["text"] for message in second["messages"]], ["m0", "m1", "m2"])

    def test_messages_still_show_without_a_database(self):
        bob = self.cli("bob")
        bob.subscribe("room1")
        BRIDGE.DB = os.path.join(self.tmp.name, "missing.db")
        self.state("alice", "room1")

        bob.publish("room1", "live", wait=True)
        alice = BRIDGE._sessions["alice"]
        self.assertTrue(wait_until(lambda: any(event[3] == "live" for event in alice.events)))
        data = self.state("alice", "room1")
        self.assertEqual([message["text"] for message in data["messages"]], ["live"])


class SessionTest(BridgeTestCase):
    def test_refresh_reuses_the_same_session(self):
        first = self.state("alice")
        session = BRIDGE._sessions["alice"]
        second = self.state("alice")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertIs(BRIDGE._sessions["alice"], session)
        self.assertEqual(len(BRIDGE._sessions), 1)

    def test_leave_drops_the_session_and_a_later_state_reconnects(self):
        self.state("alice", "room1")
        BRIDGE._api({"action": "disconnect", "user": "alice"})
        self.assertNotIn("alice", BRIDGE._sessions)

        data = self.state("alice", "room1")
        self.assertTrue(data["ok"])
        self.assertIn("room1", BRIDGE._sessions["alice"].client.subscriptions)

    def test_idle_sweep_closes_abandoned_sessions(self):
        self.state("alice")
        BRIDGE._sessions["alice"].last_seen -= BRIDGE.IDLE_LIMIT + 1
        BRIDGE._sweep_once()
        self.assertNotIn("alice", BRIDGE._sessions)

    def test_a_dead_broker_is_reported_and_not_cached(self):
        result = BRIDGE._api({"action": "state", "user": "alice", "host": "127.0.0.1", "port": 1})
        self.assertFalse(result["ok"])
        self.assertNotIn("alice", BRIDGE._sessions)

    def test_bad_input_is_rejected(self):
        self.assertFalse(BRIDGE._api({"action": "state", "user": "   "})["ok"])
        self.assertFalse(BRIDGE._api({"action": "state", "user": "x", "port": 99999})["ok"])
        self.assertFalse(BRIDGE._api({"action": "nope", "user": "x"})["ok"])
        self.assertEqual(BRIDGE._sessions, {})


class SendTest(BridgeTestCase):
    def test_send_reaches_a_subscribed_client(self):
        bob = self.cli("bob")
        bob.subscribe("room1")
        self.state("alice", "room1")

        self.assertTrue(self.send("alice", "room1", "hi bob")["ok"])
        self.assertTrue(wait_until(lambda: bob.inbox == ["hi bob"]))

    def test_send_into_a_closed_room_is_rejected(self):
        self.state("alice", "room1")
        result = self.send("alice", "", "x")
        self.assertFalse(result["ok"])

    def test_ping_returns_a_round_trip_time(self):
        self.state("alice")
        result = BRIDGE._api({"action": "ping", "user": "alice",
                              "host": "127.0.0.1", "port": self.port})
        self.assertTrue(result["ok"])
        self.assertIsInstance(result["rtt"], float)
        self.assertGreaterEqual(result["rtt"], 0.0)


class HttpTest(BridgeTestCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BRIDGE.Handler)
        self.server.daemon_threads = True
        self.web_port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def url(self, path):
        return f"http://127.0.0.1:{self.web_port}{path}"

    def post(self, payload):
        request = urllib.request.Request(self.url("/api"), data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def test_page_is_served(self):
        with urllib.request.urlopen(self.url("/"), timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"<title>PSCP chat</title>", response.read())

    def test_state_over_http(self):
        data = self.post({"action": "state", "user": "alice", "host": "127.0.0.1",
                          "port": self.port, "room": ""})
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "live")

    def test_bad_json_is_a_400(self):
        request = urllib.request.Request(self.url("/api"), data=b"{",
                                         headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 400)


class RestartTest(BridgeTestCase):
    def restart_broker(self):
        for user in list(BRIDGE._sessions):
            BRIDGE._drop(user)
        for client in self.clients:
            client.reconnect = False
            client.close()
        self.clients.clear()
        self.broker.stop()

        self.broker = Broker("127.0.0.1", 0, logger=setup("broker-web", QUIET), db=self.db)
        self.port = self.broker.start()

    def test_messages_sent_after_a_broker_restart_still_appear(self):
        bob = self.cli("bob")
        bob.subscribe("room1")
        bob.publish("room1", "old", wait=True)

        self.restart_broker()

        bob = self.cli("bob")
        bob.subscribe("room1")
        first = self.state("alice", "room1")
        self.assertEqual([message["text"] for message in first["messages"]], ["old"])

        bob.publish("room1", "new", wait=True)
        alice = BRIDGE._sessions["alice"]
        self.assertTrue(wait_until(lambda: any(event[3] == "new" for event in alice.events)))
        data = self.state("alice", "room1")
        self.assertEqual([message["text"] for message in data["messages"]], ["old", "new"])


if __name__ == "__main__":
    unittest.main()
