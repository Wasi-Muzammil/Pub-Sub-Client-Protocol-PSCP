"""Optional Streamlit chat UI for PSCP: a demo skin over pscp.client.PSCPClient.

Run it with:
    streamlit run tools/streamlit_app.py

This file is the only place that needs a third-party package; the protocol
package in pscp/ stays standard-library only (see requirements-dev.txt).
Streamlit requires Python 3.11+ even though pscp/ supports 3.8+.

Two threads produce data for the page (the client's receiver and timer
threads). Streamlit's session state may only be touched from the script run,
so the client callbacks append to plain thread-safe Buffers and the page
drains them on every rerun.
"""
import atexit
import os
import random
import sys
import threading
import time
from collections import deque

import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pscp.client import ConnectError, NotConnected, PSCPClient  # noqa: E402
from pscp.constants import (  # noqa: E402
    DEFAULT_PORT, PRESENCE_TOPIC, Presence, Status,
)
from pscp.log import setup  # noqa: E402

RUN_EVERY = "0.4s"
HISTORY_LIMIT = 200
MAX_USERNAME = 32
MAX_CLAIMS = 5          # refuse to build a sixth client for one username in CLAIM_WINDOW
CLAIM_WINDOW = 10.0


def _process():
    """Process-wide registry, kept in sys.modules.

    Streamlit re-executes this script on every rerun and after every code edit,
    so both module globals and cache_resource entries can be replaced. sys.modules
    survives the whole process, which is what a socket registry needs.

    Two live clients with one username would take the session over from each other
    forever (both reconnect with RESUME), so a new connection kills the old one
    first - even one orphaned by a browser refresh.
    """
    key = "_pscp_demo_state"
    state = sys.modules.get(key)
    if state is None:
        state = {"clients": {}, "claims": {}}
        sys.modules[key] = state
        atexit.register(lambda: [_safe_close(c) for c in list(state["clients"].values())])
    return state


class Buffer:
    """Small thread-safe queue: the client threads write, the script run reads."""

    def __init__(self, maxlen=1000):
        self._items = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, item):
        with self._lock:
            self._items.append(item)

    def snapshot(self):
        with self._lock:
            return list(self._items)

    def clear(self):
        with self._lock:
            self._items.clear()


def _safe_close(client):
    try:
        client.reconnect = False
        client.close()
    except Exception:  # noqa: BLE001 - best effort on teardown
        pass


def _drop_registered(username):
    """Close whatever client still holds this username in this process.

    A reconnect thread may already be inside _open() when we close, so give it a
    moment to finish and then close again. Without this it can come back to life
    and take the session over from the client we are about to create.
    """
    client = _process()["clients"].pop(username, None)
    if client is None:
        return
    for _ in range(6):
        _safe_close(client)
        if not client.connected.is_set():
            return
        time.sleep(0.05)


def _claim_username(username):
    """Make room for a new client. False means we are creating clients in a loop."""
    registry = _process()
    now = time.monotonic()
    stamps = registry["claims"].setdefault(username, deque())
    while stamps and now - stamps[0] > CLAIM_WINDOW:
        stamps.popleft()
    if len(stamps) >= MAX_CLAIMS:
        return False
    stamps.append(now)
    _drop_registered(username)
    return True


# ---- callbacks (never touch st.session_state from a worker thread) ---------

def _callbacks(messages, presence, events, username):
    def on_message(room, sender, text, seq):
        messages.append({"room": room, "sender": sender, "text": text, "seq": seq,
                         "at": time.strftime("%H:%M:%S")})

    def on_presence(event, user):
        presence.append((event, user))
        events.append(f"{time.strftime('%H:%M:%S')} PRESENCE {event.name.lower()} {user}")

    def on_status(text):
        events.append(f"{time.strftime('%H:%M:%S')} {text}")

    on_message.__name__ = f"on_message_{username}"
    return on_message, on_presence, on_status


# ---- session state --------------------------------------------------------

