"""Interactive chat front-end over PSCPClient."""
import threading

from ..constants import PRESENCE_TOPIC, Presence, Status

HELP = """commands:
  /join <room>          subscribe to a room (becomes the current room)
  /leave <room>         unsubscribe
  /msg <room> <text>    send to a specific room
  <text>                send to the current room
  /qos 0|1              QoS for new publishes and subscriptions
  /who                  users online
  /rooms                rooms you are in
  /ping                 measure round-trip time to the broker
  /quit                 disconnect cleanly"""


class ChatCLI:
    def __init__(self, client):
        self.client = client
        self.room = None
        self.online = set()
        self._print_lock = threading.Lock()
        client.on_message = self._on_message
        client.on_presence = self._on_presence
        client.on_status = lambda text: self.say(f"* {text}")

    def say(self, text):
        with self._print_lock:
            print(text, flush=True)

    def _on_message(self, room, sender, text, seq):
        self.say(f"[{room} #{seq}] {sender}: {text}")

    def _on_presence(self, event, user):
        if event == Presence.JOIN:
            if user in self.online:
                return
            self.online.add(user)
        else:
            self.online.discard(user)
        if user != self.client.username:
            self.say(f"* {user} {event.name.lower()}")

    def run(self):
        c = self.client
        c.connect()
        c.subscribe(PRESENCE_TOPIC)
        self.say(f"connected to {c.host}:{c.port} as {c.username} (QoS {c.qos}). /help for commands")
        try:
            while True:
                try:
                    line = input().strip()
                except EOFError:
                    break
                if line and not self._command(line):
                    break
        except KeyboardInterrupt:
            pass
        c.close()

    def _command(self, line):
        c = self.client
        if not line.startswith("/"):
            if self.room is None:
                self.say("* join a room first: /join <room>")
            else:
                self._publish(self.room, line)
            return True
        cmd, _, rest = line.partition(" ")
        rest = rest.strip()
        try:
            if cmd == "/quit":
                return False
            elif cmd == "/help":
                self.say(HELP)
            elif cmd == "/join" and rest:
                status = c.subscribe(rest)
                if status == Status.OK:
                    self.room = rest
                self.say(f"* join {rest}: {status.name}")
            elif cmd == "/leave" and rest:
                self.say(f"* leave {rest}: {c.unsubscribe(rest).name}")
                if self.room == rest:
                    self.room = next((r for r in c.subscriptions if r != PRESENCE_TOPIC), None)
            elif cmd == "/msg" and " " in rest:
                room, text = rest.split(" ", 1)
                self._publish(room, text)
            elif cmd == "/qos" and rest in ("0", "1"):
                c.qos = int(rest)
                self.say(f"* QoS {c.qos} (rejoin rooms to change their subscription QoS)")
            elif cmd == "/who":
                self.say("* online: " + ", ".join(sorted(self.online)))
            elif cmd == "/rooms":
                self.say("* rooms: " + ", ".join(r for r in c.subscriptions if r != PRESENCE_TOPIC))
            elif cmd == "/ping":
                self.say(f"* RTT {c.ping() * 1000:.1f} ms")
            else:
                self.say(HELP)
        except (TimeoutError, OSError) as e:
            self.say(f"* failed: {e}")
        except Exception as e:  # noqa: BLE001 - keep the chat alive on any client error
            self.say(f"* error: {e}")
        return True

    def _publish(self, room, text):
        try:
            self.client.publish(room, text)
        except Exception as e:  # noqa: BLE001
            self.say(f"* not sent: {e}")
