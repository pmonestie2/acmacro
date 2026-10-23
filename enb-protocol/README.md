# ENB shared protocol

This package owns game framing, direct RSA/RC4 TCP connections, capture ingestion,
and navigation-object decoding. It has no GUI, Windows, macro, or web dependency.
Both the web app and the existing `protocol/decode_capture.py` and
`protocol/decode_stream.py` utilities use it. The older world tracker continues to
provide its broader NPC, movement, and relationship observations.

Install from the repository root:

```powershell
C:\PYTHON\python.exe -m pip install -e ./enb-protocol -e ./enb-web
```

`GameConnection.connect()` reads the server's length-prefixed RSA public integers,
generates an eight-byte session key, sends its RSA-encrypted reversed key block,
and maintains independent RC4 state for each direction. `check_version()` sends
version 42.0 and validates the reply. Reads handle TCP segmentation and reject
invalid lengths; failed key exchanges close their socket.

`decode_batch()` validates outer and inner lengths before returning typed frames.
`World` reads sector descriptions and `0x2018` map objects: identity, name, type,
coordinates, visited state, and navigation flags. IDs belong to their sector;
sector changes clear the current objects. Capture readers reassemble IPv4
fragments with bounded pending state.

## Verified milestone, 2026-10-23

- Captured both TCP directions of the user's relogin using Windows Packet Monitor.
- Decoded RSA/RC4 login frames: `0000 -> 0001` version, `006d -> 0070`
  account ticket and character list, `006e -> 006f` character selection.
- Captured UDP handoff `2008 -> 2009`, sector login `0002`, sequenced world
  batches `2016`, and initial sector/map objects.
- Independently connected directly to `sunrise.net-7.org:3805`, completed key
  exchange, and received an accepted version response without a game client or
  Net7Proxy connection.
- Replayed the complete capture into 36 navigable objects in Asteroid Belt Alpha:
  three gates, one station, and 32 navpoints.

Raw evidence is ignored under `diagnostics/terminal_handshake_full.*`; it contains
session material and must not be committed or served by the web application.
The source reference is `therealKyp/Earth-and-Beyond-server` at commit
`3b46ad7c8baecca61adf8a0bedd2b23cb1936b9d`; live observations take precedence.

## Headless sessions

`GlobalLogin.open()` registers a UDP communication socket before fresh HTTPS
account authentication, submits the NUL-terminated ticket, and returns the
account's character list. `SpaceSession.enter()` consumes that connection and
registered socket after character selection, performs the master/sector handoff,
and receives the live sector map. Passwords are not retained for later retries.
Global error `0x0075` replies expose their server explanation and error code.

`SectorStream` reorders sequenced UDP payloads and reconstructs AUX frames split
between `0x2016` packets and `0x201a` continuations. The session handles resend
requests, acknowledgments, keepalives, the ship's position, and explicit logout.
On a lost receive session, its sockets close without sending a forced logout that
could disconnect a newer login of the same character.

Live verification confirmed character login, 36 navigable objects, the ship's
position, and continuing packets in Asteroid Belt Alpha. Warp-to-object and gate
selection/activation/handoff are implemented and unit-tested; they have not been
verified end to end on a live headless session. NPC and inventory interfaces are
not implemented in the web client yet.

HTTPS uses normal certificate validation unless an explicit SHA256 certificate
pin is configured. For the legacy server's expired certificate, the pin is
checked before any credential-bearing request is sent. There is no general
"ignore certificate errors" login option.
