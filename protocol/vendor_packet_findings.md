# Vendor packet experiment — 2026-10-20

Machine-readable compilation: `protocol/vendor_protocol_findings.json`.
This document records observed behavior; vendor and inventory identifiers must
be learned from the relevant session rather than assumed universal.

## NPC inventory survey

The accumulated ID-to-stock catalog is `protocol/npc_vendor_catalog.json`;
`protocol/npc_vendor_catalog.md` lists the item names. Each observation includes
the NPC greeting, reply status, item IDs, prices, and timestamp. Prices describe
the querying character's observed quote. No purchase or sale is part of the survey.

`protocol/vendor_catalog.py` reads changed-slot bitmaps instead of fixed SecureInv
lengths. SecureInv has 96 slots and 13 flag bytes; vendor stock has 128 slots and
17 flag bytes. A slot's bit is at index `slot + 4`. Selected records carry their
own field masks, including optional strings. Updates are applied transactionally;
truncated or unknown records leave the previous stock untouched. Sparse updates
retain unmodified slots. Structure is field `0x100`, quality is `0x200`.

The format was checked against captured AUX frames and the upstream
[SecureInv implementation](https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/AuxClasses/AuxSecureInventory.cpp),
[array flag handling](https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/AuxClasses/AuxBase.cpp), and
[item field serialization](https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/AuxClasses/AuxItem.cpp).

## Earth weapons vendor NPC 32

A user-requested inventory replay succeeded for **NPC 32**, the Earth weapons
vendor. Talk uses the same action 4 and Trade choice 2. Reopening unchanged
stock did not resend the catalog; switching to NPC 31 and back triggered a
fresh response. No purchase was submitted. The resulting inventory has **81
slots**, with item IDs and prices decoded for all 81 and matched in order to
the original 81 `2025` item references. Saved data:
`protocol/vendor32_inventory_sample.json`.

This catalog's `001b` frame was split across application datagrams: an initial
`2016` carried 1400 bytes of an 1830-byte inner frame, followed by a `201a`
continuation with the same player/session and the next sequence number. Removing
each 12-byte outer header and joining those parts reproduced the exact declared
frame length. This is separate from IPv4 fragment reassembly. The capture tool's
all-packets mode now preserves undecodable raw datagrams instead of dropping
them, making reconstruction possible. The general live decoder has not yet
been extended to reassemble these application fragments.

The 1826-byte AUX body contained 30 short `0052` records (item ID and price;
quantity/quality retained from cache), followed by 51 full `0372` records
(item ID, quantity, price and quality fields). All item/price entries are
recovered, but this delta does not independently resend every quantity or
provide item display names. Seventeen relevant unit tests and scoped mypy of
the two changed protocol tools passed.

## Packet-only selling verified

A second requested **20-cycle run completed**, buying and selling another
**440 items**. Credits changed from **8,010,438** to **8,419,638** (+409,200).
Every cycle closed trade; final closure was confirmed by server state `-32`.
Evidence: `diagnostics/pw_packet_trade_twenty_cycles_run2_20261020.json`.

The new capture initially stopped because Windows denied atomic replacement
while a reader held the status file. The collector now retries that transient
lock and retains its previous status on persistent contention, keeping capture
alive. Two behavioral tests and its scoped mypy check passed. The pending
seventeenth sale in cycle 1 was reconciled from a read-only desktop screenshot
showing exactly the expected **8,021,283** credits, then only the remaining five
slots were sold. No pending sale or purchase was repeated, and that screenshot
did not focus or send input to the game. Subsequent confirmations used packets.

A subsequent user-requested **20-cycle packet-only run completed**: 440 items
bought from Charon and sold to Earth, with no UI input or station resets.
Every one-unit sale received its matching credit increase and cargo removal;
all 20 purchase and 440 sale submissions are accounted for without repeated
slot submissions. Trade was closed after each cycle. Credits changed from
**7,601,238** to **8,010,438**, a net increase of **409,200**. Evidence:
`diagnostics/pw_packet_trade_twenty_cycles_20261020.json`.

