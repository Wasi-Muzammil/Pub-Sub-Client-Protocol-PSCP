import unittest

from pscp.store import Store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def tearDown(self):
        self.store.close()

    def rooms(self):
        return {room["name"]: room for room in self.store.rooms()}

    def test_rooms_show_last_message_and_unread(self):
        self.store.save_message("room1", 1, "alice", "hi")
        self.store.save_message("room1", 2, "bob", "there")
        self.store.upsert_room("room2")
        rooms = self.rooms()
        self.assertEqual(rooms["room1"]["last_text"], "there")
        self.assertEqual(rooms["room1"]["unread"], 2)
        self.store.mark_read("room1", 2)
        self.assertEqual(self.rooms()["room1"]["unread"], 0)

    def test_memberships_and_history_order(self):
        self.store.set_membership("room1", "alice", True)
        self.store.set_membership("room1", "bob", True)
        self.store.save_message("room1", 1, "alice", "one")
        self.store.save_message("room1", 2, "bob", "two")
        self.assertEqual(self.rooms()["room1"]["members"], 2)
        self.assertEqual([m["text"] for m in self.store.history("room1")], ["one", "two"])
        self.store.drop_memberships("bob")
        self.assertEqual(self.rooms()["room1"]["members"], 1)

    def test_a_message_is_stored_once(self):
        self.store.save_message("room1", 1, "alice", "hi")
        self.store.save_message("room1", 1, "alice", "hi")
        self.assertEqual(len(self.store.history("room1")), 1)


if __name__ == "__main__":
    unittest.main()
