# Navigation capture, 2026-09-22

Capture `captures/20260922_162625.pcapng` ran for 30 seconds during the user's map navigation. 480 packet observations decoded to 118 distinct UDP datagrams. No packets were sent by these analysis tools.

Outgoing server-bound observations:

- `0x0017`, 20 bytes: after the common eight-byte token/counter area, two little-endian uint32 values `0x4003a74c` and `0x000186ab`. The latter matches the final ID of the list below. This is a target-selection candidate, not a verified opcode name.
- `0x009b`, 50 bytes: after the common token/counter area, bytes `4c a7 03 00`, a little-endian uint16 count of 8, and eight little-endian uint32 IDs: `0x186a7, 0x186a8, 0x186a9, 0x186ae, 0x186af, 0x186ad, 0x186ac, 0x186ab`. This is a route/waypoint-list candidate. A single capture cannot exclude an object-query list or assign exact semantics.
- `0x005a`, 24 bytes: 12 distinct packets referencing IDs from that list, with trailing values alternating 1 and 2. Purpose unverified.
- `0x1004`, 28 or 40 bytes, to server UDP port 3806: 46 distinct movement-like updates. Three float32 little-endian values at datagram offset 12 move smoothly from approximately `(102361.84, -38592.37, 1494.80)` to `(-2146.67, -3101.51, 79.32)`. Likely position coordinates; axes/units unverified. Longer packets contain three additional float32 values, possibly orientation, before a trailing uint32. Do not label these fields definitively without code correlation.
- `0x0044`, 16 bytes: one occurrence with final uint32 `0x0036e97b`; purpose unknown.

Plaintext applies to these observed proxy-to-server UDP packets. No local client-to-proxy TCP packet was captured here. Packet Monitor observes duplicates at multiple components; distinct counts group endpoint/payload matches and therefore also collapse identical retransmissions.

Artifacts: `captures/decoded_navigation.jsonl` retains decoded frames and raw unknown payloads; `captures/navigation_positions.csv` contains candidate position triples. The static binary has a warp duration/range diagnostic at VA `0x441a04`, referenced by code at `0x41073f`; that alone does not map an opcode to a navigation action.

Result: movement traffic and a likely target/list sequence identified. Exact route-command semantics and mapping the destination ID to a map name remain unverified.
