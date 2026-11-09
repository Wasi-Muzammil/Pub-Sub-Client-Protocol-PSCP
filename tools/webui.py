"""Tiny HTTP bridge for the browser chat UI (standard library only).

A browser cannot open a raw TCP socket, so this process holds one PSCPClient
per username and the page polls it once per second.

    python tools/webui.py                 # http://127.0.0.1:8080

Routes:
    GET  /      the page (tools/web/index.html)
    POST /api   {"action": "state" | "send" | "ping" | "disconnect", ...}

The database is only read here (room previews and history); the broker owns it.
"""
import argparse
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pscp.client import ConnectError, NotConnected, PSCPClient  # noqa: E402
from pscp.constants import DEFAULT_PORT, PRESENCE_TOPIC, Presence  # noqa: E402
from pscp.log import setup  # noqa: E402
from pscp.wire import encode_username, valid_room  # noqa: E402

INDEX = Path(__file__).resolve().parent / "web" / "index.html"
DB = os.environ.get("PSCP_DB", os.path.join("data", "broker.db"))
MAX_BODY = 64 * 1024
MAX_TEXT = 4000
DIRECTORY_EVERY = 3.0
HISTORY_LIMIT = 200
IDLE_LIMIT = 30.0      # a page polls once per second; 30 s of silence means it is gone
SWEEP_EVERY = 10.0

log = setup("web", "WARNING")
_sessions = {}
_lock = threading.Lock()


class Session:
    """One PSCP connection plus what the page needs to render it."""

    def __init__(self, user, host, port):
        self.user, self.host, self.port = user, host, port
        self.client = None
        self.events = deque(maxlen=500)   # (no, room, sender, text, seq, ts)
        self.online = set()
        self.members = {}                 # room -> subscriber count
        self.rooms_at = 0.0
        self.ready = threading.Event()
        self.error = None
        self.last_seen = time.time()
        self._count = 0

    def start(self):
        try:
            # Fail fast on a dead broker: connect() retries with back-off forever.
            socket.create_connection((self.host, self.port), timeout=3.0).close()
            self.client = PSCPClient(self.host, self.user, self.port, logger=log,
                                     on_message=self._message, on_presence=self._presence)
            self.client.connect()
        except (OSError, ConnectError, NotConnected, TimeoutError) as e:
            self.error = str(e) or e.__class__.__name__
            self.ready.set()
            return
        try:
            self.client.subscribe(PRESENCE_TOPIC)
        except (OSError, NotConnected, TimeoutError):
            pass
        self.ready.set()

    def _message(self, room, sender, text, seq):
        self._count += 1
        self.events.append((self._count, room, sender, text, seq, time.time()))

    def _presence(self, event, user):
        if event == Presence.JOIN:
            self.online.add(user)
        else:
            self.online.discard(user)

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:   # a broken socket must not block shutdown
                pass


# ---- SQLite (read-only mirror written by the broker) -----------------------

def _query(sql, args=()):
    if not os.path.exists(DB):
        return []
    con = None
    try:
        con = sqlite3.connect(Path(DB).resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)
        return con.execute(sql, args).fetchall()
    except sqlite3.Error as e:
        log.warning("DB read failed (%s): %s", DB, e)
        return []
    finally:
        if con is not None:
            con.close()


_PREVIEW = """
SELECT r.name, r.last_seq,
       (SELECT m.text FROM messages m WHERE m.room = r.name ORDER BY m.id DESC LIMIT 1),
       (SELECT m.ts   FROM messages m WHERE m.room = r.name ORDER BY m.id DESC LIMIT 1)
FROM rooms r"""

_HISTORY = "SELECT seq, sender, text, ts FROM messages WHERE room = ? ORDER BY id DESC LIMIT ?"


# ---- state assembly --------------------------------------------------------

def _link(session):
    if session.client is None:
        return "offline"
    return "live" if session.client.connected.is_set() else "reconnecting"


def _directory(session):
    if _link(session) != "live" or time.monotonic() - session.rooms_at < DIRECTORY_EVERY:
        return
    session.rooms_at = time.monotonic()
    try:
        session.members = dict(session.client.list_rooms(timeout=2.0))
    except (NotConnected, OSError, TimeoutError):
        pass


def _ensure_room(session, room):
    if not room or _link(session) != "live" or room in session.client.subscriptions:
        return
    try:
        session.client.subscribe(room, 1, timeout=2.0)
    except (NotConnected, OSError, TimeoutError):
        pass


def _rooms(session):
    rooms = {name: {"name": name, "members": count, "last_text": None,
                    "last_ts": None, "last_seq": 0}
             for name, count in session.members.items() if valid_room(name)}
    for name, last_seq, text, ts in _query(_PREVIEW):
        row = rooms.setdefault(name, {"name": name, "members": 0, "last_text": None,
                                      "last_ts": None, "last_seq": 0})
        row["last_seq"], row["last_text"], row["last_ts"] = last_seq or 0, text, ts
    return sorted(rooms.values(), key=lambda r: (-(r["last_ts"] or 0), r["name"]))


