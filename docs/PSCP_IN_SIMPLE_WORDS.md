# PSCP in simple words

## What it is

PSCP is a chat program we wrote ourselves.

Three computers are involved:

- One **broker** in the middle.
- Two or more **clients**, one per user.
- Each chat group is called a **room**.

A client sends a message to the broker. The broker sends a copy to everyone
else in that room. The broker does not chat. It only passes messages.

```
Alice's client  ─────►  Broker  ◄─────  Bob's client
                           │
                    room1: Alice, Bob
```

We did not use MQTT, WebSockets, or any chat library. We made our own small
protocol and sent it over normal TCP sockets.

## What we wanted to build

- A chat that works between three different computers.
- Our own message format, so another group could implement it from our spec.
- Messages that are not silently lost.
- Users can see who else is online.
- If the internet drops for a moment, the client reconnects and gets missed
  messages.

## The main idea

A message is not sent directly from Alice to Bob. It goes through the broker.

This sounds like an extra step, but it makes things simpler:

- Alice only needs to know the broker's address.
- Bob can be behind a home router; he does not need a public address.
- The broker can give every message in a room an order number.
- The broker can keep a message until Bob confirms he got it.

## How we built it

The protocol code is in `pscp/`. It needs only Python's standard library.

- `pscp/wire.py` turns a message into bytes and bytes back into a message.
- `pscp/constants.py` keeps the message types, error codes, flags and timers.
- `pscp/reliability.py` keeps unacknowledged messages and removes duplicates.
- `pscp/broker/` is the server.
- `pscp/client/` is the client.
- `pscp/store.py` saves a copy of rooms and messages for the demo UI.

The broker is split into four jobs:

- `server.py` accepts connections and runs the timers.
- `connection.py` handles one client's socket.
- `session.py` remembers a user even after their socket closes.
- `rooms.py` holds the subscriber list and order number for each room.

Each client connection has two threads:

- A **reader** receives messages and sends direct replies.
- A **writer** sends messages that were queued for that user.

This means a slow client cannot block the reader of another client.

## The message format

Every PSCP message has the same shape:

```
8-byte header | topic (room name) | payload (message body)
```

The header stores:

- version
- message type
- flags
- room-name length
- message ID
- payload length

The receiver first reads exactly 8 bytes, then reads the room name and body.
TCP can split or combine data, so we cannot assume one `recv()` gives one
message. `wire.py` reads exactly the number of bytes the header says.

## How a connection starts

1. The client opens a TCP connection to the broker on port 5050.
2. The client sends `CONNECT` with a username.
3. The broker creates a session for that username and replies `CONNACK`.
4. If another user already has that username, the broker refuses, unless the
   new connection is a resume.

## How a user joins a room

There is no separate "enter room" command in the protocol. Joining a room is a
subscription.

1. Client sends `SUBSCRIBE room1`.
2. Broker adds that user's session to `room1`'s subscriber list.
3. Broker replies `SUBACK`.
4. The client remembers `room1` as its current room, so normal typed text goes
   there.

A room is created the first time someone subscribes to it. Publishing to a room
you have not joined is rejected. This is a deliberate rule in our design.

## How a message is sent

Alice types `Hi` in `room1`.

1. Alice's client sends:

   ```
   PUBLISH room1, text="Hi", QoS=1, message ID=7
   ```

2. The broker's reader receives it and checks:
   - Is the room name valid?
   - Is Alice subscribed to `room1`?
   - Have we already seen message ID 7 from Alice?

3. The broker gives the message the next order number for `room1`. Say it is 1.
4. The broker makes a copy for every subscriber in the room.
5. Each copy goes into that user's outgoing queue.
6. The broker sends `PUBACK` back to Alice, meaning "I have taken this message".
7. Alice's client removes the message from its retry list.

## How a message is received