A concurrent status-file rewrite interrupted cycle 3 before its next sale.
The runner resumed the confirmed purchase's remaining slots, without rebuying
or reselling confirmed items. The passive capture status writer now uses atomic
replacement; diagnostic status reads tolerate an in-progress legacy write.

Earth sales use `0027` with six big-endian uint32 fields
`[full player GameID, 1, cargo slot, 4, 0, 1]`. The physical UI capture
established these fields; the tenth user-authorized cycle replayed all 22 sales
entirely through packets. Talk to Earth NPC 31 with action 4, choose Trade (2),
wait for the server's trade-open state, then submit each known cargo slot once.
Each sale was confirmed by a **1,923-credit increase** and a ship cargo record
clearing the item. Clear records can use masks `b203` or `f203`, followed by
empty-item marker `ffffffff`; record size/mask depends on prior cached state.
The initial sale was already confirmed when the stricter diagnostic waited for
the other mask; it was not repeated.

All **10 cycles completed**, buying and selling **220 items**. Inspection action
records confirm one initial reset: nine UI sale requests, only the first with
`reset_position=true`. Cycles 2-9 reopened the vendor with an ordinary click;
cycle 10 used packets for talk, Trade, sale, and closure. Credits increased from
**7,396,638** to **7,601,238** (204,600 net). Evidence:
`diagnostics/pw_vendor_ten_cycles_20261020.json` and its passive capture.

The UI sell-all routine now performs one pass. In the preceding five-cycle run,
one pass left one item on the fifth cycle; it was sold individually to finish.
All nine UI sales in the subsequent ten-cycle run cleared all 22 items in one
pass. Nineteen relevant unit tests and scoped sender mypy passed. A scoped check
of `ac.py` still reports 56 errors in existing unrelated sections, including
wildcard-imported object names; no clean full-module type check is claimed.

## Bulk purchase limit observed

At the user's request, one Charon slot-0 transfer with quantity **20** was
submitted (same six big-endian uint32 layout). The client explicitly reported
**Insufficient space in cargo hold.** Credits remained 7,162,941; no purchase
credit/cargo confirmation arrived. No smaller or repeated purchase was sent.
Trade was closed. The native experiment now also permits exactly quantity 20
for slot 0; other quantities besides 1 remain rejected. Offline validation tests
and a scoped sender type check passed. Evidence:
`diagnostics/pw_vendor_replay_charon_20_unconfirmed_20261020.json`.

After the user explicitly requested another quantity-20 purchase, it succeeded:
credits 7,195,632 -> 7,175,772 (19,860 = 20 x 993), and the cargo response
contained twenty one-unit records for Charon item `0x148a`. One transfer command
requested all 20; trade was closed. The fresh catalog omitted SecureInv and
placed the vendor slot-0 delta at offset 27 rather than 350. The diagnostic now
accounts for that observed flag combination. Evidence:
`diagnostics/pw_vendor_replay_charon_20_success_20261020.json`.

Live verification: PW, docked at Earth, Lenorn Listor (NPC 31). After capturing
a manual purchase, PW left the vendor room using the normal station route. From
the lobby/corridor, a packet-only replay opened Lenorn's dialogue and trade
session and bought one additional unit from vendor slot 23. Credits changed
from 7,159,649 to 7,159,576 (73 credits), with a matching ship cargo update.
The session was then closed.

A second user-authorized test succeeded after PW undocked into Earth sector.
The server returned Lenorn's dialogue and a fresh matching vendor catalog, then
accepted one slot-23 purchase. Credits changed from 7,159,576 to 7,159,503
(73 credits), with a ship cargo update. The trade session was closed afterward.
Thus this vendor works from both another station room and space in Earth sector.
A third user-authorized test succeeded in Asteroid Belt Alpha sector, confirmed
by the client's sector-entry chat message before submission. Lenorn returned a
fresh matching catalog, and a one-unit purchase reduced credits from 7,159,503
to 7,159,430 (73 credits), with a ship cargo update. Trade was closed afterward.
This verifies remote access to this Earth vendor across a sector boundary.
Other vendors and additional sectors were not tested.

