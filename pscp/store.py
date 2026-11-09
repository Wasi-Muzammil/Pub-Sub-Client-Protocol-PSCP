"""SQLite mirror for rooms, users and messages (standard library only).

The broker keeps live state in memory and writes a copy here; the demo UI reads
it so it can list rooms and show history after a browser refresh.
Nothing here sits on the message path: a failed write is logged and ignored.
"""
import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    name TEXT PRIMARY KEY, created_at REAL NOT NULL, last_seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, first_seen REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memberships (
    room TEXT NOT NULL, username TEXT NOT NULL, joined_at REAL NOT NULL,
    PRIMARY KEY (room, username));
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL, seq INTEGER NOT NULL,
    sender TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL, qos INTEGER NOT NULL DEFAULT 1,
    UNIQUE (room, seq));
CREATE TABLE IF NOT EXISTS room_state (
    room TEXT PRIMARY KEY, last_read_seq INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS messages_by_room ON messages (room, seq);
"""


class Store:
    """One connection, one lock: SQLite is never called once per chat message."""

    def __init__(self, path=":memory:"):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.commit()

    def close(self):
        with self._lock:
            self._db.close()

    # ---- internals -----------------------------------------------------

    def _write(self, sql, rows):
        with self._lock, self._db:
            self._db.executemany(sql, rows)

    def _rows(self, sql, args=()):
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, args)]

    # ---- written by the broker ----------------------------------------

    def touch_user(self, username, now=None):
        now = time.time() if now is None else now
        self._write("""INSERT INTO users VALUES (?, ?, ?)
                       ON CONFLICT(username) DO UPDATE SET last_seen=excluded.last_seen""",
                    [(username, now, now)])

    def upsert_room(self, name, now=None):
        self._write("INSERT OR IGNORE INTO rooms (name, created_at, last_seq) VALUES (?, ?, 0)",
                    [(name, time.time() if now is None else now)])

    def set_membership(self, room, username, joined, now=None):
        now = time.time() if now is None else now
        if not joined:
            self._write("DELETE FROM memberships WHERE room=? AND username=?", [(room, username)])
            return
        self.upsert_room(room, now)
        self._write("INSERT OR IGNORE INTO memberships VALUES (?, ?, ?)", [(room, username, now)])

    def drop_memberships(self, username):
        self._write("DELETE FROM memberships WHERE username=?", [(username,)])

    def save_message(self, room, seq, sender, text, qos=1, now=None):
        self.upsert_room(room, now)
        self._write("INSERT OR IGNORE INTO messages (room, seq, sender, text, ts, qos) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(room, seq, sender, text, time.time() if now is None else now, qos)])
        self._write("UPDATE rooms SET last_seq=? WHERE name=?", [(seq, room)])

    def sequences(self):
        """room -> last sequence number, so a broker restart never reuses one."""
        return {row["name"]: row["last_seq"] for row in self._rows("SELECT name, last_seq FROM rooms")}

    # ---- read by the UI ------------------------------------------------

    def rooms(self):
        """Newest first, with member count, last message and unread count."""
        return self._rows("""
            SELECT r.name, r.last_seq,
                   (SELECT COUNT(*) FROM memberships m WHERE m.room=r.name) AS members,
                   (SELECT text FROM messages x WHERE x.room=r.name
                     ORDER BY x.id DESC LIMIT 1) AS last_text,
                   (SELECT ts FROM messages x WHERE x.room=r.name
                     ORDER BY x.id DESC LIMIT 1) AS last_ts,
                   MAX(0, r.last_seq - COALESCE(
                       (SELECT last_read_seq FROM room_state s WHERE s.room=r.name), 0)) AS unread
            FROM rooms r
            ORDER BY COALESCE(last_ts, r.created_at) DESC""")

    def history(self, room, limit=200):
        return self._rows("SELECT * FROM messages WHERE room=? ORDER BY id DESC LIMIT ?",
                          (room, limit))[::-1]

    def mark_read(self, room, seq):
        self._write("""INSERT INTO room_state VALUES (?, ?)
                       ON CONFLICT(room) DO UPDATE SET last_read_seq=MAX(last_read_seq, excluded.last_read_seq)""",
                    [(room, seq)])
