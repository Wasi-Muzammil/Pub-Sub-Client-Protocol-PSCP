"""WhatsApp-like presentation: CSS and HTML builders.

Every string that came from a user or the network goes through html.escape;
avatars are only embedded as images after profiles.clean_avatar validated them.
"""
import hashlib
import html
import time
import urllib.parse
from datetime import datetime

from . import profiles

GREEN = "#00a884"
NAME_COLORS = ["#1f7aec", "#e542a3", "#02a698", "#fc6c3f", "#7f66ff", "#d4a106", "#25a244", "#c63b3b"]

# WhatsApp palette as light-dark(light, dark) pairs. Streamlit sets `color-scheme` on .stApp to the
# theme it is actually drawing, so the browser picks the right side, and a theme switch applies at once.
# Frame, header and chat background are translucent tints so they sit on any theme background.
CSS = """
.stApp {
  --wa-border: light-dark(rgba(49,51,63,.14), rgba(250,250,250,.10));
  --wa-head: light-dark(rgba(49,51,63,.04), rgba(250,250,250,.04));
  --wa-chat: light-dark(rgba(239,234,226,.55), rgba(255,255,255,.025));
  --wa-out: light-dark(#d9fdd3, #005c4b);
  --wa-in: light-dark(#ffffff, #202c33);
  --wa-ink: light-dark(#111b21, #e9edef);
  --wa-muted: light-dark(#667781, #8696a0);
  --wa-meta-out: light-dark(#667781, rgba(233,237,239,.6));
  --wa-chip: light-dark(#ffffff, #202c33);
  --wa-note-bg: light-dark(#fff5c4, rgba(255,210,121,.10));
  --wa-note-ink: light-dark(#54656f, #ffd279);
  --wa-avbg: light-dark(#dfe5e7, #2a3942);
  --wa-shadow: light-dark(rgba(11,20,26,.13), rgba(0,0,0,.35));
  --wa-banner-bg: light-dark(#fff3cd, rgba(255,193,7,.12));
  --wa-banner-ink: light-dark(#664d03, #ffd279);
}
/* clear Streamlit's fixed top bar (~3.75rem) so titles are not hidden behind it */
/* bottom: Streamlit already reserves room for st.chat_input; extra padding would make the page scroll */
.block-container { padding-top: 4rem; padding-bottom: 1rem; }
header[data-testid="stHeader"] { background: transparent; }
/* header + chat form one card: Streamlit containers keyed wa_card / wa_head get class st-key-KEY.
   Never write a less-than sign followed by a letter in this CSS: Streamlit's HTML sanitiser drops the whole style block. */
.st-key-wa_card { gap:0 !important; border:1px solid var(--wa-border); border-radius:12px; overflow:hidden; }
.st-key-wa_head { background:var(--wa-head); padding:6px 12px 6px 14px; border-bottom:1px solid var(--wa-border); }
.st-key-wa_head button { border:none; background:transparent; }
.wa-head { display:flex; align-items:center; gap:12px; padding:2px 0; }
.wa-head .title { font-weight:600; font-size:1.05rem; }
.wa-head .sub { color:var(--wa-muted); font-size:.82rem; }
.wa-chat { background:var(--wa-chat); height:calc(100vh - 290px); min-height:300px; overflow-y:auto;
           display:flex; flex-direction:column-reverse; padding:10px 6%; }
.wa-msgs { display:flex; flex-direction:column; gap:2px; }
.wa-row { display:flex; margin:1px 0; }
.wa-row.out { justify-content:flex-end; }
.wa-bubble { max-width:68%; padding:6px 9px 4px 9px; border-radius:8px; color:var(--wa-ink);
             box-shadow:0 1px .5px var(--wa-shadow); font-size:.93rem; line-height:1.35; word-wrap:break-word; }
.wa-row.out .wa-bubble { background:var(--wa-out); }
.wa-row.in .wa-bubble { background:var(--wa-in); }
.wa-row.first.out .wa-bubble { border-top-right-radius:0; }
.wa-row.first.in .wa-bubble { border-top-left-radius:0; }
.wa-row.first { margin-top:7px; }
.wa-sender { font-size:.8rem; font-weight:600; margin-bottom:1px; }
.wa-meta { float:right; margin:6px 0 -3px 10px; font-size:.68rem; color:var(--wa-muted); white-space:nowrap; }
.wa-row.out .wa-meta { color:var(--wa-meta-out); }
.wa-tick { margin-left:3px; }
.wa-tick.fail { color:#e5534b; }
.wa-day { align-self:center; background:var(--wa-chip); color:var(--wa-muted); font-size:.75rem; padding:4px 10px;
          border-radius:7px; margin:10px 0 4px 0; box-shadow:0 1px .5px var(--wa-shadow); }
.wa-note { align-self:center; background:var(--wa-note-bg); color:var(--wa-note-ink); font-size:.75rem;
           padding:5px 12px; border-radius:7px; margin:8px 0; text-align:center; max-width:80%; }
.wa-av { border-radius:50%; display:inline-flex; align-items:center; justify-content:center; flex:none;
         color:#fff; font-weight:600; overflow:hidden; }
.wa-av img { width:100%; height:100%; object-fit:cover; }
.wa-me { display:flex; align-items:center; gap:10px; margin-bottom:6px; }
.wa-me .name { font-weight:600; } .wa-me .sub { color:var(--wa-muted); font-size:.8rem; }
.wa-dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
.wa-banner { background:var(--wa-banner-bg); color:var(--wa-banner-ink); padding:7px 12px; border-radius:8px;
             font-size:.85rem; margin-bottom:8px; }
.wa-empty { text-align:center; color:var(--wa-muted); padding:18vh 10% 0 10%; }
.wa-empty h2 { color:inherit; font-weight:300; opacity:.85; }
/* modal windows (st.dialog): banner header styled after the newsletter-modal reference.
   The dialog is mounted outside .stApp, so it gets its own muted colour derived from its text colour. */
[data-testid="stDialog"] { --wa-muted: color-mix(in srgb, currentColor 62%, transparent); }
[data-testid="stDialog"] [role="dialog"] { border-radius:14px; overflow:hidden; }
.wa-hero { margin:0 0 .4rem 0; }
.wa-hero-top { color:#fff; text-align:center; padding:30px 20px 58px 20px; border-radius:10px;
  background: radial-gradient(rgba(255,255,255,.08) 1px, transparent 1.6px) 0 0 / 14px 14px,
              linear-gradient(135deg, #0b3b45 0%, #075e54 100%); }
.wa-hero-top .t { font-size:1.65rem; font-weight:600; letter-spacing:.2px; }
.wa-hero-top .s { opacity:.85; font-size:.88rem; margin-top:6px; }
.wa-hero-row { display:flex; justify-content:space-around; margin-top:-44px; }
.wa-hero-c { width:31%; text-align:center; }
.wa-hero-c:only-child { width:80%; }
.wa-hero-ic { width:84px; height:84px; border-radius:50%; background:#3a9ad9; color:#fff; display:inline-flex;
  align-items:center; justify-content:center; box-shadow:0 3px 10px rgba(0,0,0,.25); overflow:hidden; font-size:38px; }
.wa-hero-ic img { width:100%; height:100%; object-fit:cover; }
.wa-i { width:40px; height:40px; background:#fff; mask:center / 40px no-repeat; -webkit-mask:center / 40px no-repeat; }
.wa-hero-lbl { font-weight:600; margin-top:8px; font-size:.92rem; }
.wa-hero-val { color:var(--wa-muted); font-size:1.05rem; margin-top:2px; min-height:1.4em; }
.wa-hero-foot { text-align:center; color:var(--wa-muted); font-size:.74rem; margin-top:.4rem; }
[data-testid="stDialog"] button[kind^="primary"] { background:#f26b3a; border-color:#f26b3a; color:#fff; }
[data-testid="stDialog"] button[kind^="primary"]:hover { background:#e05a2a; border-color:#e05a2a; color:#fff; }
"""

