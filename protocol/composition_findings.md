# Composition-click investigation

## Injection experiment: no confirmed composition response

After a fresh world capture (`20260922_173402.pcapng`), the nearest remembered gas cloud was `0x1870b`; its last named update was older than the public 30-second dump window. A single `0x005a` action-1 request was injected through the native handler. The continuous wire log verified an outgoing 24-byte datagram at 17:34:46, sequence 375, with body `40 03 a7 4c 00 01 87 0b 01 00 00 00` after removing the UDP envelope. The server returned a short `0x005c` body `00 01 86 a8 00 00 00 00 00 00 00 00`, referencing a different object. No composition response for the requested cloud was identified.

A follow-up intended to submit selection `0x0017` returned from the native handler, but the observed outgoing packet at 17:35:17 was instead `0x005a`, sequence 377, with a mismatched little-endian payload and extra bytes. Cause is unresolved: handler return alone is not evidence that the intended command reached the wire. Further selection injection is disabled in validation pending investigation. No bulk selection or composition requests were sent. See `captures/composition_inject_result.json`, `captures/composition_select_result.json`, and `captures/packet_stream.jsonl`.

## Follow-up: seven composition-like object replies found

The next 15-second capture was `20260922_172923.pcapng`. The continuous log also retained later clicks. Seven target changes correlated with larger `0x001b` replies whose first uint32 is the selected object ID. These are stronger composition candidates than the previously noticed `0x005c` replies.

The observed bodies begin with object ID, encoded length and prefix `01 a6 02 f6`. Populated entries in these samples contain marker bytes `36 07 e3`, followed by a little-endian uint32 resource ID and a little-endian uint32 quantity candidate. This is an extraction rule for the observed layout, not a complete decoding of its variable-field masks. `0x2025` replies sometimes carry the same resource IDs, potentially related to resource metadata; their precise role remains unverified.

| Cloud ID | Resource ID : quantity candidates | Final uint32, possible level |
| --- | --- | --- |
| `0x18753` | 2847 : 4, 2870 : 15 | 2 |
| `0x18740` | 2847 : 5 | 2 |
| `0x18750` | 2895 : 18 | 3 |
| `0x18747` | 2910 : 5, 2918 : 4 | 4 |
| `0x18741` | 2895 : 7 | 3 |
| `0x1874a` | 2895 : 4 | 3 |
| `0x18748` | 2876 : 7 | 2 |

All are gas clouds sharing type ID 1834. The differing trailing values are evidence against assuming that the shared type alone fixes level. They need comparison with UI levels before treating the field as confirmed. Resource IDs cannot yet be named carbon dioxide, fluorine, etc. without a name mapping. Raw bodies and extracted candidates are retained in `captures/composition_candidates.json`.

The requested timed capture ran for 15 seconds (`20260922_172428.pcapng`). It contains no new selection/composition request. The already-running continuous log caught two subsequent selection changes at 17:24:46 and 17:24:49, after this timed capture's packet window. The user reported three clicks; three distinct network requests were not observed.

Both observed exchanges have this shape:

1. Outgoing `0x0017`: player ID and selected object ID, both uint32 little-endian. Selected IDs were `0x18751` and `0x18739`.
2. Incoming `0x0019`: selected ID followed by `ffffffff`; significance unverified.
3. Outgoing `0x005a`: 12-byte body containing player ID and target ID in big-endian, then a uint32 little-endian action. Action 2 references the previous target; action 1 references the new target. This supports a selection subscription/activation interpretation, not proof of a composition-specific request.
4. Incoming `0x005c`: 20-byte body beginning with the selected target ID in big-endian, followed by opaque data.

Example request payload after removing the UDP wrapper, for `0x18739`:

```text
40 03 a7 4c  00 01 87 39  01 00 00 00
player ID    target ID     action 1
```

Its `0x005c` response body:

```text
00 01 87 39 00 00 00 01 02 00 09 00 00 00 00 01 00 00 09 00
```

The `0x18751` response differs in the penultimate four-byte group (`02 00 09 00` instead of `00 00 09 00`). No material names or quantities have been identified in these bytes. Similar `0x005a`/`0x005c` exchanges also occurred during ordinary navigation, so they cannot yet be labeled composition commands. The visible composition text is needed to correlate contents with a response or determine whether the UI is using previously cached information.
