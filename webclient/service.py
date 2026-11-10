"""ChatService: one user's long-lived PSCP connection, bridged into the local SQLite store.

The Streamlit UI only reads the store and calls these methods; all network
events arrive on PSCPClient's receiver thread and are written to the store here.
"""
import threading

from pscp.client import ConnectError, NotConnected, PSCPClient
from pscp.constants import BACKOFF, DEFAULT_PORT, PRESENCE_TOPIC, Presence, Status

from . import profiles

PROFILE_REPLY_DELAY = 0.5  # one reply covers every `who` that arrives within this window


def ping_stats(rtts):
    """{'avg', 'min', 'max', 'loss', 'sent'} from ChatService.ping() results (None = lost)."""
    ok = [r for r in rtts if r is not None]
    loss = 100.0 * (len(rtts) - len(ok)) / len(rtts) if rtts else 0.0
    if not ok:
        return {"avg": None, "min": None, "max": None, "loss": loss, "sent": len(rtts)}
    return {"avg": sum(ok) / len(ok), "min": min(ok), "max": max(ok), "loss": loss, "sent": len(rtts)}


class ChatService:
    def __init__(self, store, logger=None, backoff=BACKOFF, retry_interval=5.0, keepalive=None):
        self.store = store
        self.profile = store.get_profile()
        if self.profile is None:
            raise ValueError("store has no profile")
        self.username = self.profile["username"]
        self.retry_interval = retry_interval
        self.state = "connecting"  # connecting | online | offline | error
        self.last_error = ""
        kw = {"keepalive": keepalive} if keepalive else {}
        self.client = PSCPClient(
            self.profile["broker_host"], self.username, self.profile["broker_port"] or DEFAULT_PORT,
            qos=1, reconnect=False, backoff=backoff, logger=logger,
            on_message=self._on_message, on_presence=self._on_presence,
            on_status=self._on_status, on_ack=self._on_ack, **kw)
        self.log = self.client.log
        self._stop = threading.Event()
        self._flush_lock = threading.Lock()
        self._ack_lock = threading.Lock()
        self._early_acks = {}  # PUBACK that arrived before the row learned its msg_id
        self._reply_timer = None
        self._reply_lock = threading.Lock()
        self._thread = None

    # ---- lifecycle -----------------------------------------------------

    def start(self):
        self.store.requeue_unacked()
        self._thread = threading.Thread(target=self._supervise, name="chat-supervisor", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self.client.close()

    @property
    def online(self):
        return self.client.connected.is_set()

    def _supervise(self):
        """Keep (re)connecting forever; the PSCPClient's own back-off runs inside each attempt."""
        while not self._stop.is_set():
            if not self.client.connected.is_set():
                self.state = "connecting"
                try:
                    self.client.connect(resume=True)
                except ConnectError as e:
                    self.state, self.last_error = "error", f"broker refused: {e.status.name}"
                    self._stop.wait(self.retry_interval * 2)
                    continue
                except ConnectionError as e:
                    if self._stop.is_set():
                        return
                    self.state, self.last_error = "offline", str(e)
                    self._stop.wait(self.retry_interval)
                    continue
                self._after_connect()
            self._stop.wait(0.5)

    def _after_connect(self):
        try:
            self.store.all_offline()  # the $presence snapshot re-marks who is online
            self.client.subscribe(PRESENCE_TOPIC)
            self.client.subscribe(profiles.PROFILE_ROOM)
            for room in self.store.active_room_names():
                self.client.subscribe(room)
            self.state, self.last_error = "online", ""
            self.flush()
            self.publish_profile()
            self.client.publish(profiles.PROFILE_ROOM, profiles.encode_who(), qos=0)
        except (TimeoutError, NotConnected, OSError) as e:
            self.log.warning("post-connect setup failed: %s", e)

    # ---- user actions --------------------------------------------------

    def send(self, room, text):
        self.store.add_outgoing(room, self.username, text)
        if self.online:
            self.flush()

    def flush(self):
        """Publish queued rows in order (offline outbox). QoS 1 keeps them safe across link loss."""
        with self._flush_lock:
            for row in self.store.queued():
                if not self.online:
                    return
                try:
                    msg_id = self.client.publish(row["room"], row["text"], qos=1)
                except (NotConnected, OSError):
                    return
                with self._ack_lock:
                    self.store.mark_sending(row["id"], msg_id)
                    early = self._early_acks.pop(msg_id, None)
                    if early is not None:
                        self.store.mark_acked(msg_id, early == Status.OK)

    def join(self, room):
        """Returns None on success (or when offline: joined at next connect), else an error string."""
        if not profiles.valid_room_name(room):
            return "Group names are 1-64 characters and may not start with '_' or '$'."
        if self.online:
            try:
                status = self.client.subscribe(room)
            except (TimeoutError, NotConnected, OSError) as e:
                status = None
                self.log.warning("subscribe %s deferred: %s", room, e)
            if status is not None and status != Status.OK:
                return f"Broker refused: {status.name}"
        self.store.join_room(room)
        return None

    def leave(self, room):
        self.store.leave_room(room)
        if self.online:
            try:
                self.client.unsubscribe(room)
            except (TimeoutError, NotConnected, OSError):
                pass

    def update_profile(self, display_name, about, avatar, broker_host=None, broker_port=None):
        p = self.profile
        self.store.save_profile(self.username, display_name, about, avatar,
                                broker_host or p["broker_host"], broker_port or p["broker_port"])
        self.profile = self.store.get_profile()
        self.publish_profile()

    def ping(self, count, timeout=3.0):
        """RTTs in ms of `count` PING/PONG exchanges on the live connection; None for each failure."""
        rtts = []
        for i in range(count):
            if i:
                self._stop.wait(0.2)
            try:
                rtts.append(self.client.ping(timeout) * 1000)
            except (TimeoutError, NotConnected, OSError):
                rtts.append(None)
        return rtts

    def publish_profile(self):
        if not self.online:
            return
        p = self.profile
        try:
            self.client.publish(profiles.PROFILE_ROOM,
                                profiles.encode_profile(p["display_name"], p["about"], p["avatar"]), qos=0)
        except (NotConnected, OSError):
            pass

    def _schedule_profile_reply(self):
        """Debounce: a burst of `who` requests (several people joining) gets a single reply."""
        with self._reply_lock:
            if self._reply_timer is not None and self._reply_timer.is_alive():
                return
            self._reply_timer = threading.Timer(PROFILE_REPLY_DELAY, self.publish_profile)
            self._reply_timer.daemon = True
            self._reply_timer.start()

    # ---- PSCP callbacks (receiver thread) -----------------------------

    def _on_message(self, room, sender, text, seq):
        if room == profiles.PROFILE_ROOM:
            self._on_profile(sender, text)
        elif sender == self.username:
            if not self.store.mark_echo(room, text, seq):
                self.store.add_incoming(room, sender, text, seq)  # e.g. sent from a previous install
        else:
            self.store.add_incoming(room, sender, text, seq)

    def _on_profile(self, sender, text):
        if sender == self.username:
            return
        msg = profiles.decode(text)
        if msg is None:
            self.log.info("ignored malformed profile message from %s", sender)
        elif msg["kind"] == "profile":
            self.store.upsert_contact(sender, msg["display_name"], msg["about"], msg["avatar"])
        else:
            self._schedule_profile_reply()

    def _on_presence(self, event, user):
        if user != self.username:
            self.store.set_presence(user, event == Presence.JOIN)

    def _on_ack(self, msg_id, room, status):
        if room == profiles.PROFILE_ROOM:
            return
        with self._ack_lock:
            if not self.store.mark_acked(msg_id, status == Status.OK):
                self._early_acks[msg_id] = status
                if len(self._early_acks) > 1000:  # unclaimed acks must not grow forever
                    self._early_acks.pop(next(iter(self._early_acks)))

    def _on_status(self, text):
        if text.startswith("connection lost"):
            self.state = "connecting"
            self.store.all_offline()