def _messages(session, room):
    rows = [{"seq": seq, "sender": sender, "text": text, "ts": ts}
            for seq, sender, text, ts in reversed(_query(_HISTORY, (room, HISTORY_LIMIT)))]
    seen = {(row["seq"], row["sender"], row["text"]) for row in rows if row["seq"]}
    for _, event_room, sender, text, seq, ts in list(session.events):
        if event_room != room or (seq and (seq, sender, text) in seen):
            continue
        rows.append({"seq": seq, "sender": sender, "text": text, "ts": ts})
    rows.sort(key=lambda m: (m["ts"] or 0, m["seq"]))
    return rows[-HISTORY_LIMIT:]


def _session(body):
    user = str(body.get("user") or "").strip()
    try:
        encode_username(user)
    except ValueError:
        raise ValueError("username must be 1-32 bytes")
    host = str(body.get("host") or "127.0.0.1").strip()
    try:
        port = int(body.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        raise ValueError("bad port")
    if not 0 < port < 65536:
        raise ValueError("bad port")

    with _lock:
        session = _sessions.get(user)
        fresh = session is None
        if fresh:
            session = _sessions[user] = Session(user, host, port)
    if fresh:
        session.start()
    if not session.ready.wait(15.0):
        _drop(user)
        raise TimeoutError("broker did not answer")
    if session.error:
        _drop(user)
        raise ValueError(session.error)
    session.last_seen = time.time()
    return session


def _drop(user):
    with _lock:
        session = _sessions.pop(user, None)
    if session is not None:
        session.close()


def _sweep_once():
    """Close sessions whose page stopped polling (closed tab, crashed browser)."""
    now = time.time()
    with _lock:
        stale = [user for user, session in _sessions.items() if now - session.last_seen > IDLE_LIMIT]
    for user in stale:
        log.info("SESSION %s idle for %gs: closing", user, IDLE_LIMIT)
        _drop(user)


def _sweep():
    while True:
        time.sleep(SWEEP_EVERY)
        _sweep_once()


# ---- actions ---------------------------------------------------------------

def _state(body):
    session = _session(body)
    room = str(body.get("room") or "")
    if not valid_room(room):
        room = ""
    _directory(session)
    _ensure_room(session, room)
    return {"ok": True, "status": _link(session), "user": session.user,
            "online": any(user != session.user for user in tuple(session.online)),
            "rooms": _rooms(session),
            "messages": _messages(session, room) if room else []}


def _send(body):
    session = _session(body)
    room = str(body.get("room") or "")
    text = str(body.get("text") or "")[:MAX_TEXT].strip()
    if not valid_room(room) or not text:
        raise ValueError("nothing to send")
    if _link(session) != "live":
        raise ValueError("not connected")
    _ensure_room(session, room)
    session.client.publish(room, text, qos=1)
    return {"ok": True}


def _ping(body):
    session = _session(body)
    if _link(session) != "live":
        raise ValueError("not connected")
    return {"ok": True, "rtt": session.client.ping(timeout=5.0)}


def _api(body):
    action = body.get("action")
    try:
        if action == "state":
            return _state(body)
        if action == "send":
            return _send(body)
        if action == "ping":
            return _ping(body)
        if action == "disconnect":
            _drop(str(body.get("user") or "").strip())
            return {"ok": True}
        raise ValueError("unknown action")
    except (ValueError, TimeoutError, OSError, NotConnected, ConnectError) as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__}


# ---- HTTP ------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self):
        if self.path == "/favicon.ico":
            return self._send(204, "image/x-icon", b"")
        if self.path in ("/", "/index.html"):
            try:
                body = INDEX.read_bytes()
            except OSError:
                return self._send(500, "text/plain; charset=utf-8", b"index.html is missing")
            return self._send(200, "text/html; charset=utf-8", body)
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        if self.path != "/api":
            return self._send(404, "text/plain; charset=utf-8", b"not found")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            return self._json(413, {"ok": False, "error": "body too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"ok": False, "error": "bad JSON"})
        self._json(200, _api(body))

    def _json(self, code, payload):
        self._send(code, "application/json", json.dumps(payload).encode())

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass   # the browser closed the tab mid-response


def main():
    parser = argparse.ArgumentParser(description="browser chat for PSCP")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (keep it local)")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    threading.Thread(target=_sweep, name="session-sweep", daemon=True).start()
    print(f"web UI on http://{args.host}:{args.port}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with _lock:
            users = list(_sessions)
        for user in users:
            _drop(user)


if __name__ == "__main__":
    main()
