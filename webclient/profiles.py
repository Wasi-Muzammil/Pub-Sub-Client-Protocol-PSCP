"""Profile sharing: an application convention on top of PSCP (not part of the protocol).

Profiles travel as JSON text in normal PUBLISH messages on the hidden room `_profiles`:
    {"v":1,"kind":"profile","display_name":"...","about":"...","avatar":"..."}
    {"v":1,"kind":"who"}          -> everyone online answers with their profile
The broker stores nothing, so the `who` request is how a late joiner learns existing profiles.
"""
import base64
import io
import json
import re

PROFILE_ROOM = "_profiles"
MAX_NAME = 40
MAX_ABOUT = 140
AVATAR_PX = 96
_DATA_URI = re.compile(r"^data:image/jpeg;base64,[A-Za-z0-9+/=]{1,60000}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def encode_profile(display_name, about, avatar):
    return json.dumps({"v": 1, "kind": "profile", "display_name": display_name[:MAX_NAME],
                       "about": about[:MAX_ABOUT], "avatar": avatar}, separators=(",", ":"))


def encode_who():
    return json.dumps({"v": 1, "kind": "who"}, separators=(",", ":"))


def decode(text):
    """Return a validated dict, or None for anything malformed (it came from the network)."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("v") != 1 or obj.get("kind") not in ("profile", "who"):
        return None
    if obj["kind"] == "who":
        return {"kind": "who"}
    name, about, avatar = obj.get("display_name"), obj.get("about"), obj.get("avatar")
    if not all(isinstance(x, str) for x in (name, about, avatar)):
        return None
    return {"kind": "profile", "display_name": name[:MAX_NAME], "about": about[:MAX_ABOUT],
            "avatar": clean_avatar(avatar)}


def clean_avatar(avatar):
    """An avatar is either a short emoji/text or a small JPEG data URI; anything else is dropped."""
    if _DATA_URI.match(avatar):
        return avatar
    return avatar[:8] if not avatar.startswith("data:") else ""


def is_image(avatar):
    return bool(avatar) and avatar.startswith("data:image/jpeg;base64,")


def avatar_from_upload(data):
    """Square-crop and shrink an uploaded picture to a ~5 KB JPEG data URI (Pillow ships with Streamlit)."""
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((AVATAR_PX, AVATAR_PX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def valid_room_name(name):
    """User rooms: 1-64 chars, no leading '_' (app-reserved) or '$' (PSCP-reserved)."""
    return 1 <= len(name) <= 64 and not name.startswith(("_", "$")) and name.strip() == name