1. Bob's writer thread takes the copy from his queue.
2. The broker sends `PUBLISH room1, seq=1, from="Alice", text="Hi"`.
3. Bob's client receives it.
4. If QoS is 1, Bob first sends `PUBACK` back to the broker.
5. Bob's client checks whether it already saw room `room1`, sequence `1`.
6. If it is new, the message is shown to Bob.
7. The broker receives Bob's `PUBACK` and removes the copy from Bob's retry
   list.

So a QoS 1 chat message uses four PSCP messages:

```
Alice  ──PUBLISH──►  Broker  ──PUBACK──►  Alice
Broker ──PUBLISH──►  Bob     ◄──PUBACK── Bob
```

The broker acknowledges Alice before Bob acknowledges the broker. The broker's
`PUBACK` does not mean Bob read the message. It only means the broker has it.

## What QoS 0 and QoS 1 mean

QoS is the delivery promise.

**QoS 0** means "send once and forget". There is no acknowledgement and no retry.
It is faster, but a message can be lost.

**QoS 1** means "keep sending until the next hop confirms it". The sender keeps
a copy, and resends after 2 seconds if there is no acknowledgement. It tries
three times.

Because a resend can create a duplicate, the receiver remembers the last 256
message IDs. If it sees the same one again, it acknowledges it again but does
not show it twice.

## What happens if the connection drops

The broker keeps a user's session for 120 seconds after the connection is lost.
The session contains the user's rooms, queued messages and unacknowledged
messages.

When the client reconnects, it sends `CONNECT` with the RESUME flag.

- If the broker still has the session, it replies with `SESSION=1`.
- The broker resends every unacknowledged message, marked as a duplicate.
- The client also resends its own unacknowledged messages.

If there is no session, the broker replies `SESSION=0`. The client clears its
duplicate list, subscribes to its rooms again, and resends its own messages.

This is why QoS 1 messages can survive a short disconnect, while QoS 0 messages
sent during the gap are lost.

## Presence

Every client also subscribes to a special room called `$presence`.

The broker sends presence messages there:

- `JOIN` when a user connects.
- `LEAVE` when a user disconnects cleanly.
- `TIMEOUT` when a connection dies without a clean disconnect.

When a client subscribes to `$presence`, it also gets a snapshot of everyone who
is currently online. This is what `/who` uses.

## Rooms and the demo UI

The client can ask the broker for the list of rooms with `ROOMS`. The broker
answers with `ROOMSACK`, which contains each room name and how many users are
in it.

The optional browser UI uses this to show a room list. The broker also keeps a
SQLite copy of rooms and messages so previews and history survive a restart.
The protocol itself does not depend on SQLite. If a database write fails, the
broker logs it and keeps running.

## How to run it

Three terminals on one machine:

```bash
python -m pscp.broker --host 127.0.0.1
python -m pscp.client --host 127.0.0.1 --user alice
python -m pscp.client --host 127.0.0.1 --user bob
```

In each client:

```
/join room1
```

Then type a message. The other client should see it.

For three real machines, run the broker on one machine and use its IP address in
the two clients:

```bash
python -m pscp.client --host <broker-ip> --user alice
```

## The demo in one minute

1. Start the broker on the cloud VM.
2. Start Alice and Bob on two other machines.
3. Both join `room1`.
4. Alice sends a message. Bob receives it.
5. Show the log lines: Alice `PUBLISH`, broker `PUBACK`, broker `PUBLISH`,
   Bob `PUBACK`.
6. Kill Bob's client. Show `TIMEOUT` in the logs.
7. Start Bob again with resume. Show the missed messages arriving once.
8. Optionally run the broker with `--drop-ack 0.3` to show retransmission and
   duplicate removal.

## What is still limited

- There is no login password and no encryption. Anyone who can reach port 5050
  can claim a username. In the demo we limit the port to our own IP addresses.
- It is one broker, not a cluster. If the broker restarts, sessions in memory
  are lost.
- The default limit is 100 connected clients.
- QoS 1 means "at least once" on the wire. We hide duplicates at the receiver,
  but this is not a formally proven exactly-once system.
- Measurements and packet captures must be taken on the real three-host setup;
  local loopback numbers are only smoke tests.