def _init_state():
    ss = st.session_state
    ss.setdefault("client", None)
    ss.setdefault("messages", Buffer())
    ss.setdefault("presence_events", Buffer())
    ss.setdefault("events", Buffer())
    ss.setdefault("online", set())
    ss.setdefault("host", os.environ.get("PSCP_BROKER", "127.0.0.1"))
    ss.setdefault("port", DEFAULT_PORT)
    ss.setdefault("user", f"web-{random.randrange(16 ** 4):04x}")
    ss.setdefault("room", "room1")
    ss.setdefault("qos", 1)
    ss.setdefault("rtt", None)
    ss.setdefault("notice", None)


def _reset_conversation():
    ss = st.session_state
    ss.messages = Buffer()
    ss.presence_events = Buffer()
    ss.events = Buffer()
    ss.online = set()
    ss.rtt = None


def _connect(host, port, user, resume, qos):
    ss = st.session_state
    user = (user or "").strip()
    if not user:
        ss.notice = "choose a username first"
        return False
    if not _claim_username(user):
        ss.notice = (f"connect loop for '{user}': the broker kept taking this session over. "
                     "Check that no other client or tab is using the same username.")
        return False

    _disconnect()
    _reset_conversation()
    logger = setup(f"web-{user}", "WARNING")
    on_message, on_presence, on_status = _callbacks(ss.messages, ss.presence_events,
                                                    ss.events, user)
    client = PSCPClient(host, user, port, qos=qos, logger=logger, on_message=on_message,
                        on_presence=on_presence, on_status=on_status)
    _process()["clients"][user] = client   # before connect: an aborted script run must
    try:                                   # not leave an untracked client behind
        client.connect(resume=resume)
    except (ConnectError, ConnectionError, OSError) as e:
        _drop_registered(user)
        ss.notice = f"cannot connect: {e}"
        return False

    try:
        status = client.subscribe(PRESENCE_TOPIC)
        if status != Status.OK:
            ss.notice = f"presence subscribe failed: {status.name}"
    except (NotConnected, OSError, TimeoutError) as e:
        # the link can die in the millisecond between CONNACK and SUBSCRIBE
        ss.notice = f"connected, but the link dropped immediately: {e} (retrying in the background)"

    ss.client = client
    return True


def _disconnect():
    ss = st.session_state
    client = ss.client
    if client is not None:
        registry = _process()["clients"]
        if registry.get(client.username) is client:
            del registry[client.username]
        _safe_close(client)
    ss.client = None


def _client_state():
    """none | live | reconnecting | dead."""
    client = st.session_state.client
    if client is None:
        return "none"
    if client.connected.is_set():
        return "live"
    return "reconnecting" if client.reconnect else "dead"


# ---- widgets --------------------------------------------------------------

def _connected():
    client = st.session_state.client
    return client is not None and client.connected.is_set()


def _try_request(call, *args):
    """Run a blocking client request; a dead link must not paint a traceback."""
    try:
        return call(*args)
    except (NotConnected, OSError, TimeoutError) as e:
        st.session_state.notice = f"not connected right now: {e} (the client is reconnecting)"
        return None


