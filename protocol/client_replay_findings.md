# Client command trace and replay constraints

## Update: native injection tested successfully

`inject_navigation.py` now supports one-shot `0x009b` submission on this exact proxy build. At 16:38:52 on 2026-09-22, it submitted the saved seven-waypoint frame through native handler `0x402820`. The native thread returned the completion marker; the sender sequence advanced from 1690 to 1691. The packet capture contains exactly one distinct outgoing `0x009b` datagram with sequence 1690 and a body identical to the saved command. This is verified transmission, not merely an API success code.

There were 23 subsequent outgoing position updates, from approximately `(91886.56, -35418.27, -404.38)` to `(47805.46, -19878.11, -1515.55)` during the remaining capture. An incoming `0x009c` frame with body `01 00 00 00` followed about ten seconds later; its status semantics are not established. The game and proxy remained responsive. Destination arrival was not verified.

The injector defaults to validation only. It verifies the executable SHA256, runtime code signatures, active type-3 connection, and the frame's entity/token against the current session. It checks for a quiet sender interval, allocates a private copy of the connection data with the captured payload, and calls the native handler on one temporary thread. The live receive buffer and RC4 state are not overwritten. The handler's connection-flag side effect is applied to the live object. Code/data allocations are freed after thread completion; a timeout is never automatically retried and retains allocations so executing code cannot be freed beneath the thread.

This is an experimental one-shot tool, not a general packet injector or production macro transport. The quiet-interval check is not a lock against all concurrent proxy activity. Use while the game is idle; do not run simultaneous injectors or navigate manually during submission. The saved route may be invalid after a sector/session change; entity validation is not proof that all waypoint IDs still belong to the current sector. It supports only the fingerprinted build and only navigation opcode `0x009b`.

Example for this session (elevated shell; recheck PID and route for a new session):

```powershell
# Validate only:
C:\PYTHON\python.exe protocol\inject_navigation.py --pid 12020 --frame protocol\captures\navigation_client_frame.bin --report protocol\captures\inject_preflight.json
# Submit once:
C:\PYTHON\python.exe protocol\inject_navigation.py --pid 12020 --frame protocol\captures\navigation_client_frame.bin --send --report protocol\captures\inject_result.json
```

Evidence: `captures/inject_result.json`, `captures/20260922_163832.pcapng`, and `captures/decoded_injection.jsonl`. All 19 protocol tests pass. The earlier trace-only findings below describe the investigation before the injector was implemented.

On 2026-09-22, a 30-second trace using the proxy's existing diagnostics captured six client frames, including one map-navigation route. No packet was injected or replayed. The helper restored all diagnostic settings afterward (`captures/client_trace_status.json`: `restored: true`).

## Verified navigation frame

At 16:32:01 the proxy received encrypted header `2b 5e df 76`, decrypted it to `26 00 9b 00`, and logged this 34-byte decrypted body:

```text
4c a7 03 00 07 00
a8 86 01 00 a9 86 01 00 ae 86 01 00 af 86 01 00
ad 86 01 00 ac 86 01 00 ab 86 01 00
```

The complete plaintext client frame is 38 bytes:

| Offset | Type | Captured value |
| --- | --- | --- |
| 0 | uint16 LE | 38, total frame bytes |
| 2 | uint16 LE | `0x009b`, navigation route observed during user action |
| 4 | uint32 LE | `0x0003a74c`, entity/token field |
| 8 | uint16 LE | 7, waypoint count |
| 10 | Seven uint32 LE values | `0x186a8, 0x186a9, 0x186ae, 0x186af, 0x186ad, 0x186ac, 0x186ab` |

The preceding `0x0017` client frame was `0c 00 17 00 4c a7 03 40 ab 86 01 00`, referencing the same final waypoint. Its exact selection semantics are still inferred.

The client frame omits the extra eight bytes present in the outgoing UDP envelope. The client-facing TCP connection uses continuous RC4 state; encrypted header bytes are not reusable on an arbitrary stream position. The packet body logged above is after decryption, not ciphertext.

## Verified proxy code path

- Active game connection: pointer at `[global 0x44e620] + 0xb10`, type 3 in this session.
- TCP receive loop decrypts header/body using the receive RC4 state, then dispatches type 3 to `0x402820`.
- Opcode `0x009b` maps through the switch table to `0x402b46`. It calls `0x4027c0` (which invokes `0x40ecd0` on the movement object) and clears a connection flag before forwarding.
- The forwarding path calls `0x40f220`, then `0x40cc40`, which builds/queues the server-bound UDP command using proxy-owned connection state.
- `0x0017` and `0x00a3` take the ordinary forwarding branch `0x402b54`.

These addresses apply only to the binary fingerprint in README.md. This establishes a client command and the proxy processing path, not a tested injection mechanism.

## What a working replay must do

Do not write the saved plaintext frame to the encrypted TCP socket. Do not reuse the captured ciphertext header. Inserting bytes into the existing RC4 stream without coordinating the client's subsequent state would desynchronize it.

A replay implementation needs either a relay that owns both sides' cipher states and rewrites the ongoing stream, or an in-process command submission through the proxy's native handler. Calling the forwarding function alone would skip the navigation opcode's local side effects. Opening a second TCP connection is not verified to join the current game session. The tested injector now uses the native-handler approach described above. Further disassembly showed `0x40bf10` invokes Winsock directly; the forwarding path should not be assumed to provide a synchronized queue.

Artifacts under ignored `captures/`:

- `client_trace_20260922_163140_2026_09_22.log`: actual encrypted/decrypted-header diagnostics and plaintext bodies.
- `client_frames.jsonl`: six extracted, length-checked frames.
- `navigation_client_frame.bin`: exact 38-byte plaintext frame for offline inspection; NOT ready for direct socket transmission.

`extract_client_trace.py` validates logged headers, payload lengths and waypoint counts. The protocol test suite has 15 passing tests. The trace helper temporarily changes the built-in logging flags and log prefix in process memory, then restores them; it does not patch executable code or change disk game files. Do not terminate it during the 30-second trace, because its cleanup must run.
