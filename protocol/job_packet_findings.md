# JE job-terminal capture, 2026-10-19

Read-only capture of JE proxy PID 9244, creation time
2026-10-19 14:12:03.505000+00:00. No commands were injected and the running
macro was not changed. Raw evidence is in
`diagnostics/je_job_capture_20261019.jsonl`; decoded analysis is in
`diagnostics/je_job_packet_analysis.json`.

## Confirmed wire layout

Client commands use opcode **0x004e**, with a nine-byte little-endian body:
`uint32 player_id, uint32 target_id, uint8 action`. The captured proxy UDP
datagram adds an eight-byte owner/sequence wrapper before that body. Do not
replay raw UDP datagrams or reuse their sequences. A client frame is 13 bytes:
`uint16 length=13, uint16 opcode=0x004e`, followed by the body.

| Action | Target | Observed behavior |
|---|---|---|
| 6 | 0 | Open terminal; server streams available jobs |
| 7 | Current job ID | Request that job's description |
| 8 | Current job ID | Accept that job |
| 9 | 0 | Close terminal |

The live player ID was 246276 (0x0003c204). IDs are session evidence, not saved
commands for subsequent characters or logins.

Server opcode **0x0093**: `uint32 row_count`, followed by that many rows. Each
row is four little-endian uint32 fields (`job_id, type, unknown, level`) and
three null-terminated cp1252 strings (`title, sponsor, reward`). Observed types:
0 combat, 1 trade, 2 explore. The unknown field was zero in these samples.
Each captured chunk had five rows. One complete opening streamed 80 jobs over
approximately ten seconds; an earlier opening closed partway through the stream.
Scrolling and sorting did not request additional list pages: those are local UI
operations over the already-streamed data.

Server opcode **0x0094**: `uint32 job_id, uint8 available`, followed by two
null-terminated strings (`title, description`). The description includes the
actual delivery target and sector, e.g. Puck in Uranus. A listed title alone is
insufficient to choose the correct job.

Server opcodes **0x0096** (acceptance reply) and **0x0095** (remove from list)
each contained the accepted `uint32 job_id` in the three successful examples.
Do not count an outgoing accept click/request, or an unrelated removal, as a
successful acceptance. Correlate the reply with the pending ID and mission AUX
updates. Failure/limit responses require additional live examples before a sender
can claim complete coverage.

## Observed successful accepts

| Job ID | Destination | Acceptance reply latency |
|---|---|---|
| 94769 | Puck, Uranus | 0.139 s |
| 94811 | Puck, Uranus | 0.183 s |
| 94785 | Puck, Uranus | 0.188 s |

All were Launch a Satellite jobs. The macro logged count 3 before pickup and
6 afterward. Its pickup elapsed time was 94 seconds. Description replies averaged
about 0.16 seconds, while the UI waits two seconds for every selected row.

The catalog contained 23 explore jobs, with 18 satellite/garbage candidates.
`job_packets.py` parses the catalog, requests and descriptions, matches low jobs
by exact title plus Ariel/Puck in Uranus, and builds byte-accurate client frames
without transmitting them. Tests use packets from this live capture.

## Faster implementation path

1. Open the actual terminal once and collect its streamed current catalog.
2. Filter type/title/level in memory; no scrolling, column toggling or pixel tests.
3. Request candidate descriptions by current job ID, correlate replies, and select
   the configured destination. Expired/unavailable IDs must be skipped/refreshed.
4. Submit one accept at a time through the owned proxy's native command handler;
   wait for the matching reply and mission update before accepting another.
5. Stop at the configured count/server limit and close the terminal.

This eliminates per-row rendering delays and repeated focus handoffs. The native
sender validates 0x004e actions 6–9 through the owned proxy's guarded native
sender. A PW probe verified catalog and description replies without accepting
jobs. `packet_jobs.py` implements sequential acceptance with matching server
acknowledgments; `job_pickup = ui` retains the original picker. An uncertain
acceptance stops the macro and cannot fall back to UI. The measured latencies
above are from the original capture, not a benchmark of the new picker.

For actions 6–9, the dispatcher at `0x402820` reaches the forwarding call
`0x40f220`; the local job handler at `0x402770` only acts on action 1, which
the injector rejects. Job submissions now call that same forwarding function
with a private body and the live sender, retaining the process, binary, session,
connection and entity guards. Its code signature is checked before submission.
Packet-only mode stops on failure; UI pickup requires explicit configuration.

Reproduce offline decoding:

```powershell
C:\PYTHON\python.exe -m protocol.job_packets diagnostics\je_job_capture_20261019.jsonl --output diagnostics\je_job_packet_analysis.json
```
