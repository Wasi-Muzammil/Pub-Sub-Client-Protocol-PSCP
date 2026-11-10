# PSCP Chat: WhatsApp-style web client

A Streamlit front-end on top of `PSCPClient` (`pscp/client/api.py`). Each user runs it **on their own laptop**. Everything the protocol does not store (profile, joined groups, chat history, other people's profiles) is kept in a **local SQLite file**.

## Run it
```bash
pip install -r webclient/requirements.txt         # streamlit>=1.37
streamlit run webclient/app.py                    # opens http://localhost:8501
```
On first start, create a profile: username, display name, about, avatar (an emoji or a photo), and the broker host/port, for example the Oracle VM's public IP and 5050. Next time, pick the profile under **Continue as**.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PSCP_DATA_DIR` | `~/.pscp` | Where `<username>.sqlite3` and `<username>.log` are stored |
| `PSCP_BROKER` | `127.0.0.1` | Broker host filled in on the create-profile form |

**Two users on one laptop** (for testing): run two instances with different data folders.
```bash
PSCP_DATA_DIR=./tmpA streamlit run webclient/app.py
PSCP_DATA_DIR=./tmpB streamlit run webclient/app.py --server.port 8502
```
On Windows PowerShell, use `$env:PSCP_DATA_DIR="./tmpA"; streamlit run webclient/app.py`.

## What you see
- **Groups = PSCP rooms.** Type a name under *New group*. Anyone who joins the same name is in the group, even if it is just two people. The chat list is sorted by most recent activity.
- **Message ticks** show QoS 1 in action:

  | Tick | Meaning |
  | --- | --- |
  | 🕓 | Queued (offline) or sent but not yet acknowledged |
  | ✓ | The broker sent PUBACK |
  | ✓✓ | Our own copy came back through the room, meaning the broker has passed it to every member |
  | ⚠ | Rejected by the broker |

  There are no blue ticks, because PSCP has no read receipts.
- **Presence:** green dots in the chat list, and "online / last seen …" in the group header. These come from PRESENCE JOIN/LEAVE/TIMEOUT.
- **Offline outbox:** a message written while disconnected is saved as 🕓 and sent automatically once the connection is back, even after restarting the app.
- **Profiles:** other members' display names, about text and avatars. See *Profile convention* below.
- **⚙️ menu (next to your name in the sidebar)** opens two modal windows:
  - **Profile & settings:** edit your display name, about text, avatar and broker host, or **Switch profile**. Saving publishes the new profile to everyone online.
  - **Ping broker:** sends 1–20 PSCP `PING`s over the live TCP connection and shows the **average** round-trip time, **min / max** and **loss**. It is the web version of the terminal client's `/ping`, and the button is disabled while you're offline. Each ping waits until any keep-alive PING still outstanding has had its PONG, so a stray PONG can't make the round-trip time look too short.

## Local database (`~/.pscp/<username>.sqlite3`)
| Table | Contents |
| --- | --- |
| `profile` | Your username, display name, about, avatar, broker host/port |
| `rooms` | Groups you have joined, rejoined automatically at start-up. `left_at` is set when you leave |
| `messages` | Chat history per room: direction, status (tick), broker sequence number, time |
| `contacts` | Other users' profiles and presence (online, last seen) |
| `room_members` | Who has been seen in each room. PSCP has no member list, so this is learned from senders |

The file can be opened with any SQLite browser.

## Profile convention (application layer, not part of PSCP)
Profiles travel as JSON text in ordinary PUBLISH messages on a hidden room called `_profiles`.

Room names that start with `_` are reserved by the app and hidden from the chat list.

```json
{"v":1,"kind":"profile","display_name":"Alice","about":"…","avatar":"🙂 or data:image/jpeg;base64,…"}
{"v":1,"kind":"who"}
```

- **When it connects,** a client publishes its profile and a `who` request.
- **Every online client answers `who`** by publishing its own profile. A burst of requests within 0.5 s gets a single reply.
- **Why `who` is needed:** the broker keeps nothing, so this is how someone who joins late learns the profiles of people already online.
- **Photos** are cropped and shrunk to 96×96 JPEG (about 5 KB), well under the 65,535 B payload limit.
- **Profiles from the network are checked before use.** Malformed JSON is ignored. Only JPEG data URIs are shown as images. All text is HTML-escaped before display.

## Limitations (by design of PSCP)
- **No history on the broker.** A message sent while your app is closed is lost if the app stays closed for more than about 2 minutes (the broker's 120 s session retention). Within those 2 minutes, it is replayed when you reconnect.
- **Your history exists only on this device.** Deleting `~/.pscp` deletes it.
- **Delivery is at least once across app restarts.** If the app is killed after sending but before the PUBACK arrives, the message is sent again on the next start.
- **Don't open the same profile on two devices at once.** The second takes over the first one's session, and the two keep taking it back from each other.

## Code
| File | Role |
| --- | --- |
| `webclient/app.py` | Streamlit pages: profile picker, sidebar, chat view (refreshed by `st.fragment(run_every=…)`) |
| `webclient/service.py` | `ChatService`: one long-lived `PSCPClient` per profile (`st.cache_resource`), so a refresh doesn't reconnect. It turns callbacks into SQLite writes and runs the outbox and profile logic |
| `webclient/store.py` | SQLite layer (WAL, one locked connection shared with the receiver thread) |
| `webclient/profiles.py` | Profile JSON, validation, avatar resizing |
| `webclient/ui.py` | WhatsApp CSS and HTML builders |
| `tests/test_webclient.py` | Store, ticks, profiles (including a late joiner), offline outbox, restart/rejoin, UI smoke test |