A fourth user-authorized Earth replay succeeded while traveling toward Pluto;
the pre-submission screenshot placed PW at Akeron's Gate near the Pluto/Charon
accelerator. Lenorn supplied a fresh matching catalog, and one purchase reduced
credits from 7,159,430 to 7,159,357 (73 credits), with a cargo update. Trade was
closed. Evidence: `diagnostics/pw_vendor_replay_akeron_20261020.json`.
These automated Earth commands are present in the ongoing Pluto capture and
must not be mistaken for the user's manual Pluto vendor interaction.

The outgoing application frames have a little-endian uint16 length and opcode:

| Opcode | Body | Meaning |
| --- | --- | --- |
| `004e` | little-endian uint32 player low 24 bits, uint32 NPC ID, byte action 4 | Talk to NPC |
| `0055` | little-endian uint32 player low 24 bits, byte choice 2 | Trade |
| `0027` | six big-endian uint32 values: full player GameID, source inventory 4, source slot 23, destination inventory 1, destination slot `ffffffff`, quantity 1 | Buy captured item into cargo |
| `0055` | same player, choice 0 then choice 230 | Close trade/dialogue |

Incoming `0054` identifies Lenorn's dialogue. Incoming `0056` value 3 marks
the trade session opening. Player AUX `001b` carries vendor stock and credits;
ship AUX carries the cargo update. The server may retain the vendor catalog
instead of retransmitting unchanged stock on reopening. The replay used the
catalog captured from this same NPC and checked any newly received stock
against it before buying.

The native sender uses the fingerprinted proxy's existing forwarding function
at `0x40f220`, with process/session identity guards. Validation currently allows
only captured NPCs 31 and 536, dialogue choices, and one-unit purchases from
captured slots 23 or 0. The active vendor catalog determines slot meaning. This is
an experiment, not a configurable vendor-buying routine or macro integration.
Purchases are never retried after an uncertain reply.

Local evidence (ignored diagnostics directory):

- `diagnostics/pw_vendor_capture_20261020.jsonl`: manual and replay packet capture.
- `diagnostics/pw_vendor_replay_20261020.json`: replay commands and credit/cargo confirmation.
- `diagnostics/pw_vendor_replay_lobby_20261020.json`: preserved first successful replay.
- `diagnostics/pw_vendor_replay_space_20261020.json`: successful undocked replay.
- `diagnostics/pw_vendor_replay_aba_20261020.json`: successful Asteroid Belt Alpha replay.
- `diagnostics/replay_vendor_probe.py`: supervised one-shot experiment.

Offline tests cover command validation and guarded forwarding. They do not
drive a game client. Live confirmation above is separate from those tests.

## Charon manual purchase captured

The user's Pluto destination was Charon Cloister in Pluto and Charon sector.
A separate 20-minute passive capture is running against PW's pinned proxy PID/birth under
`diagnostics/pw_pluto_vendor_capture_20261020.jsonl`. No automated vendor
commands are sent by this capture.

The manual purchase was captured at timestamp 1792490701.5513449. The vendor's
on-screen label is **Harabolos Aikmene**, NPC **536**. Player AUX explicitly
identifies **Charon Cloister** and **Pluto and Charon**. The initial dialogue
offers trade as choice **2**, with the same opening-state sequence `6, 5, 3`
and closing choices `0, 230` as Earth (the UI also repeats choice 230).

The buy body is six big-endian uint32 values:
`[1073988105, 4, 0, 1, 4294967295, 1]`. This is vendor slot **0**, one unit into
cargo. The server lowered credits from **7,159,357** to **7,158,364**, a cost of
**993**, and sent a 55-byte ship cargo AUX update in the same received datagram.
The initial player stock AUX is 376 bytes, versus Earth's captured 1130 bytes;
catalog sizes must not be hardcoded. The purchased item's display name has not
yet been identified.

