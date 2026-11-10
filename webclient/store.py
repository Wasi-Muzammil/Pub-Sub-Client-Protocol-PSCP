"""Local SQLite store: everything PSCP itself never keeps (profile, joined rooms, history, contacts).

One connection shared by the Streamlit thread and the PSCP receiver thread,
serialised with a lock. WAL mode lets the UI read while a write is in progress.
"""
import sqlite3
import threading
import time

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    about TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    broker_host TEXT NOT NULL,
    broker_port INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
    name TEXT PRIMARY KEY,
    joined_at REAL NOT NULL,
    left_at REAL,
    last_activity REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room TEXT NOT NULL,
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'sending', 'sent', 'delivered', 'failed', 'received')),
    client_msg_id INTEGER,
    seq INTEGER,
    created_at REAL NOT NULL,
    received_at REAL
);
CREATE INDEX IF NOT EXISTS messages_room ON messages (room, id);
CREATE TABLE IF NOT EXISTS contacts (
    username TEXT PRIMARY KEY,
    display_name TEXT,
    about TEXT,
    avatar TEXT,
    online INTEGER NOT NULL DEFAULT 0,
    last_seen REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS room_members (
    room TEXT NOT NULL,
    username TEXT NOT NULL,
    last_active REAL NOT NULL,
    PRIMARY KEY (room, username)
);
"""


class Store:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            if self._db.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                self._db.executescript(SCHEMA)
                self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self):
        with self._lock:
            self._db.close()

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def _run(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args)

    # ---- profile -------------------------------------------------------

    def get_profile(self):
        return self._one("SELECT * FROM profile WHERE id = 1")

    def save_profile(self, username, display_name, about, avatar, broker_host, broker_port):
        self._run("""INSERT INTO profile (id, username, display_name, about, avatar, broker_host, broker_port, created_at)
                     VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT (id) DO UPDATE SET display_name = excluded.display_name, about = excluded.about,
                       avatar = excluded.avatar, broker_host = excluded.broker_host, broker_port = excluded.broker_port""",
                  (username, display_name, about, avatar, broker_host, int(broker_port), time.time()))

    # ---- rooms ---------------------------------------------------------

    def join_room(self, name):
        now = time.time()
        self._run("""INSERT INTO rooms (name, joined_at, last_activity) VALUES (?, ?, ?)
                     ON CONFLICT (name) DO UPDATE SET left_at = NULL, last_activity = excluded.last_activity""",
                  (name, now, now))

    def leave_room(self, name):
        self._run("UPDATE rooms SET left_at = ? WHERE name = ?", (time.time(), name))

    def delete_room(self, name):
        with self._lock:
            for table in ("messages", "room_members", "rooms"):
                col = "name" if table == "rooms" else "room"
                self._db.execute(f"DELETE FROM {table} WHERE {col} = ?", (name,))

    def rooms(self, include_left=False):
        """Rooms for the chat list, most recent activity first."""
        where = "" if include_left else "WHERE left_at IS NULL"
        return self._all(f"SELECT * FROM rooms {where} ORDER BY last_activity DESC")

    def active_room_names(self):
        return [r["name"] for r in self.rooms()]

    def _touch(self, room, ts):
        self._db.execute("UPDATE rooms SET last_activity = MAX(last_activity, ?) WHERE name = ?", (ts, room))

    # ---- messages ------------------------------------------------------

    def add_outgoing(self, room, sender, text):
        now = time.time()
        with self._lock:
            cur = self._db.execute("""INSERT INTO messages (room, sender, text, direction, status, created_at)
                                      VALUES (?, ?, ?, 'out', 'queued', ?)""", (room, sender, text, now))
            self._touch(room, now)
            return cur.lastrowid

    def queued(self):
        return self._all("SELECT * FROM messages WHERE status = 'queued' ORDER BY id")

    def requeue_unacked(self):
        """After an app restart the in-memory PSCP outbox is gone: send 'sending' rows again."""
        self._run("UPDATE messages SET status = 'queued', client_msg_id = NULL WHERE status = 'sending'")

    def mark_sending(self, row_id, client_msg_id):
        # the echo may already have marked the row delivered: never downgrade
        self._run("""UPDATE messages SET client_msg_id = ?,
                       status = CASE WHEN status = 'queued' THEN 'sending' ELSE status END
                     WHERE id = ?""", (client_msg_id, row_id))

    def mark_acked(self, client_msg_id, ok):
        """PUBACK for one of our publishes. Returns False if no row carries that ID (yet)."""
        new = "sent" if ok else "failed"
        cur = self._run("""UPDATE messages SET client_msg_id = NULL,
                             status = CASE WHEN status IN ('queued', 'sending') OR ? = 'failed' THEN ? ELSE status END
                           WHERE id = (SELECT id FROM messages WHERE client_msg_id = ? AND direction = 'out'
                                       ORDER BY id DESC LIMIT 1)""", (new, new, client_msg_id))
        return cur.rowcount > 0

    def mark_echo(self, room, text, seq):
        """Our own message came back through the room: the oldest matching outgoing row is delivered."""
        cur = self._run("""UPDATE messages SET status = 'delivered', seq = ?, received_at = ?
                           WHERE id = (SELECT id FROM messages WHERE room = ? AND text = ? AND direction = 'out'
                                       AND status IN ('queued', 'sending', 'sent') ORDER BY id LIMIT 1)""",
                        (seq, time.time(), room, text))
        return cur.rowcount > 0

    def add_incoming(self, room, sender, text, seq):
        now = time.time()
        with self._lock:
            self._db.execute("""INSERT INTO messages (room, sender, text, direction, status, seq, created_at, received_at)
                                VALUES (?, ?, ?, 'in', 'received', ?, ?, ?)""", (room, sender, text, seq, now, now))
            self._db.execute("""INSERT INTO room_members (room, username, last_active) VALUES (?, ?, ?)
                                ON CONFLICT (room, username) DO UPDATE SET last_active = excluded.last_active""",
                             (room, sender, now))
            self._touch(room, now)

    def messages(self, room, limit=500):
        rows = self._all("SELECT * FROM messages WHERE room = ? ORDER BY id DESC LIMIT ?", (room, limit))
        return rows[::-1]

    # ---- contacts / presence ------------------------------------------

    def upsert_contact(self, username, display_name, about, avatar):
        self._run("""INSERT INTO contacts (username, display_name, about, avatar, updated_at) VALUES (?, ?, ?, ?, ?)
                     ON CONFLICT (username) DO UPDATE SET display_name = excluded.display_name,
                       about = excluded.about, avatar = excluded.avatar, updated_at = excluded.updated_at""",
                  (username, display_name, about, avatar, time.time()))

    def set_presence(self, username, online):
        self._run("""INSERT INTO contacts (username, online, last_seen) VALUES (?, ?, ?)
                     ON CONFLICT (username) DO UPDATE SET online = excluded.online, last_seen = excluded.last_seen""",
                  (username, int(online), time.time()))

    def all_offline(self):
        """Our own link went down: we no longer know who is online."""
        self._run("UPDATE contacts SET online = 0 WHERE online = 1")

    def contacts(self):
        return {c["username"]: c for c in self._all("SELECT * FROM contacts")}

    def room_members(self, room):
        return [r["username"] for r in self._all(
            "SELECT username FROM room_members WHERE room = ? ORDER BY last_active DESC", (room,))]