# White line icons for the modal circles, drawn as CSS masks: st.html strips inline SVG, and the
# percent-encoding keeps any raw less-than sign out of the style block (the sanitiser would drop it).
_ICON_SVG = {
    "timer": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6M12 2v3"/>',
    "signal": '<path d="M4 20v-3M9 20v-7M14 20V9M19 20V4"/>',
    "package": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
}


def _icon_css(name, body):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="black" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    uri = "data:image/svg+xml," + urllib.parse.quote(svg, safe="")
    return f'.wa-i-{name} {{ mask-image:url("{uri}"); -webkit-mask-image:url("{uri}"); }}\n'


CSS = "<style>" + CSS + "".join(_icon_css(n, b) for n, b in _ICON_SVG.items()) + "</style>"
ICONS = {name: f'<span class="wa-i wa-i-{name}"></span>' for name in _ICON_SVG}


def esc(text):
    return html.escape(text or "", quote=True)


def color_for(username):
    return NAME_COLORS[int(hashlib.md5(username.encode()).hexdigest(), 16) % len(NAME_COLORS)]


def avatar_html(avatar, username, size=40):
    style = f"width:{size}px;height:{size}px;font-size:{int(size * 0.45)}px;"
    if profiles.is_image(avatar):
        return f'<span class="wa-av" style="{style}"><img src="{esc(avatar)}" alt=""></span>'
    if avatar:
        return f'<span class="wa-av" style="{style}background:var(--wa-avbg);">{esc(avatar)}</span>'
    initial = esc(username[:1].upper() or "?")
    return f'<span class="wa-av" style="{style}background:{color_for(username)};">{initial}</span>'


