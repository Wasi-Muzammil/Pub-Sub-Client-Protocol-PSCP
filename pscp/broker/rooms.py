"""Rooms (topics): subscriber lists and per-room sequence numbers.

One lock per room. Taking a sequence number and enqueueing to every subscriber
happen under that lock, so all subscribers see the same order.
"""
import threading

from ..constants import MAX_MSG_ID, MsgType
from ..wire import Message


class Room:
    def __init__(self, name):
        self.name = name
        self.lock = threading.Lock()
        self.last_seq = 0
        self.subscribers = {}  # Session -> granted QoS

    def next_seq(self):
        # 1..65535, wrapping; 0 means "no sequence number"
        self.last_seq = self.last_seq % MAX_MSG_ID + 1
        return self.last_seq


class Rooms:
    def __init__(self):
        self._lock = threading.Lock()
        self._rooms = {}

    def _room(self, name):
        with self._lock:
            room = self._rooms.get(name)
            if room is None:
                room = self._rooms[name] = Room(name)
            return room

    def subscribe(self, session, topic, qos):
        room = self._room(topic)
        with room.lock:
            room.subscribers[session] = qos
        session.subscriptions[topic] = qos

    def unsubscribe(self, session, topic):
        room = self._room(topic)
        with room.lock:
            found = room.subscribers.pop(session, None) is not None
        session.subscriptions.pop(topic, None)
        return found

    def remove_session(self, session):
        for topic in list(session.subscriptions):
            self.unsubscribe(session, topic)

    def publish(self, topic, payload, qos, msg_type=MsgType.PUBLISH):
        """Fan out to every subscriber with QoS = min(publish QoS, subscription QoS).

        Returns (seq, sessions whose queue overflowed).
        """
        room = self._room(topic)
        overflowed = []
        with room.lock:
            seq = room.next_seq()
            for session, sub_qos in room.subscribers.items():
                msg = Message(msg_type, min(qos, sub_qos), seq, topic, payload)
                if not session.enqueue(msg):
                    overflowed.append(session)
        return seq, overflowed
