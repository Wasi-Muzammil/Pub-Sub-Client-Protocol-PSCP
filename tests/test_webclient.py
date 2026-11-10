"""Web client: SQLite store + ChatService against an in-process broker (Streamlit only for the smoke test)."""
import os
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from pscp.broker import Broker
from pscp.log import setup
from tests.test_integration import QUIET, wait_until
from webclient import profiles
from webclient.service import ChatService, ping_stats
from webclient.store import Store

FAST = {"backoff": (0.2, 0.2, 0.2), "retry_interval": 0.2}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebClientCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.services = []
        self.broker = None

    def tearDown(self):
        for s in self.services:
            s.stop()
            s.store.close()
        if self.broker:
            self.broker.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def start_broker(self, port=0):
        self.broker = Broker("127.0.0.1", port, logger=setup("broker-web", QUIET))
        return self.broker.start()

    def service(self, user, port, display=None):
        store = Store(Path(self.dir) / f"{user}.sqlite3")
        if store.get_profile() is None:
            store.save_profile(user, display or user.title(), f"about {user}", "🙂", "127.0.0.1", port)
        svc = ChatService(store, logger=setup(f"web-{user}", QUIET), **FAST).start()
        self.services.append(svc)
        return svc

    def statuses(self, svc, room):
        return [m["status"] for m in svc.store.messages(room) if m["direction"] == "out"]


class StoreTest(WebClientCase):
    def test_schema_profile_rooms_and_ordering(self):
        st = Store(Path(self.dir) / "a.sqlite3")
        st.save_profile("a", "A", "", "", "h", 5050)
        self.assertEqual(st.get_profile()["display_name"], "A")
        st.join_room("r1")
        st.join_room("r2")
        st.add_outgoing("r1", "a", "hello")  # r1 becomes most recent
        self.assertEqual(st.active_room_names(), ["r1", "r2"])
        st.add_incoming("r1", "b", "hi", 1)
        self.assertEqual([m["text"] for m in st.messages("r1")], ["hello", "hi"])
        self.assertEqual(st.room_members("r1"), ["b"])
        st.leave_room("r2")
        self.assertEqual(st.active_room_names(), ["r1"])
        st.close()

    def test_tick_transitions_never_downgrade(self):
        st = Store(Path(self.dir) / "a.sqlite3")
        rid = st.add_outgoing("r", "a", "x")
        self.assertTrue(st.mark_echo("r", "x", 7))  # echo can beat the msg_id bookkeeping
        st.mark_sending(rid, 3)
        st.mark_acked(3, True)
        self.assertEqual(st.messages("r")[0]["status"], "delivered")
        st.close()

    def test_profile_messages_are_validated(self):
        self.assertIsNone(profiles.decode("not json"))
        self.assertIsNone(profiles.decode('{"v":1,"kind":"profile","display_name":1,"about":"","avatar":""}'))
        bad = profiles.decode('{"v":1,"kind":"profile","display_name":"x","about":"","avatar":"data:text/html,<b>"}')
        self.assertEqual(bad["avatar"], "")
        self.assertFalse(profiles.valid_room_name("_profiles"))
        self.assertFalse(profiles.valid_room_name("$presence"))


class PingTest(WebClientCase):
    def test_ping_stats(self):
        s = ping_stats([10.0, None, 30.0, 20.0])
        self.assertEqual((s["avg"], s["min"], s["max"], s["loss"], s["sent"]), (20.0, 10.0, 30.0, 25.0, 4))
        lost = ping_stats([None, None])
        self.assertEqual((lost["avg"], lost["loss"]), (None, 100.0))

    def test_ping_online_and_offline(self):
        port = self.start_broker()
        alice = self.service("alice", port)
        self.assertTrue(wait_until(lambda: alice.online))
        rtts = alice.ping(3)
        self.assertEqual(len(rtts), 3)
        self.assertTrue(all(isinstance(r, float) and 0 < r < 1000 for r in rtts))
        ghost = self.service("ghost", free_port())  # no broker there
        self.assertEqual(ghost.ping(2, timeout=0.5), [None, None])

    def test_manual_ping_not_confused_by_keepalive(self):
        port = self.start_broker()
        alice = self.service("alice", port)
        self.assertTrue(wait_until(lambda: alice.online))
        alice.client._send_ping()  # an outstanding "keep-alive" PING whose PONG must not end our timing
        self.assertIsInstance(alice.client.ping(2.0), float)
        self.assertEqual(alice.client._pings_out, 0)


