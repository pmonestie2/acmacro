# Native ship progression

`src/ship_progression.rs` reproduces the archived `StaticData.h` hull table.
Its class order is Terran warrior/trader/explorer, Jenquai warrior/trader/explorer,
then Progen warrior/trader/explorer. Public names are TE, TT, TS, JD, JS, JE,
PW, PP and PS. Hull upgrade is an index from 0 through 6, not an item level or
overall character level.

| Class | Upgrade 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TE | 18 | 70 | 280 | 1,100 | 4,500 | 18,000 | 72,000 |
| TT | 13 | 50 | 210 | 850 | 3,400 | 13,400 | 56,000 |
| TS | 12 | 48 | 210 | 850 | 3,300 | 13,200 | 55,500 |
| JD | 16 | 65 | 260 | 1,000 | 4,100 | 16,500 | 66,300 |
| JS | 12 | 50 | 200 | 800 | 3,200 | 13,000 | 55,000 |
| JE | 11 | 45 | 170 | 680 | 2,700 | 11,000 | 42,000 |
| PW | 20 | 80 | 320 | 1,300 | 5,100 | 20,500 | 82,000 |
| PP | 14 | 65 | 260 | 1,000 | 4,000 | 17,000 | 66,300 |
| PS | 13 | 50 | 210 | 850 | 3,500 | 13,700 | 55,000 |

The source itself marks TS, JS and PP rows as guesses; these are archived values,
not new balancing estimates. `PlayerSaves.cpp::ReInitializeSavedData` takes
column 0 for a new ship. `PlayerClass.cpp::ShipUpgrade` selects the new column and
sets both current and maximum hull. Reloaded characters instead load stored
current/max hull fields; this helper supplies the authoritative base capacity
for the Rust server's derived equipment stats, without applying equipment or
passive modifiers again.

EquipmentStats now derives every sandbox class's base hull from upgrade 6,
matching the upgrade advertised to the client, and applies equipped STAT_HULL
modifiers once through the existing stat engine. New ships and full dock/tow
repairs use this capacity. Checkpoints store hull capacity and preserve the
remaining hull fraction when capacity changes; legacy checkpoints use the former
1000-point capacity for this migration. Jumpstart restores 49 percent
of the derived capacity. Integration tests exercise all nine class capacities,
duplicate equipped effects, repeated derivation and broken-item exclusion.

`can_upgrade(current,requested,total_level)` requires exactly the next upgrade
and minimum total levels 10, 30, 50, 75, 100 and 135 for stages 1 through 6.
It validates indices and does not perform NPC mission/payment authorization or
mutate a character. `hull_capacity(class,upgrade)` returns the unmodified base.
`progression` additionally returns the source's cumulative weapon/device mount
counts, cargo count, visual tier, minimum level and warp power.

WeaponTable and DeviceTable entries are additions, so mount counts sum every
column through the current stage, as in `ResetWeaponMounts` and
`ResetDeviceMounts`. One source inconsistency is preserved explicitly: PP's
WeaponTable totals 4 mounts at stage 6, while MaxWeaponSlots says 5. The helper
returns the table total; it does not change the existing sandbox's maximum
class mount policy. Cargo is BaseCargo plus twice the upgrade index; all native
values remain within 40, including TT 40 at the final stage. Warp power is
3 plus integer upgrade/2, matching the saved-data rebuild rule.

`PlayerSaves.cpp::SetHullUpgrade` uses visual tier 0 at upgrades 0–2, tier 1 at 3–4
and tier 2 at 5–6. The tier is added to each default hull, profession, wing and
engine asset ID. Custom hull/wing choices still require their chosen base
assets; a hull-upgrade index is not itself an asset ID. Returning this tier does
not change protocol appearance or client attachments by itself.

Cargo, intermediate mount counts and appearance tiers remain helper metadata.
This correction changes hull capacity only; existing class maximum mounts,
sandbox cargo provisioning and ship appearance stay under their current policy.