At the user's request, one Charon packet replay then succeeded: credits
7,158,364 -> 7,157,371 (993), with a cargo update. It reused the unchanged
same-NPC cached catalog. The subsequent switch back to Earth also succeeded:
credits 7,157,371 -> 7,157,298 (73), with a cargo update. Both sessions were
closed, and exactly one purchase was submitted to each vendor.

Switching back to Earth returned an 1118-byte stock AUX instead of the original
1130-byte message: an earlier slot used a shorter update representation.
The original whole-catalog equality check stopped before buying. Inspection
confirmed the last seven records, including purchased slot 23, matched the
original capture byte for byte; the purchase was then submitted once in the
already-open trade session. Catalog comparison must account for partial record
updates rather than assume whole-message equality. The general diagnostic
replay still stops on these differences; a complete inventory delta decoder
has not been implemented.

Evidence: `diagnostics/pw_vendor_replay_charon_20261020.json` and
`diagnostics/pw_vendor_replay_earth_after_charon_20261020.json`.

A later one-unit Charon attempt opened trade and matched the fresh catalog,
then sent exactly one purchase at 1792490988.5271168. No corresponding credit
or cargo update arrived before the ten-second confirmation deadline. The client
still displayed 7,157,298 credits afterward. A sector transition into Saturn
occurred during that wait. The cause is unestablished; this purchase is not
counted as successful and was not retried. Evidence:
`diagnostics/pw_vendor_replay_charon_unconfirmed_20261020.json`.

After the user explicitly requested another attempt, one new Charon purchase
succeeded: credits 7,157,298 -> 7,156,305 (993), with a matching cargo update.
Trade was closed. The preceding unconfirmed attempt remains uncounted; its
cause is still unestablished. Evidence:
`diagnostics/pw_vendor_replay_charon_retry_20261020.json`.

A subsequent Charon request after PW manually returned to Lenorn at Earth
exposed an important session limitation: talking to NPC 536 returned the
Charon dialogue, but selecting Trade returned Earth's 1118-byte catalog.
The catalog guard prevented any purchase, and the mismatched trade was closed.
Thus remote dialogue alone does not reliably select the active vendor inventory.
Prior successes remain verified by their catalogs and credit/cargo changes,
but arbitrary vendor switching cannot yet be assumed. The influence of the
last physical vendor interaction and any additional selection packet remains
to be established. Evidence:
`diagnostics/pw_vendor_replay_charon_catalog_mismatch_20261020.json`.

Correction to the initial mismatch interpretation: message size did not identify
Earth's inventory. After the user reopened Earth and sold the Charon item,
inspection of the next 1118-byte catalog showed **Charon's** item `0x148a`
and price 993 in slot 0, using the shorter `0x0052` record mask. Later slots
were cleared with empty item markers. The old whole-message comparison was
rejecting a valid partial update. The diagnostic now recognizes this specific
captured slot-0 item/price update; it is not a complete inventory decoder.
One purchase then succeeded: credits 7,163,934 -> 7,162,941, with the correct
Charon item in the cargo reply. Both vendor switching and buying after an Earth
sale are therefore verified for these two vendors. Trade was closed. Evidence:
`diagnostics/pw_vendor_replay_charon_after_earth_sale_20261020.json`.

After another explicit user request following the dialogue/trade-only replay,
a Charon purchase succeeded without a retransmitted catalog: credits
7,163,004 -> 7,162,011 (993), with a cargo reply containing the captured Charon
item identifier bytes `8a140000`. Trade was closed. This shows the earlier
catalog mismatch does not establish that the server selected Earth's inventory;
stale or partial catalog updates remain a possible explanation. The current
diagnostic's same-NPC cache assumption needs a complete inventory state decoder
before production use, especially after switching vendors. Evidence:
`diagnostics/pw_vendor_replay_charon_after_open_20261020.json`.

`protocol/vendor_charon_capture_sample.json` preserves the relevant application
commands, dialogue, stock AUX, credit delta and cargo reply without network
endpoints or unrelated navigation/chat packets.