class ChatServiceTest(WebClientCase):
    def test_chat_ticks_presence_and_profiles(self):
        port = self.start_broker()
        alice = self.service("alice", port, "Alice A")
        self.assertTrue(wait_until(lambda: alice.online))
        alice.join("room1")
        bob = self.service("bob", port, "Bob B")  # joins later: learns Alice's profile via `who`
        self.assertTrue(wait_until(lambda: bob.online))
        bob.join("room1")

        alice.send("room1", "hi bob")
        self.assertTrue(wait_until(lambda: [m["text"] for m in bob.store.messages("room1")] == ["hi bob"]))
        self.assertTrue(wait_until(lambda: self.statuses(alice, "room1") == ["delivered"]))
        self.assertEqual(len(alice.store.messages("room1")), 1)  # echo did not add a duplicate

        self.assertTrue(wait_until(lambda: (bob.store.contacts().get("alice") or {}).get("display_name") == "Alice A"))
        self.assertTrue(wait_until(lambda: (alice.store.contacts().get("bob") or {}).get("display_name") == "Bob B"))
        self.assertTrue(wait_until(lambda: (alice.store.contacts().get("bob") or {}).get("online") == 1))
        bob.stop()
        self.assertTrue(wait_until(lambda: (alice.store.contacts().get("bob") or {}).get("online") == 0))

    def test_offline_outbox_delivered_in_order_when_broker_appears(self):
        port = free_port()
        alice = self.service("alice", port)  # no broker yet
        alice.store.join_room("room1")
        for i in range(3):
            alice.send("room1", f"m{i}")
        self.assertEqual(self.statuses(alice, "room1"), ["queued"] * 3)
        self.start_broker(port)
        watcher = self.service("watcher", port)
        watcher.join("room1")
        self.assertTrue(wait_until(lambda: alice.online, 15))
        self.assertTrue(wait_until(lambda: self.statuses(alice, "room1") == ["delivered"] * 3))
        self.assertTrue(wait_until(lambda: [m["text"] for m in watcher.store.messages("room1")] == ["m0", "m1", "m2"]))

    def test_restart_rejoins_stored_rooms(self):
        port = self.start_broker()
        alice = self.service("alice", port)
        self.assertTrue(wait_until(lambda: alice.online))
        alice.join("room1")
        alice.stop()
        alice.store.close()
        self.services.remove(alice)
        again = self.service("alice", port)  # same SQLite file
        bob = self.service("bob", port)
        self.assertTrue(wait_until(lambda: again.online and bob.online))
        bob.join("room1")
        bob.send("room1", "are you back?")
        self.assertTrue(wait_until(lambda: [m["text"] for m in again.store.messages("room1")] == ["are you back?"]))


@unittest.skipUnless(shutil.which("streamlit") or __import__("importlib").util.find_spec("streamlit"),
                     "streamlit not installed")
class AppSmokeTest(unittest.TestCase):
    def test_setup_page_renders(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as d:
            os.environ["PSCP_DATA_DIR"] = d
            try:
                at = AppTest.from_file(str(Path(__file__).parents[1] / "webclient" / "app.py"), default_timeout=30)
                at.run()
                self.assertFalse(at.exception)
                self.assertTrue(any("Create a profile" in m.value for m in at.markdown))
            finally:
                del os.environ["PSCP_DATA_DIR"]


if __name__ == "__main__":
    unittest.main()
