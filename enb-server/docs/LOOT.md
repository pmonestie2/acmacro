# MOB drops, wrecks and tractor collection

Item loot is sourced from `data/loot.json`, imported from public `mob_base` and
`mob_items` rows. It contains 2042 MOB templates and 8506 valid drop rows. SQL
percentages, including fractional chances, are retained. The archived C++ byte
conversion and comparison against a unit-scale roll are not reproduced; those
bugs made many nominal low-probability drops unconditional. A legitimate roll
can therefore produce credits only, or an empty wreck for types without credits.

For example, Achilles Drone (template46) has four candidate rows with chances of
20,15,15 and5 percent. It is unsuitable for a test that requires an item after
one kill. Ishuan Revenant Young (template2010) has item5931 at100 percent, so it
provides a real public-content fixture without changing ordinary drop rates:

```text
/d_spawn Ishuan Revenant Young 1
```

`loot_world.rs` captures the authoritative combat killer and death generation.
Corpse birth stock is generated once from that identity and persisted before
publication. A corpse has six native slots and optional credits. Opening it
commits credits and their receipt with the player's inventory. Item collection
reserves the selected slot, emits the native timed tractor effect, then commits
cargo plus its matching receipt before publishing shared depletion. Cancellation
or a failed player write leaves stock available; restart reconciles committed
receipts without generating new drops or awarding the same item twice.

The corpse-birth diagnostic records sector, corpse/spawn IDs, generation,
template, killer, actual slot item IDs/quantities and credits after persistence
succeeds. `items=[]` means the server generated an empty item roll; missing client
stock in another case can be checked against that record. Existing corpses keep
their original stock when the executable or content changes.

The native sequence is target selection `0x17`, loot permission/open `0x51`,
server `0x8c` permission plus corpse AUX and `0x66` interface, then `0x27` item
transfer with source container6 or18 and the advertised corpse slot. Selection,
life/warp state, current range, private ownership/group permission, cargo space
and competing claims are checked server-side. Clients do not submit item IDs or
generated stock. No new packet layout is required.

`tools/smoke_loot.py` kills template2010 with a real equipped beam and asserts
durable nonempty stock before opening. Two native-protocol clients verify private
permissions, shared tractor effect, competing claim rejection, replacement
during collection, owner-only cargo award, corpse depletion for both viewers and
no revival or duplicate award after another replacement. The relocated fixture
clears its original sector's roaming region so movement cannot turn this loot
test into an unrelated range race. Graphical tractor rendering and a Unity
client's own parsing remain separate from that backend packet verification.