def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def fmt_day(ts):
    d = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    if d == today:
        return "Today"
    if (today - d).days == 1:
        return "Yesterday"
    return d.strftime("%d %B %Y")


def last_seen(ts):
    if not ts:
        return "offline"
    if time.time() - ts < 60:
        return "last seen just now"
    day = fmt_day(ts)
    return f"last seen {day.lower() if day in ('Today', 'Yesterday') else 'on ' + day} at {fmt_time(ts)}"


TICKS = {
    "queued": ('<span class="wa-tick" title="Waiting for connection">🕓</span>'),
    "sending": ('<span class="wa-tick" title="Sent, waiting for broker PUBACK">🕓</span>'),
    "sent": ('<span class="wa-tick" title="Broker acknowledged (PUBACK)">✓</span>'),
    # grey, not WhatsApp-blue: PSCP has no read receipts, only "the broker fanned it out to the room"
    "delivered": ('<span class="wa-tick" title="Delivered to the room (own copy returned)">✓✓</span>'),
    "failed": ('<span class="wa-tick fail" title="Rejected by the broker">⚠</span>'),
}


def display_name(username, contacts, me=None):
    if username == me:
        return "You"
    c = contacts.get(username) or {}
    return c.get("display_name") or username


def chat_html(messages, me, contacts):
    """Message list in a column-reverse box, so the view stays pinned to the newest message."""
    parts, last_day, last_sender = [], None, None
    parts.append('<div class="wa-note">🔒 Messages are stored only on this device. The PSCP broker keeps no '
                 'history: anything sent while your app is closed for more than 2 minutes is not delivered.</div>')
    for m in messages:
        day = fmt_day(m["created_at"])
        if day != last_day:
            parts.append(f'<div class="wa-day">{esc(day)}</div>')
            last_day, last_sender = day, None
        out = m["direction"] == "out"
        first = m["sender"] != last_sender
        last_sender = m["sender"]
        sender = ""
        if not out and first:
            sender = (f'<div class="wa-sender" style="color:{color_for(m["sender"])}">'
                      f'{esc(display_name(m["sender"], contacts))}</div>')
        tick = TICKS.get(m["status"], "") if out else ""
        text = esc(m["text"]).replace("\n", "<br>")
        parts.append(f'<div class="wa-row {"out" if out else "in"}{" first" if first else ""}">'
                     f'<div class="wa-bubble">{sender}{text}'
                     f'<span class="wa-meta">{fmt_time(m["created_at"])}{tick}</span></div></div>')
    return f'<div class="wa-chat"><div class="wa-msgs">{"".join(parts)}</div></div>'


def header_html(room, members, contacts, me):
    others = [u for u in members if u != me]
    online = [u for u in others if (contacts.get(u) or {}).get("online")]
    if not others:
        sub = "no other members seen yet"
    elif len(others) == 1:
        c = contacts.get(others[0]) or {}
        sub = f"{esc(display_name(others[0], contacts))} · " + ("online" if c.get("online") else last_seen(c.get("last_seen")))
    else:
        names = ", ".join(esc(display_name(u, contacts)) for u in others[:6])
        sub = f"{names} · {len(online)} online"
    return (f'<div class="wa-head">{avatar_html("👥", room, 40)}<div><div class="title">{esc(room)}</div>'
            f'<div class="sub">{sub}</div></div></div>')


def modal_hero_html(title, subtitle, circles):
    """Banner + overlapping circles. circles: [(icon_html, label, value)]; icon_html is trusted markup
    (ICONS or avatar_html output); label/value are escaped here."""
    row = "".join(
        f'<div class="wa-hero-c"><div class="wa-hero-ic">{icon}</div>'
        f'<div class="wa-hero-lbl">{esc(label)}</div><div class="wa-hero-val">{esc(value)}</div></div>'
        for icon, label, value in circles)
    return (f'<div class="wa-hero"><div class="wa-hero-top"><div class="t">{esc(title)}</div>'
            f'<div class="s">{esc(subtitle)}</div></div><div class="wa-hero-row">{row}</div></div>')


def avatar_circle(avatar, username):
    """Avatar content for a modal circle: photo, emoji, or initial on the circle's blue background."""
    if profiles.is_image(avatar):
        return f'<img src="{esc(avatar)}" alt="">'
    return esc(avatar) if avatar else esc(username[:1].upper() or "?")


def me_html(profile, state):
    color = {"online": GREEN, "connecting": "#f0b400"}.get(state, "#d33")
    return (f'<div class="wa-me">{avatar_html(profile["avatar"], profile["username"], 46)}<div>'
            f'<div class="name">{esc(profile["display_name"])}</div>'
            f'<div class="sub"><span class="wa-dot" style="background:{color}"></span>{esc(state)} · '
            f'@{esc(profile["username"])}</div></div></div>')
