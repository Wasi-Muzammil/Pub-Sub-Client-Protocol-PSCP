"""PSCP Chat: WhatsApp-style Streamlit client.

    streamlit run webclient/app.py
Environment: PSCP_DATA_DIR (default ~/.pscp), PSCP_BROKER (default broker host for new profiles).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from pscp.constants import DEFAULT_PORT  # noqa: E402
from pscp.log import setup  # noqa: E402
from webclient import profiles, ui  # noqa: E402
from webclient.service import ChatService, ping_stats  # noqa: E402
from webclient.store import Store  # noqa: E402

DATA_DIR = Path(os.environ.get("PSCP_DATA_DIR", Path.home() / ".pscp"))
DEFAULT_BROKER = os.environ.get("PSCP_BROKER", "127.0.0.1")
EMOJIS = ["🙂", "😎", "🦊", "🐼", "🚀", "🎧", "📚", "⚽", "🌸", "🔥", "💻", "🍕"]

st.set_page_config(page_title="PSCP Chat", page_icon="💬", layout="wide")
st.html(ui.CSS)  # follows the active Streamlit theme by itself (CSS light-dark)


def db_path(username):
    return DATA_DIR / f"{username}.sqlite3"


@st.cache_resource(show_spinner=False)
def get_service(path):
    """One live PSCP connection per profile, shared by every rerun and browser tab."""
    store = Store(path)
    user = store.get_profile()["username"]
    logger = setup(f"web-{user}", "INFO", str(Path(path).with_suffix(".log")), console_level="WARNING")
    return ChatService(store, logger=logger).start()


# ---- profile picker / first run ---------------------------------------------------------

def avatar_inputs(key, current=""):
    """Returns a function that resolves the chosen avatar once the form is submitted."""
    emoji_default = current if current in EMOJIS else EMOJIS[0]
    emoji = st.selectbox("Avatar", EMOJIS, index=EMOJIS.index(emoji_default), key=f"{key}-emoji")
    photo = st.file_uploader("…or a photo (optional)", type=["png", "jpg", "jpeg"], key=f"{key}-photo")
    keep = profiles.is_image(current) and st.checkbox("Keep current photo", value=True, key=f"{key}-keep")

    def resolve():
        if photo is not None:
            try:
                return profiles.avatar_from_upload(photo.getvalue())
            except Exception:  # noqa: BLE001 - any unreadable image falls back to the emoji
                st.warning("Could not read that image; using the emoji instead.")
        return current if keep else emoji
    return resolve


def login_page():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    st.markdown("## 💬 PSCP Chat")
    st.caption(f"Profiles and chats are stored locally in `{DATA_DIR}`.")
    existing = sorted(p.stem for p in DATA_DIR.glob("*.sqlite3"))
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Continue as")
        if not existing:
            st.info("No profiles on this device yet.")
        for name in existing:
            if st.button(f"👤 {name}", key=f"use-{name}", use_container_width=True):
                st.session_state.user = name
                st.rerun()
    with right:
        st.markdown("#### Create a profile")
        with st.form("create"):
            username = st.text_input("Username (unique on the broker)", max_chars=32,
                                     help="Letters, digits, '.', '_' or '-'")
            display = st.text_input("Display name", max_chars=profiles.MAX_NAME)
            about = st.text_input("About", value="Hey there! I am using PSCP Chat.", max_chars=profiles.MAX_ABOUT)
            avatar = avatar_inputs("new")
            c1, c2 = st.columns([3, 1])
            host = c1.text_input("Broker host / IP", value=DEFAULT_BROKER)
            port = c2.number_input("Port", value=DEFAULT_PORT, min_value=1, max_value=65535)
            if st.form_submit_button("Create profile", type="primary"):
                if not profiles.USERNAME_RE.match(username):
                    st.error("Username: 1-32 letters, digits, '.', '_' or '-'.")
                elif db_path(username).exists():
                    st.error("A profile with that username already exists on this device.")
                elif not host.strip():
                    st.error("Enter the broker's host or IP.")
                else:
                    store = Store(db_path(username))
                    store.save_profile(username, display.strip() or username, about.strip(),
                                       avatar(), host.strip(), int(port))
                    store.close()
                    st.session_state.user = username
                    st.rerun()


# ---- main chat UI -----------------------------------------------------------------------

@st.dialog(" ", width="medium")
def profile_dialog(svc):
    p = svc.profile
    st.html(ui.modal_hero_html("Profile & settings", f"@{p['username']} · stored only on this device",
                               [(ui.avatar_circle(p["avatar"], p["username"]), p["display_name"], p["about"])]))
    with st.form("edit-profile", border=False):
        display = st.text_input("Display name", value=p["display_name"], max_chars=profiles.MAX_NAME)
        about = st.text_input("About", value=p["about"], max_chars=profiles.MAX_ABOUT)
        avatar = avatar_inputs("edit", p["avatar"])
        host = st.text_input("Broker host / IP", value=p["broker_host"],
                             help="Takes effect after 'Switch profile' and signing in again")
        if st.form_submit_button("Save", type="primary", use_container_width=True):
            svc.update_profile(display.strip() or p["username"], about.strip(), avatar(), broker_host=host.strip())
            st.rerun()  # closes the dialog
    if st.button("↩️ Switch profile", use_container_width=True):
        svc.stop()
        get_service.clear()
        for key in ("user", "room", "ping_result"):
            st.session_state.pop(key, None)
        st.rerun()


@st.dialog(" ", width="medium")
def ping_dialog(svc):
    p = svc.profile
    rtts = st.session_state.get("ping_result")
    s = ping_stats(rtts) if rtts else None

    def ms(v):
        return "—" if v is None else f"{v:.1f} ms"
    circles = [
        (ui.ICONS["timer"], "Average", ms(s["avg"]) if s else "—"),
        (ui.ICONS["signal"], "Min / Max",
         f"{s['min']:.0f} / {s['max']:.0f} ms" if s and s["min"] is not None else "—"),
        (ui.ICONS["package"], "Loss", f"{s['loss']:.0f} % of {s['sent']}" if s else "—"),
    ]
    st.html(ui.modal_hero_html("Ping the Broker", f"Round-trip time to {p['broker_host']}:{p['broker_port']}",
                               circles))
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    count = c1.number_input("Number of pings", min_value=1, max_value=20, value=5)
    if c2.button("Ping!", type="primary", use_container_width=True, disabled=not svc.online):
        with st.spinner(f"Sending {count} PING(s)…"):
            st.session_state.ping_result = svc.ping(int(count))
        st.rerun(scope="fragment")  # redraw only the dialog with the new numbers
    if not svc.online:
        st.caption("⚠️ Not connected to the broker right now. Ping is available once the connection is back.")
    st.html('<div class="wa-hero-foot">PING → PONG over your live PSCP TCP connection '
            '(the same as /ping in the terminal client).</div>')


def sidebar(svc):
    me, gear = st.columns([3, 1], vertical_alignment="center")
    me.html(ui.me_html(svc.profile, svc.state))
    with gear.popover("", icon=":material/settings:", help="Settings", use_container_width=True):
        if st.button("👤 Profile & settings", use_container_width=True):
            profile_dialog(svc)
        if st.button("📶 Ping broker", use_container_width=True):
            ping_dialog(svc)
    if svc.state != "online":
        p = svc.profile
        detail = f" ({svc.last_error})" if svc.last_error else ""
        st.html(f'<div class="wa-banner">Connecting to {ui.esc(p["broker_host"])}:{p["broker_port"]}{ui.esc(detail)}. '
                'New messages wait in your outbox 🕓 and are sent once the connection is back.</div>')

    with st.form("new-group", clear_on_submit=True, border=False):
        c1, c2 = st.columns([4, 1])
        name = c1.text_input("New group", placeholder="New group / room name", label_visibility="collapsed")
        if c2.form_submit_button("➕") and name:
            error = svc.join(name.strip())
            if error:
                st.error(error)
            else:
                st.session_state.room = name.strip()
                st.rerun()

    chat_list(svc)


@st.fragment(run_every="2s")
def chat_list(svc):
    contacts = svc.store.contacts()
    rooms = svc.store.rooms()
    if not rooms:
        st.caption("No groups yet. Create or join one above; anyone who joins the same name is in it.")
    for r in rooms:
        members = [u for u in svc.store.room_members(r["name"]) if u != svc.username]
        online = sum(1 for u in members if (contacts.get(u) or {}).get("online"))
        label = f"👥 {r['name']}" + (f"  ·  🟢 {online}" if online else "")
        selected = st.session_state.get("room") == r["name"]
        if st.button(label, key=f"room-{r['name']}", use_container_width=True,
                     type="primary" if selected else "secondary"):
            st.session_state.room = r["name"]
            st.rerun()


@st.fragment(run_every="1s")
def chat_view(svc, room):
    contacts = svc.store.contacts()
    with st.container(key="wa_card"):  # header + messages as one card (styled via .st-key-wa_card)
        with st.container(key="wa_head"):
            head, menu = st.columns([20, 1], vertical_alignment="center")
            head.html(ui.header_html(room, svc.store.room_members(room), contacts, svc.username))
            with menu.popover("⋮"):
                if st.button("🚪 Leave group", key="leave"):
                    svc.leave(room)
                    st.session_state.pop("room", None)
                    st.rerun()
                if st.button("🗑️ Leave and delete chat", key="delete"):
                    svc.leave(room)
                    svc.store.delete_room(room)
                    st.session_state.pop("room", None)
                    st.rerun()
        st.html(ui.chat_html(svc.store.messages(room), svc.username, contacts))


def chat_page(user):
    path = db_path(user)
    if not path.exists():
        st.session_state.pop("user", None)
        st.rerun()
    svc = get_service(str(path))
    with st.sidebar:
        sidebar(svc)

    room = st.session_state.get("room")
    if room and room not in svc.store.active_room_names():
        room = None
    if not room:
        st.html('<div class="wa-empty"><h2>PSCP Chat</h2><p>Pick a group on the left, or create one.<br>'
                'Rooms are groups: everyone who joins the same name chats together.</p></div>')
        return
    text = st.chat_input(f"Message {room}")
    if text and text.strip():
        svc.send(room, text.strip())
    chat_view(svc, room)


if "user" in st.session_state:
    chat_page(st.session_state.user)
else:
    login_page()