def _sidebar():
    ss = st.session_state
    with st.sidebar:
        st.subheader("Connection")
        st.text_input("Broker host", key="host")
        st.number_input("Port", min_value=1, max_value=65535, step=1, key="port")
        st.text_input("Username", key="user", max_chars=MAX_USERNAME)
        st.checkbox("Take over an existing session (RESUME)", key="resume",
                    help="Clears a session left behind by a browser refresh.")
        connected = _connected()
        state = _client_state()
        if state in ("live", "reconnecting"):
            if state == "reconnecting":
                st.caption("link lost - reconnecting in the background")
            if st.button("Disconnect", type="primary", use_container_width=True):
                _disconnect()
                st.rerun()
        else:
            if state == "dead":
                _disconnect()   # another client took this session over
                st.info("this session was taken over by another client")
            if st.button("Connect", type="primary", use_container_width=True):
                if _connect(ss.host, int(ss.port), ss.user, ss.resume, ss.qos):
                    st.rerun()

        if ss.notice:
            st.error(ss.notice)

        st.divider()
        st.subheader("Room")
        st.text_input("Room name", key="room")
        qos = st.radio("QoS for new publishes and joins", (0, 1), horizontal=True,
                       key="qos", disabled=not connected)
        left, right = st.columns(2)
        if left.button("Join", use_container_width=True, disabled=not connected):
            status = _try_request(ss.client.subscribe, ss.room.strip(), qos)
            if status is not None:
                st.toast(f"join {ss.room}: {status.name}")
                if status != Status.OK:
                    ss.notice = f"join {ss.room}: {status.name}"
        if right.button("Leave", use_container_width=True, disabled=not connected):
            status = _try_request(ss.client.unsubscribe, ss.room.strip())
            if status is not None:
                st.toast(f"leave {ss.room}: {status.name}")
                if status != Status.OK:
                    ss.notice = f"leave {ss.room}: {status.name}"

        client = ss.client
        if client is not None:
            client.qos = qos
            rooms = [r for r in client.subscriptions if r != PRESENCE_TOPIC]
            st.caption("joined: " + (", ".join(sorted(rooms)) if rooms else "none yet"))
        st.divider()
        st.caption("PSCP demo skin - the protocol lives in pscp/, this page only renders it.")


def _drain(ss):
    for event, user in ss.presence_events.snapshot():
        if event == Presence.JOIN:
            ss.online.add(user)
        else:
            ss.online.discard(user)
    ss.presence_events.clear()


def _live_panel():
    ss = st.session_state
    _drain(ss)
    left, right = st.columns([3, 1], gap="medium")

    with left:
        box = st.container(height=540)
        with box:
            messages = ss.messages.snapshot()[-HISTORY_LIMIT:]
            if not messages:
                st.caption("No messages yet. Join a room with the sidebar, then type below.")
            for m in messages:
                mine = m["sender"] == ss.user
                with st.chat_message("user" if mine else "assistant"):
                    st.caption(f"{m['sender']} - {m['room']} #{m['seq']} - {m['at']}")
                    st.write(m["text"])

    with right:
        st.markdown("**Online**")
        others = sorted(u for u in ss.online if u != ss.user)
        st.write("you" if ss.user in ss.online else "_not joined_")
        for user in others:
            st.write(f"- {user}")
        if not others and ss.user in ss.online:
            st.caption("nobody else online")

        st.divider()
        client = ss.client
        if client is not None:
            if st.button("Ping broker", use_container_width=True):
                with st.spinner("PING..."):
                    try:
                        ss.rtt = client.ping() * 1000
                    except (TimeoutError, OSError) as e:
                        ss.rtt = None
                        st.warning(str(e))
            if ss.rtt is not None:
                st.metric("RTT", f"{ss.rtt:.1f} ms")
            st.metric("Unacked publishes", client.pending_count())
        else:
            st.caption("not connected")

        with st.expander("Protocol log"):
            lines = ss.events.snapshot()[-40:]
            st.text("\n".join(lines) if lines else "no events yet")


# ---- page -----------------------------------------------------------------

def main():
    st.set_page_config(page_title="PSCP chat", page_icon="💬", layout="wide")
    _init_state()
    ss = st.session_state

    st.title("PSCP chat")
    state = "connected" if _connected() else "not connected"
    st.caption(f"{state} - {ss.user}@{ss.host}:{int(ss.port)} - room {ss.room} - QoS {ss.qos}")

    _sidebar()

    @st.fragment(run_every=RUN_EVERY)
    def live():
        _live_panel()

    live()

    text = st.chat_input(f"Message to {ss.room}" if _connected() else "Connect first")
    if text:
        client = ss.client
        if client is None:
            st.warning("connect first")
        elif not ss.room.strip():
            st.warning("join a room first")
        else:
            try:
                client.publish(ss.room.strip(), text, qos=ss.qos)
            except (NotConnected, OSError) as e:
                st.error(f"not sent: {e}")


if __name__ == "__main__":
    main()
