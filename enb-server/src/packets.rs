//! Packed Win32 wire layouts, derived from PacketStructures.h and AuxClasses.
use crate::account::CharacterProfile;
#[cfg(test)]
use crate::account::FIRST_PLAYER_ID;
use crate::content::{NavPoint, Sector};
use crate::equipment_stats::ShipStats;
use crate::inventory::{Inventory, ItemInstance};
use crate::items::{ItemCatalog, ItemTemplate};
use crate::mob::Mob;
use crate::motion::Motion;
use crate::protocol::{string, Frame};
use crate::skills::SkillCatalog;
use crate::warp::{FinishReason, WarpPhase};
use std::collections::BTreeMap;
use std::net::Ipv4Addr;

fn put32(data: &mut [u8], offset: usize, value: u32) {
    data[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
}
fn be32(data: &mut [u8], offset: usize, value: u32) {
    data[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
}
fn float(data: &mut [u8], offset: usize, value: f32) {
    put32(data, offset, value.to_bits());
}
fn fixed_text(data: &mut [u8], offset: usize, size: usize, text: &str) {
    let length = text.len().min(size - 1);
    data[offset..offset + length].copy_from_slice(&text.as_bytes()[..length]);
}

pub fn avatar(profile: &CharacterProfile) -> Vec<u8> {
    let mut data = vec![0; 241];
    fixed_text(&mut data, 0, 20, &profile.name);
    data[45] = 1; // Avatar version; remaining appearance fields use client defaults.
                  // AvatarData fields are little-endian even though the enclosing character
                  // list's AvatarInfo fields use network byte order (observed client capture).
    put32(&mut data, 46, profile.race);
    put32(&mut data, 50, profile.profession);
    for offset in [98, 110, 122, 134] {
        float(&mut data, offset, 0.5);
    }
    data
}

pub fn characters(sector: &Sector, profile: &CharacterProfile) -> Frame {
    let mut data = vec![0; 5 * 374 + 4 + 2 * 84];
    be32(&mut data, 4, sector.id);
    be32(&mut data, 8, 1);
    be32(&mut data, 12, 5);
    be32(&mut data, 20, profile.game_id & 0xffffff);
    be32(&mut data, 28, 1);
    for offset in [40, 44, 48] {
        be32(&mut data, offset, crate::account::SANDBOX_TRACK_LEVEL);
    }
    fixed_text(&mut data, 52, 81, &sector.name);
    data[133..374].copy_from_slice(&avatar(profile));
    be32(&mut data, 1870, 1);
    let galaxy = 1874;
    fixed_text(&mut data, galaxy, 64, "Rust Sandbox");
    // Galaxy is a mixed-endian structure: count/port use network byte order,
    // GalaxyID/player counts use little-endian, and the IPv4 integer is stored
    // reversed. Both AccountManager::BuildAvatarList and capture confirm this.
    put32(&mut data, galaxy + 64, 1);
    let mut address = Ipv4Addr::LOCALHOST.octets();
    address.reverse();
    data[galaxy + 68..galaxy + 72].copy_from_slice(&address);
    data[galaxy + 72..galaxy + 74].copy_from_slice(&3801u16.to_be_bytes());
    put32(&mut data, galaxy + 78, 1);
    data[galaxy + 82..galaxy + 84].copy_from_slice(&1u16.to_le_bytes());
    Frame::new(0x70, data)
}

pub fn character_ticket(sector: u32, nonce: &[u8; 20], player: u32) -> Frame {
    let mut data = vec![0; 68];
    be32(&mut data, 20, player);
    be32(&mut data, 24, sector);
    data[48..68].copy_from_slice(nonce);
    Frame::new(0x6f, data)
}

pub fn global_error(message: &str, code: u32) -> Frame {
    let mut data = Vec::from((message.len() as u32).to_le_bytes());
    data.extend_from_slice(&code.to_be_bytes());
    data.extend_from_slice(message.as_bytes());
    Frame::new(0x75, data)
}

pub fn sector_description(sector: &Sector, player: u32) -> Frame {
    let mut data = Vec::from(4u32.to_le_bytes());
    data.extend_from_slice(&0u32.to_le_bytes());
    data.extend_from_slice(&(player & 0xffffff).to_le_bytes());
    data.extend_from_slice(sector.system.as_bytes());
    data.push(0);
    data.extend_from_slice(sector.name.as_bytes());
    data.extend_from_slice(&[0, 0]);
    data.extend_from_slice(&375u32.to_le_bytes());
    let length = data.len() as u32 - 8;
    put32(&mut data, 4, length);
    Frame::new(0x97, data)
}

pub fn map_object(nav: &NavPoint) -> Frame {
    let mut data = vec![0; 60];
    put32(&mut data, 0, nav.id);
    // This byte becomes Create.Type in the native client, not a database type.
    // Newer public content includes DB types 41/42; the archived loader falls
    // back to decorations for unknown static objects. Sending those raw values
    // selects client actor types that this server has not initialized.
    data[4] = match nav.object_type {
        3 | 11 | 12 | 25 | 37 | 38 => nav.object_type,
        _ => 37,
    };
    data[5..7].copy_from_slice(&nav.asset.to_le_bytes());
    float(&mut data, 7, nav.scale);
    data[23] = 2; // static scenery/resources have a friendly relationship
    for (i, color) in nav.hsv.iter().enumerate() {
        float(&mut data, 11 + i * 4, *color);
    }
    for (i, coordinate) in nav.position.iter().enumerate() {
        float(&mut data, 25 + i * 4, *coordinate);
    }
    for (i, value) in nav.orientation.iter().enumerate() {
        float(&mut data, 37 + i * 4, *value);
    }
    float(&mut data, 53, nav.signature);
    data[57] = nav.navigation_type
        | if nav.navigation_present { 0x90 } else { 0 }
        // Proxy flag5 becomes AuxNameSignature.IsNav. Navigation presence
        // (flag4) alone emits0x99 but leaves generic Type37 actors non-navs.
        | if nav.navigation_type > 0 { 0x20 } else { 0 }
        | if nav.huge { 0x40 } else { 0 };
    data[58..60].copy_from_slice(&(nav.name.len() as u16).to_le_bytes());
    data.extend_from_slice(nav.name.as_bytes());
    Frame::new(0x2018, data)
}

/// Refresh known static actors without sending another Create. The proxy's
/// compact map packet expands into this native name/signature AUX plus0x99.
/// Resume uses the native updates to repair an old client's cached nav flags.
pub fn static_navigation_refresh(nav: &NavPoint) -> Vec<Frame> {
    let mut frames = Vec::new();
    if !matches!(nav.object_type, 3 | 11 | 12 | 25 | 38) {
        let mut record = vec![0x72];
        record.extend(text(&nav.name));
        record.push(u8::from(nav.navigation_type > 0));
        record.extend_from_slice(&nav.signature.to_le_bytes());
        frames.push(aux_frame(nav.id, record));
    }
    if nav.navigation_present && nav.navigation_type > 0 {
        let mut body = Vec::from(nav.id.to_le_bytes());
        body.extend_from_slice(&nav.signature.to_le_bytes());
        body.push(1); // Current sandbox publishes static navs as visited.
        body.extend_from_slice(&u32::from(nav.navigation_type).to_le_bytes());
        body.push(u8::from(nav.huge));
        frames.push(Frame::new(0x99, body));
    }
    frames
}

pub fn create(id: u32, asset: u16) -> Frame {
    let mut data = Vec::from(id.to_le_bytes());
    data.extend_from_slice(&1f32.to_le_bytes());
    data.extend_from_slice(&asset.to_le_bytes());
    data.push(1);
    data.extend_from_slice(&[0; 12]);
    Frame::new(4, data)
}

pub fn resource_name(id: u32, name: &str) -> Frame {
    // Resource::SendAuxNameResource uses this distinct two-byte resource AUX
    // header. It is not the 58-field ship AUX used by players and MOBs.
    let mut record = vec![0x16, 0x04];
    record.extend(text(name));
    aux_frame(id, record)
}

/// A Jenquai ship is assembled from a hull and four bone attachments. These
/// starting assets come from StaticData.h, not item-template IDs.
pub fn ship_subparts(id: u32, class: crate::classes::CharacterClass) -> Frame {
    let mut data = Vec::from(id.to_be_bytes());
    data.extend_from_slice(&4u32.to_be_bytes());
    let parts = if class.race() == 1 {
        vec![
            ("~01", class.profession_asset()),
            ("~01/~03_01", class.engine_asset()),
            ("~01/~03_02", class.engine_asset()),
            ("~02", class.wing_asset()),
        ]
    } else {
        vec![
            ("~01", class.profession_asset()),
            ("~02", class.wing_asset()),
            ("~02/~03_01", class.engine_asset()),
            ("~02/~03_02", class.engine_asset()),
        ]
    };
    for (bone, asset) in parts {
        data.extend_from_slice(bone.as_bytes());
        data.push(0);
        data.extend_from_slice(&asset.to_be_bytes());
    }
    Frame::new(0xb4, data)
}

pub fn ship_colors(id: u32) -> Frame {
    let mut data = Vec::from(id.to_le_bytes());
    data.extend_from_slice(&8u16.to_le_bytes());
    for _ in 0..8 {
        data.extend_from_slice(&0u32.to_le_bytes()); // nonmetallic
        for value in [0.55f32, 0.15, 0.9] {
            data.extend_from_slice(&value.to_le_bytes());
        }
    }
    Frame::new(0x11, data)
}

pub fn client_time(client_sent: u32, server_tick: u32) -> Frame {
    let mut data = Vec::from(client_sent.to_le_bytes());
    data.extend_from_slice(&server_tick.to_le_bytes());
    data.extend_from_slice(&server_tick.to_le_bytes());
    Frame::new(0x34, data)
}

pub fn movement(id: u32, position: [f32; 3], tick: u32) -> Frame {
    position_update(id, position, tick, None)
}

pub fn player_movement(id: u32, position: [f32; 3], tick: u32, motion: &Motion) -> Frame {
    position_update(id, position, tick, Some(motion))
}

fn position_update(id: u32, position: [f32; 3], tick: u32, motion: Option<&Motion>) -> Frame {
    let flags = if motion.is_some() { 0x7fu16 } else { 0 };
    let mut data = Vec::from(flags.to_le_bytes());
    data.extend_from_slice(&id.to_le_bytes());
    data.extend_from_slice(&tick.to_le_bytes());
    for value in position {
        data.extend_from_slice(&value.to_le_bytes());
    }
    for value in motion.map_or([0., 0., 0., 1.], |m| m.orientation) {
        data.extend_from_slice(&value.to_le_bytes());
    }
    data.extend_from_slice(&motion.map_or(0, |m| m.movement_id).to_le_bytes());
    if let Some(motion) = motion {
        // Packet velocities are meters per millisecond. RotY/RotZ match the
        // original Object::Tilt/Turn fields; DesiredY/DesiredZ are zero.
        let acceleration = if motion.speed == motion.set_speed {
            0.
        } else {
            motion.acceleration * 0.001
        };
        for value in [
            motion.speed * 0.001,
            motion.set_speed * 0.001,
            acceleration,
            -motion.tilt * motion.turn_scale / 883.,
            0.,
            -motion.turn * motion.turn_scale / 883.,
            0.,
        ] {
            data.extend_from_slice(&value.to_le_bytes());
        }
    }
    Frame::new(0x3e, data)
}

/// Two-bit AUX flags have four control bits, N value bits and N default bits.
/// A reset nested record additionally carries the C++ empty-record marker 0x05.
fn aux_record(count: usize, nested: &[usize], values: BTreeMap<usize, Vec<u8>>) -> Vec<u8> {
    let mut flags = vec![0u8; (4 + count * 2).div_ceil(8)];
    flags[0] = 6;
    for field in 0..count {
        let bit = 4
            + field
            + if values.contains_key(&field) {
                0
            } else {
                count
            };
        flags[bit / 8] |= 1 << (bit % 8);
    }
    let mut data = flags;
    for field in 0..count {
        if let Some(value) = values.get(&field) {
            data.extend_from_slice(value);
        } else if nested.contains(&field) {
            data.push(5);
        }
    }
    data
}

fn aux_frame(id: u32, record: Vec<u8>) -> Frame {
    let mut data = Vec::from(id.to_le_bytes());
    data.extend_from_slice(&((record.len() + 1) as u16).to_le_bytes());
    data.push(1);
    data.extend(record);
    Frame::new(0x1b, data)
}

/// One-bit AUX flags change only the listed fields. Unlike a full two-bit
/// snapshot this must not reset inventory, skills, or other active effects.
fn ship_diff(id: u32, values: BTreeMap<usize, Vec<u8>>) -> Frame {
    aux_frame(id, aux_diff(58, values))
}

fn aux_diff(count: usize, values: BTreeMap<usize, Vec<u8>>) -> Vec<u8> {
    let mut record = vec![0u8; (4usize + count).div_ceil(8)];
    record[0] = 2;
    for field in values.keys() {
        record[(field + 4) / 8] |= 1 << ((field + 4) % 8);
    }
    for value in values.into_values() {
        record.extend(value);
    }
    record
}

pub fn ability_progress(ability: Option<u32>, start: u32, finish: u32) -> Frame {
    aux_frame(
        0,
        aux_diff(
            18,
            BTreeMap::from([(
                6,
                aux_diff(
                    16,
                    BTreeMap::from([
                        (13, start.to_le_bytes().to_vec()),
                        (14, finish.to_le_bytes().to_vec()),
                        (15, ability.unwrap_or(u32::MAX).to_le_bytes().to_vec()),
                    ]),
                ),
            )]),
        ),
    )
}

pub fn skill_cooldown(skill: u32, tick: u32, duration_ms: u32) -> Frame {
    let skill = aux_diff(
        64,
        BTreeMap::from([(
            skill as usize,
            aux_diff(
                7,
                BTreeMap::from([
                    (1, duration_ms.to_le_bytes().to_vec()),
                    (2, tick.to_le_bytes().to_vec()),
                ]),
            ),
        )]),
    );
    aux_frame(
        0,
        aux_diff(
            18,
            BTreeMap::from([(6, aux_diff(16, BTreeMap::from([(2, skill)])))]),
        ),
    )
}

pub fn cloak(id: u32, enabled: bool) -> Frame {
    ship_diff(id, BTreeMap::from([(19, vec![u8::from(enabled)])]))
}

pub fn reputation(
    catalog: &crate::factions::FactionCatalog,
    reactions: &crate::factions::FactionReactions,
) -> Frame {
    let entries = catalog
        .visible()
        .enumerate()
        .map(|(index, faction)| {
            (
                index,
                aux_record(
                    3,
                    &[],
                    BTreeMap::from([
                        (0, text(&faction.pda_text)),
                        (1, reactions.standing(faction.id).to_le_bytes().to_vec()),
                        (2, (index as u32).to_le_bytes().to_vec()),
                    ]),
                ),
            )
        })
        .collect();
    let reputation = aux_record(
        2,
        &[0],
        BTreeMap::from([
            (0, aux_record(32, &[], entries)),
            (1, text(&reactions.affiliation)),
        ]),
    );
    aux_frame(0, aux_diff(18, BTreeMap::from([(10, reputation)])))
}

pub fn ship_faction(id: u32, identifier: &str) -> Frame {
    ship_diff(id, BTreeMap::from([(57, text(identifier))]))
}

pub fn relationship(id: u32, reaction: u32) -> Frame {
    let mut body = Vec::from(id.to_be_bytes());
    body.extend_from_slice(&reaction.to_le_bytes());
    body.push(0);
    Frame::new(0x89, body)
}

pub fn group_invite(leader: &str) -> Frame {
    let message = format!("{leader} is requesting you to join their group");
    let mut body = Vec::from(((message.len() + 1) as u16).to_le_bytes());
    body.push(1);
    body.extend_from_slice(message.as_bytes());
    body.push(0);
    Frame::new(0x1e, body)
}

pub fn group_state(
    viewer: u32,
    members: &[CharacterProfile],
    leader: u32,
    formation: u32,
    formed: &[u32],
) -> Frame {
    let member_formation = |id: u32| if formed.contains(&id) { formation } else { 0 };
    let member_position = |id: u32| {
        if member_formation(id) == 0 {
            u32::MAX
        } else {
            members
                .iter()
                .position(|member| member.game_id == id)
                .map_or(u32::MAX, |index| index as u32)
        }
    };
    let others: Vec<&CharacterProfile> = members
        .iter()
        .filter(|member| member.game_id != viewer)
        .collect();
    // Explicitly clear unused slots. A nested default/reset marker is suitable
    // for initialization, but the native HUD retains an old member after a
    // live leave unless its name and identity are updated as ordinary fields.
    let member_records = aux_diff(
        5,
        (0..5)
            .map(|index| {
                let member = others.get(index).copied();
                let id = member.map_or(0, |member| member.game_id);
                (
                    index,
                    aux_diff(
                        4,
                        BTreeMap::from([
                            (0, text(member.map_or("", |member| member.name.as_str()))),
                            (1, id.to_le_bytes().to_vec()),
                            (2, member_formation(id).to_le_bytes().to_vec()),
                            (3, member_position(id).to_le_bytes().to_vec()),
                        ]),
                    ),
                )
            })
            .collect(),
    );
    let info = aux_record(
        11,
        &[10],
        BTreeMap::from([
            (0, vec![u8::from(viewer == leader)]),
            (2, vec![1]),
            (3, vec![1]),
            (
                7,
                text(match formation {
                    4 => "Slot Back",
                    5 => "Block",
                    6 => "Pipe",
                    _ => "",
                }),
            ),
            (8, member_formation(viewer).to_le_bytes().to_vec()),
            (9, member_position(viewer).to_le_bytes().to_vec()),
            (10, member_records),
        ]),
    );
    aux_frame(0, aux_diff(18, BTreeMap::from([(17, info)])))
}

pub fn warp_state(id: u32, phase: WarpPhase, reason: Option<FinishReason>, tick: u32) -> Frame {
    let active = matches!(phase, WarpPhase::Charging | WarpPhase::Traveling);
    let (private, global) = match phase {
        WarpPhase::Idle => (0u32, 0u32),
        WarpPhase::Charging => (1, 1),
        WarpPhase::Traveling => (2, 2),
        WarpPhase::Recovering => (
            if reason == Some(FinishReason::Arrived) {
                8
            } else {
                3
            },
            3,
        ),
    };
    ship_diff(
        id,
        BTreeMap::from([
            (16, vec![u8::from(active)]),
            (17, vec![u8::from(active)]),
            (31, private.to_le_bytes().to_vec()),
            (32, global.to_le_bytes().to_vec()),
            (
                33,
                // This is drive availability, not an "idle" boolean. The C++
                // server leaves it at 2 while warping so Q can send a cancel.
                // PrivateWarpState/TriggerTime control charge and recovery.
                2u32.to_le_bytes().to_vec(),
            ),
            (34, tick.to_le_bytes().to_vec()),
            (
                46,
                u32::from(phase == WarpPhase::Traveling)
                    .to_le_bytes()
                    .to_vec(),
            ),
        ]),
    )
}

/// Selection has two messages in the original server: SetTarget controls the
/// target display, while ShipIndex.TargetGameID supplies the actor's target.
/// Updating only the display leaves client actions using the previous target.
pub fn ship_target(id: u32, target: u32) -> Frame {
    ship_diff(id, BTreeMap::from([(27, target.to_le_bytes().to_vec())]))
}

pub fn incapacitated(id: u32, incapacitated: bool) -> Frame {
    ship_diff(
        id,
        BTreeMap::from([
            (16, vec![u8::from(incapacitated)]),
            (17, vec![u8::from(incapacitated)]),
            (21, vec![u8::from(incapacitated)]),
            (
                33,
                (if incapacitated { 0u32 } else { 2 })
                    .to_le_bytes()
                    .to_vec(),
            ),
        ]),
    )
}

pub fn object_effect(id: u32, effect_id: u32, asset: u16, tick: u32) -> Frame {
    let mut data = vec![3]; // effect identity and timestamp are present
    data.extend_from_slice(&id.to_le_bytes());
    data.extend_from_slice(&asset.to_le_bytes());
    data.extend_from_slice(&effect_id.to_le_bytes());
    data.extend_from_slice(&tick.to_le_bytes());
    Frame::new(9, data)
}

/// Simulation supplies raw points. AuxPercent.StartValue is a fraction; hull
/// alone stays in absolute points. This applies equally to players and peers.
pub fn ship_resources(
    id: u32,
    ship: &ShipStats,
    energy: Option<f32>,
    shield: Option<f32>,
    hull: Option<f32>,
) -> Frame {
    let mut values = BTreeMap::new();
    for (field, value, capacity) in [
        (4, energy, ship.energy_capacity),
        (6, shield, ship.shield_capacity),
    ] {
        if let Some(value) = value {
            let value = resource_fraction(value, capacity);
            values.insert(
                field,
                aux_record(3, &[], BTreeMap::from([(2, value.to_le_bytes().to_vec())])),
            );
        }
    }
    if let Some(hull) = hull {
        values.insert(8, hull.to_le_bytes().to_vec());
    }
    ship_diff(id, values)
}

fn resource_fraction(value: f32, capacity: f32) -> f32 {
    if capacity > 0. {
        (value / capacity).clamp(0., 1.)
    } else {
        0.
    }
}

fn text(value: &str) -> Vec<u8> {
    let mut data = Vec::new();
    string(&mut data, value);
    data
}

fn empty_inventory(size: usize) -> Vec<u8> {
    let item = aux_record(
        10,
        &[],
        BTreeMap::from([(0, u32::MAX.to_le_bytes().to_vec())]),
    );
    aux_record(
        size,
        &(0..size).collect::<Vec<_>>(),
        (0..size).map(|i| (i, item.clone())).collect(),
    )
}

fn stats(ship: &ShipStats) -> Vec<u8> {
    let fields = [
        (2, ship.impulse_speed as i32),
        (3, ship.warp_speed as i32),
        (4, 1),
        (5, ship.turn_rate as i32),
        (6, ship.scan_range as i32),
        (7, ship.signature as i32),
        (8, ship.deflects[0] as i32),
        (9, ship.deflects[1] as i32),
        (10, ship.deflects[2] as i32),
        (11, ship.deflects[3] as i32),
        (12, ship.deflects[4] as i32),
        (13, ship.deflects[5] as i32),
        (14, ship.deflects[8] as i32),
    ];
    aux_record(
        15,
        &[],
        fields
            .into_iter()
            .map(|(i, value)| (i, value.to_le_bytes().to_vec()))
            .collect(),
    )
}

pub fn player_aux(
    sector: &Sector,
    profile: &CharacterProfile,
    inventory: &Inventory,
    items: &ItemCatalog,
) -> Frame {
    let catalog = SkillCatalog::for_profile(profile).expect("validated built-in class catalog");
    let skills = aux_record(
        64,
        &(0..64).collect::<Vec<_>>(),
        catalog
            .skills
            .iter()
            .map(|skill| {
                (
                    skill.id as usize,
                    aux_record(
                        7,
                        &[],
                        BTreeMap::from([
                            (0, skill.level.to_le_bytes().to_vec()),
                            (2, 1u32.to_le_bytes().to_vec()),
                            (
                                3,
                                [4u32, 0x40, 0, 0]
                                    .into_iter()
                                    .flat_map(u32::to_le_bytes)
                                    .collect(),
                            ),
                            (4, 9u32.to_le_bytes().to_vec()),
                            (5, skill.level.to_le_bytes().to_vec()),
                        ]),
                    ),
                )
            })
            .collect(),
    );
    let abilities = aux_record(
        170,
        &(0..170).collect::<Vec<_>>(),
        catalog
            .abilities
            .iter()
            .map(|ability| {
                (
                    ability.id as usize,
                    aux_record(
                        7,
                        &[],
                        BTreeMap::from([
                            (0, vec![1]),
                            (3, ability.range.to_le_bytes().to_vec()),
                            (4, ability.radius.to_le_bytes().to_vec()),
                        ]),
                    ),
                )
            })
            .collect(),
    );
    let rpg = aux_record(
        16,
        &[2, 3],
        BTreeMap::from([
            (0, profile.race.to_le_bytes().to_vec()),
            (1, profile.profession.to_le_bytes().to_vec()),
            (2, skills),
            (3, abilities),
            (
                7,
                crate::account::SANDBOX_TRACK_LEVEL.to_le_bytes().to_vec(),
            ),
            (
                9,
                crate::account::SANDBOX_TRACK_LEVEL.to_le_bytes().to_vec(),
            ),
            (
                11,
                crate::account::SANDBOX_TRACK_LEVEL.to_le_bytes().to_vec(),
            ),
            (12, 6u32.to_le_bytes().to_vec()),
            (15, u32::MAX.to_le_bytes().to_vec()),
        ]),
    );
    let values = BTreeMap::from([
        (0, inventory.credits.to_le_bytes().to_vec()),
        (2, instance_inventory(&inventory.vault, 96, 10, items)),
        (6, rpg),
        (8, u32::MAX.to_le_bytes().to_vec()),
        (14, text(&sector.name)),
        (15, sector.id.to_le_bytes().to_vec()),
    ]);
    aux_frame(0, aux_record(18, &[2, 3, 4, 5, 6, 9, 10, 17], values))
}

#[derive(Clone, Copy)]
pub struct ShipResources {
    pub energy: f32,
    pub shield: f32,
    pub hull: f32,
}

#[cfg(test)]
fn ship_aux(id: u32, name: &str, shield: f32, hull: f32) -> Frame {
    ship_snapshot(
        id,
        name,
        ShipResources {
            energy: 0.,
            shield,
            hull,
        },
        None,
        &ShipStats::default(),
    )
}

pub fn ship_parameters(id: u32, ship: &ShipStats, resources: ShipResources) -> Frame {
    let mut values = BTreeMap::from([
        (
            4,
            aux_record(
                3,
                &[],
                BTreeMap::from([(
                    2,
                    resource_fraction(resources.energy, ship.energy_capacity)
                        .to_le_bytes()
                        .to_vec(),
                )]),
            ),
        ),
        (
            6,
            aux_record(
                3,
                &[],
                BTreeMap::from([(
                    2,
                    resource_fraction(resources.shield, ship.shield_capacity)
                        .to_le_bytes()
                        .to_vec(),
                )]),
            ),
        ),
        (8, resources.hull.to_le_bytes().to_vec()),
        (
            33,
            u32::from(ship.warp_speed > 0. && resources.hull > 0.)
                .saturating_mul(2)
                .to_le_bytes()
                .to_vec(),
        ),
        (44, stats(ship)),
        (45, stats(ship)),
    ]);
    for (field, value) in [
        (5, ship.energy_capacity),
        (7, ship.shield_capacity),
        (9, ship.hull_capacity),
        (10, ship.turn_rate / 45.),
        (11, ship.turn_rate / 45.),
        (13, ship.impulse_speed),
        (14, -ship.impulse_speed / 2.),
        (15, ship.acceleration),
    ] {
        values.insert(field, value.to_le_bytes().to_vec());
    }
    ship_diff(id, values)
}

pub fn public_ship_parameters(id: u32, ship: &ShipStats) -> Frame {
    let mut values = BTreeMap::from([(44, stats(ship))]);
    for (field, value) in [
        (7, ship.shield_capacity),
        (9, ship.hull_capacity),
        (13, ship.impulse_speed),
        (14, -ship.impulse_speed / 2.),
        (15, ship.acceleration),
    ] {
        values.insert(field, value.to_le_bytes().to_vec());
    }
    ship_diff(id, values)
}

pub fn mob_status(id: u32, mob: &Mob) -> Frame {
    let percent = aux_record(
        3,
        &[],
        BTreeMap::from([(
            2,
            resource_fraction(mob.shield, mob.max_shield)
                .to_le_bytes()
                .to_vec(),
        )]),
    );
    aux_frame(
        id,
        aux_record(
            58,
            &[4, 6, 35, 36, 37, 38, 39, 40, 41, 42, 44, 45],
            BTreeMap::from([
                (0, text(&mob.definition.name)),
                (6, percent),
                (7, mob.max_shield.to_le_bytes().to_vec()),
                (8, mob.health.to_le_bytes().to_vec()),
                (9, mob.max_health.to_le_bytes().to_vec()),
                (26, mob.definition.level.to_le_bytes().to_vec()),
                (27, u32::MAX.to_le_bytes().to_vec()),
            ]),
        ),
    )
}

pub fn mob_spawn(id: u32, mob: &Mob, tick: u32, reaction: u32) -> Vec<Frame> {
    let mut model = create(id, mob.definition.asset);
    // MOB::MOB uses Create.Type=0. Type 1 creates a player actor, whose
    // avatar association is absent for MOBs and leaves "Default" labels.
    model.body[10] = 0;
    float(&mut model.body, 4, mob.definition.scale);
    for (index, value) in mob.definition.hsv.iter().enumerate() {
        float(&mut model.body, 11 + index * 4, *value);
    }
    vec![
        model,
        relationship(id, reaction),
        mob_movement(id, mob, tick),
        mob_status(id, mob),
    ]
}

/// NPCs use the native velocity/quaternion fields just like player ships.
pub fn mob_movement(id: u32, mob: &Mob, tick: u32) -> Frame {
    position_update(id, mob.position, tick, Some(&mob.motion))
}

/// Original ObjectToObjectEffect layout, using only documented fields 0..2.
pub fn beam(source: u32, target: u32, asset: u16, effect: u32, tick: u32) -> Frame {
    weapon_effect(source, target, asset, effect, tick, 500)
}

/// Player::SendClientDamage: float damage/modifier, then type, inflicted,
/// source and target. These fields are little endian, unlike many AUX IDs.
pub fn client_damage(
    target: u32,
    source: u32,
    damage: f32,
    modifier: f32,
    damage_type: u32,
    inflicted: u32,
) -> Frame {
    let mut body = Vec::with_capacity(24);
    body.extend_from_slice(&damage.to_le_bytes());
    body.extend_from_slice(&modifier.to_le_bytes());
    for value in [damage_type, inflicted, source, target] {
        body.extend_from_slice(&value.to_le_bytes());
    }
    Frame::new(0x64, body)
}

pub fn weapon_effect(
    source: u32,
    target: u32,
    asset: u16,
    effect: u32,
    tick: u32,
    duration: u16,
) -> Frame {
    let mut body = Vec::from(7u16.to_le_bytes());
    body.extend_from_slice(&source.to_le_bytes());
    body.extend_from_slice(&target.to_le_bytes());
    body.extend_from_slice(&asset.to_le_bytes());
    body.push(0); // empty NUL-terminated message
    body.extend_from_slice(&effect.to_le_bytes());
    body.extend_from_slice(&tick.to_le_bytes());
    body.extend_from_slice(&duration.to_le_bytes());
    Frame::new(0x0b, body)
}

fn instance_inventory(
    items: &[Option<ItemInstance>],
    size: usize,
    item_fields: usize,
    catalog: &ItemCatalog,
) -> Vec<u8> {
    encode_instance_inventory(items, size, item_fields, catalog, u32::MAX)
}

fn encode_instance_inventory(
    items: &[Option<ItemInstance>],
    size: usize,
    item_fields: usize,
    catalog: &ItemCatalog,
    empty_template: u32,
) -> Vec<u8> {
    let values = (0..size)
        .map(|slot| {
            let item = items.get(slot).and_then(Option::as_ref);
            let mut fields = BTreeMap::from([(
                0,
                item.map_or(empty_template, |item| item.template)
                    .to_le_bytes()
                    .to_vec(),
            )]);
            if let Some(item) = item {
                fields.insert(1, item.quantity.to_le_bytes().to_vec());
                fields.insert(4, item.structure.to_le_bytes().to_vec());
                fields.insert(5, item.quality.to_le_bytes().to_vec());
                if !item.builder_name.is_empty() {
                    fields.insert(9, text(&item.builder_name));
                }
                let info = catalog
                    .get(item.template)
                    .expect("validated inventory template")
                    .instance_info(item.quality);
                if !info.is_empty() {
                    fields.insert(6, text(&info));
                }
            }
            // AuxItem has ten fields. Only AuxEquipItem adds four more,
            // including nested field 13. Mixing them desynchronizes the rest
            // of the AUX packet even when all item values happen to match.
            (
                slot,
                aux_record(
                    item_fields,
                    if item_fields == 14 { &[13] } else { &[] },
                    fields,
                ),
            )
        })
        .collect();
    aux_record(size, &(0..size).collect::<Vec<_>>(), values)
}

pub fn inventory_update(
    id: u32,
    inventory: &Inventory,
    catalog: &ItemCatalog,
    stats: &ShipStats,
) -> Frame {
    ship_diff(
        id,
        BTreeMap::from([(
            36,
            aux_diff(
                11,
                BTreeMap::from([
                    (
                        2,
                        equipment_mounts(stats.weapon_mounts, stats.device_mounts),
                    ),
                    (6, instance_inventory(&inventory.cargo, 40, 10, catalog)),
                    (7, instance_inventory(&inventory.equipped, 20, 14, catalog)),
                    (
                        8,
                        instance_inventory(&inventory.ammunition, 20, 10, catalog),
                    ),
                ]),
            ),
        )]),
    )
}

pub fn resource_contents(node: &crate::resources::ResourceState, catalog: &ItemCatalog) -> Frame {
    aux_frame(
        node.definition.object.id,
        aux_record(
            4,
            &[1],
            BTreeMap::from([
                (0, text(&node.definition.object.name)),
                (
                    1,
                    encode_instance_inventory(&node.contents, 40, 10, catalog, u32::MAX - 1),
                ),
                (2, node.fraction().to_le_bytes().to_vec()),
                (3, u32::from(node.definition.level).to_le_bytes().to_vec()),
            ]),
        ),
    )
}

pub fn corpse_create(corpse: &crate::loot_world::Corpse) -> Frame {
    map_object(&NavPoint {
        id: corpse.id,
        name: corpse.name.clone(),
        object_type: 25,
        asset: corpse.initial.asset,
        radius: 0., // This map packet carries no collision geometry field.
        position: corpse.position,
        scale: 20.,
        orientation: [0., 0., 0., 1.],
        hsv: [0.; 3],
        signature: 5000.,
        navigation_present: false,
        navigation_type: 0,
        huge: false,
        gate_destination: None,
    })
}

pub fn corpse_name(corpse: &crate::loot_world::Corpse) -> Frame {
    // Native Husk AUX differs from AuxHarvestable. These top-level flags and
    // four empty nested records are SendHuskName's archived layout.
    let mut record = vec![0x16, 0xe0, 0x03];
    record.extend(text(&corpse.name));
    record.extend([5; 4]);
    aux_frame(corpse.id, record)
}

pub fn corpse_contents(
    corpse: &crate::loot_world::Corpse,
    stock: &crate::loot::CorpseContents,
    catalog: &ItemCatalog,
) -> Frame {
    // SendHuskContent: name, four reset records, empty 20-slot equipment-like
    // inventory, then the 40-slot corpse inventory. Preserve its native envelope.
    let mut record = vec![0x16, 0xec, 0x0f];
    record.extend(text(&corpse.name));
    record.extend([5; 4]);
    record.extend(encode_instance_inventory(
        &[],
        20,
        10,
        catalog,
        u32::MAX - 1,
    ));
    record.extend(encode_instance_inventory(
        &stock.items,
        40,
        10,
        catalog,
        u32::MAX - 1,
    ));
    aux_frame(corpse.id, record)
}

pub fn resource_level(id: u32, fraction: f32, level: u16) -> Frame {
    aux_frame(
        id,
        aux_diff(
            4,
            BTreeMap::from([
                (2, fraction.to_le_bytes().to_vec()),
                (3, u32::from(level).to_le_bytes().to_vec()),
            ]),
        ),
    )
}

pub fn resource_interface(open: bool) -> Frame {
    Frame::new(
        0x66,
        [u32::from(!open), 0]
            .into_iter()
            .flat_map(u32::to_le_bytes)
            .collect(),
    )
}

pub fn manufacturing_lab(id: u32) -> Frame {
    let mut frame = create(id, u16::MAX);
    frame.body[10] = 0x27; // CREATE_MANU_LAB, not a starship or map decoration.
    frame
}

pub fn workbench_snapshot(
    id: u32,
    view: &crate::production::WorkbenchView,
    items: &ItemCatalog,
) -> Frame {
    let target = if matches!(view.mode, 2 | 3) {
        view.target.clone()
    } else {
        (view.selected != 0).then_some(ItemInstance {
            template: view.selected,
            quantity: u32::from(view.ready),
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        })
    };
    let components: Vec<Option<ItemInstance>> = view
        .components
        .iter()
        .map(|(template, quantity)| {
            Some(ItemInstance {
                template: *template,
                quantity: *quantity,
                quality: 1.,
                structure: 1.,
                builder_name: String::new(),
            })
        })
        .collect();
    let mut values = BTreeMap::from([
        (0, text("Manufacturing Lab")),
        (1, view.mode.to_le_bytes().to_vec()),
        (2, view.validity.to_le_bytes().to_vec()),
        (3, text(&view.failure)),
        (4, 1u32.to_le_bytes().to_vec()),
        (5, instance_inventory(&[target], 1, 10, items)),
        (6, instance_inventory(&components, 6, 10, items)),
        (7, instance_inventory(&[], 1, 10, items)),
        (8, view.cost.to_le_bytes().to_vec()),
        (9, view.base_cost.to_le_bytes().to_vec()),
        (10, manufacturing_categories(&view.available_categories)),
        (13, view.category.to_le_bytes().to_vec()),
        (19, view.additional.to_le_bytes().to_vec()),
        (20, view.filter.to_le_bytes().to_vec()),
    ]);
    for (index, value) in [(14, view.chance), (15, 0.), (16, 1.), (17, 1.), (18, 1.)] {
        values.insert(index, value.to_le_bytes().to_vec());
    }
    aux_frame(id, aux_record(21, &[5, 6, 7, 10, 11, 12], values))
}

fn manufacturing_categories(available: &[(i32, i32)]) -> Vec<u8> {
    let primary = [("Items", false), ("Components", true)]
        .into_iter()
        .enumerate()
        .map(|(index, (name, components))| {
            let categories = crate::production::CATEGORIES
                .iter()
                .filter(|category| (category.id >= 50) == components)
                .enumerate()
                .map(|(index, category)| {
                    let subcategories = category
                        .subcategories
                        .iter()
                        .enumerate()
                        .map(|(index, (id, name))| {
                            let visible = available
                                .iter()
                                .any(|(cat, sub)| *cat == category.id as i32 && *sub == *id as i32);
                            (
                                index,
                                aux_record(
                                    3,
                                    &[],
                                    BTreeMap::from([
                                        (0, text(name)),
                                        (1, id.to_le_bytes().to_vec()),
                                        (2, vec![u8::from(visible)]),
                                    ]),
                                ),
                            )
                        })
                        .collect();
                    (
                        index,
                        aux_record(
                            3,
                            &[1],
                            BTreeMap::from([
                                (0, text(category.name)),
                                (1, aux_record(5, &[0, 1, 2, 3, 4], subcategories)),
                                (2, category.id.to_le_bytes().to_vec()),
                            ]),
                        ),
                    )
                })
                .collect();
            (
                index,
                aux_record(
                    2,
                    &[1],
                    BTreeMap::from([
                        (0, text(name)),
                        (1, aux_record(5, &[0, 1, 2, 3, 4], categories)),
                    ]),
                ),
            )
        })
        .collect();
    aux_record(2, &[0, 1], primary)
}

pub fn workbench_formulas(
    id: u32,
    products: &[u32],
    previous_count: usize,
    items: &ItemCatalog,
) -> Frame {
    let values = (0..products.len().max(previous_count).min(500))
        .map(|index| {
            let fields = if let Some(product) = products.get(index) {
                let item = items.get(*product).expect("validated refinery product");
                BTreeMap::from([
                    (0, text(&item.name[..item.name.len().min(31)])),
                    (1, product.to_le_bytes().to_vec()),
                    (2, u32::from(item.level).to_le_bytes().to_vec()),
                ])
            } else {
                BTreeMap::new()
            };
            (index, aux_record(3, &[], fields))
        })
        .collect();
    aux_frame(
        id,
        aux_diff(21, BTreeMap::from([(11, aux_diff(500, values))])),
    )
}

pub fn start_prospect(
    player: u32,
    target: u32,
    effect: u32,
    tick: u32,
    milliseconds: u32,
) -> Frame {
    Frame::new(
        0x2012,
        [player, target, effect, tick, milliseconds]
            .into_iter()
            .flat_map(u32::to_le_bytes)
            .collect(),
    )
}

pub struct OreTractor<'a> {
    pub player: u32,
    pub article: u32,
    pub effect: u32,
    pub tick: u32,
    pub milliseconds: u32,
    pub speed: f32,
    pub position: [f32; 3],
    pub item: &'a ItemTemplate,
}

pub fn tractor_ore(tractor: &OreTractor<'_>) -> Frame {
    let mut body: Vec<u8> = [tractor.player, tractor.article, tractor.effect]
        .into_iter()
        .flat_map(u32::to_le_bytes)
        .collect();
    body.extend_from_slice(&tractor.item.asset.to_le_bytes());
    body.extend_from_slice(&tractor.tick.to_le_bytes());
    string(&mut body, &tractor.item.name);
    body.extend_from_slice(&tractor.milliseconds.to_le_bytes());
    body.extend_from_slice(&tractor.speed.to_le_bytes());
    for value in tractor.position {
        body.extend_from_slice(&value.to_le_bytes());
    }
    Frame::new(0x2013, body)
}

pub fn vault_update(inventory: &Inventory, catalog: &ItemCatalog) -> Frame {
    aux_frame(
        0,
        aux_diff(
            18,
            BTreeMap::from([
                (0, inventory.credits.to_le_bytes().to_vec()),
                (2, instance_inventory(&inventory.vault, 96, 10, catalog)),
            ]),
        ),
    )
}

/// Vendor slots use AuxItem (10 fields), not equipped-item records. Empty
/// vendor slots have the original invisible sentinel -2 rather than cargo -1.
pub fn vendor_inventory(offers: &[crate::npcs::VendorOffer]) -> Frame {
    let values = (0..128)
        .map(|slot| {
            let offer = offers.get(slot);
            let fields = BTreeMap::from([
                (
                    0,
                    offer
                        .map_or(u32::MAX - 1, |offer| offer.item)
                        .to_le_bytes()
                        .to_vec(),
                ),
                (1, 1u32.to_le_bytes().to_vec()),
                (
                    2,
                    offer.map_or(0, |offer| offer.price).to_le_bytes().to_vec(),
                ),
                (4, 1f32.to_le_bytes().to_vec()),
                (5, 1f32.to_le_bytes().to_vec()),
            ]);
            (slot, aux_record(10, &[], fields))
        })
        .collect();
    aux_frame(
        0,
        aux_diff(
            18,
            BTreeMap::from([(3, aux_record(128, &(0..128).collect::<Vec<_>>(), values))]),
        ),
    )
}

/// Quotes are transient NPC context. Never store them as owned item state.
/// Explicit zeroes also clear prices when leaving the vendor or switching NPCs.
pub fn inventory_prices(
    id: u32,
    inventory: &Inventory,
    prices: &std::collections::HashMap<u32, u64>,
) -> [Frame; 2] {
    let quote = |items: &[Option<ItemInstance>], size: usize, fields: usize| {
        aux_diff(
            size,
            (0..size)
                .map(|slot| {
                    let price = items
                        .get(slot)
                        .and_then(Option::as_ref)
                        .and_then(|item| prices.get(&item.template))
                        .copied()
                        .unwrap_or(0);
                    (
                        slot,
                        aux_diff(fields, BTreeMap::from([(2, price.to_le_bytes().to_vec())])),
                    )
                })
                .collect(),
        )
    };
    [
        ship_diff(
            id,
            BTreeMap::from([(
                36,
                aux_diff(
                    11,
                    BTreeMap::from([
                        (6, quote(&inventory.cargo, 40, 10)),
                        (7, quote(&inventory.equipped, 20, 14)),
                    ]),
                ),
            )]),
        ),
        aux_frame(
            0,
            aux_diff(18, BTreeMap::from([(2, quote(&inventory.vault, 96, 10))])),
        ),
    ]
}

pub fn talk_tree(text: &str, branches: &[(u8, String)]) -> Frame {
    let mut body = text.as_bytes().to_vec();
    body.extend_from_slice(&[0, 0, branches.len() as u8]);
    for (node, label) in branches {
        body.extend_from_slice(&u32::from(*node).to_le_bytes());
        body.extend_from_slice(label.as_bytes());
        body.push(0);
    }
    Frame::new(0x54, body)
}

pub fn talk_control(action: i32) -> Frame {
    Frame::new(0x56, action.to_le_bytes().to_vec())
}

pub fn weapon_state(id: u32, slot: usize, ready: u32, range: f32, state: u32) -> Frame {
    ship_diff(
        id,
        BTreeMap::from([(
            36,
            aux_diff(
                11,
                BTreeMap::from([(
                    7,
                    aux_diff(
                        20,
                        BTreeMap::from([(
                            slot,
                            aux_diff(
                                14,
                                BTreeMap::from([
                                    (10, ready.to_le_bytes().to_vec()),
                                    (11, range.to_le_bytes().to_vec()),
                                    (12, state.to_le_bytes().to_vec()),
                                ]),
                            ),
                        )]),
                    ),
                )]),
            ),
        )]),
    )
}

fn equipment_mounts(weapons: usize, devices: usize) -> Vec<u8> {
    let mut mounts = BTreeMap::new();
    let slots = [(0, 4u32), (1, 0x80), (2, 0x40)]
        .into_iter()
        .chain((3..3 + weapons).map(|slot| (slot, 0x1c002)))
        .chain((9..9 + devices).map(|slot| (slot, 0x20809)));
    for (slot, mask) in slots {
        let mut data = Vec::from(mask.to_le_bytes());
        data.extend_from_slice(&u32::MAX.to_le_bytes());
        data.extend_from_slice(&u32::MAX.to_le_bytes());
        mounts.insert(slot, data);
    }
    aux_record(20, &[], mounts)
}

struct InventoryEncoding<'a> {
    inventory: &'a Inventory,
    catalog: &'a ItemCatalog,
}

fn ship_snapshot(
    id: u32,
    name: &str,
    resources: ShipResources,
    inventory: Option<InventoryEncoding<'_>>,
    ship: &ShipStats,
) -> Frame {
    let percent =
        |value: f32| aux_record(3, &[], BTreeMap::from([(2, value.to_le_bytes().to_vec())]));
    let mut inventory_fields = BTreeMap::from([
        (0, 22u32.to_le_bytes().to_vec()),
        (1, text("tvf01_1")),
        (4, (ship.weapon_mounts as u32).to_le_bytes().to_vec()),
        (5, (ship.device_mounts as u32).to_le_bytes().to_vec()),
        (6, empty_inventory(40)),
        (8, empty_inventory(20)),
        (10, empty_inventory(6)),
    ]);
    if let Some(encoding) = inventory {
        let inventory = encoding.inventory;
        inventory_fields.insert(2, equipment_mounts(ship.weapon_mounts, ship.device_mounts));
        inventory_fields.insert(
            6,
            instance_inventory(&inventory.cargo, 40, 10, encoding.catalog),
        );
        inventory_fields.insert(
            7,
            instance_inventory(&inventory.equipped, 20, 14, encoding.catalog),
        );
        inventory_fields.insert(
            8,
            instance_inventory(&inventory.ammunition, 20, 10, encoding.catalog),
        );
    }
    let inventory = aux_record(11, &[2, 3, 6, 7, 8, 9, 10], inventory_fields);
    let mut values = BTreeMap::from([
        (0, text(name)),
        (
            1,
            text(if id & 0xff000000 == 0x40000000 {
                name
            } else {
                ""
            }),
        ),
        (
            4,
            percent(resource_fraction(resources.energy, ship.energy_capacity)),
        ),
        (
            6,
            percent(resource_fraction(resources.shield, ship.shield_capacity)),
        ),
        (27, u32::MAX.to_le_bytes().to_vec()),
        (
            33,
            (if ship.warp_speed > 0. && resources.hull > 0. {
                2u32
            } else {
                0
            })
            .to_le_bytes()
            .to_vec(),
        ),
        (36, inventory),
        (16, vec![u8::from(resources.hull <= 0.)]),
        (17, vec![u8::from(resources.hull <= 0.)]),
        (21, vec![u8::from(resources.hull <= 0.)]),
        (44, stats(ship)),
        (45, stats(ship)),
    ]);
    for (field, value) in [
        (5, ship.energy_capacity),
        (7, ship.shield_capacity),
        (8, resources.hull),
        (9, ship.hull_capacity),
        (10, ship.turn_rate / 45.),
        (11, ship.turn_rate / 45.),
        (12, 1.5),
        (13, ship.impulse_speed),
        (14, -ship.impulse_speed / 2.),
        (15, ship.acceleration),
    ] {
        values.insert(field, value.to_le_bytes().to_vec());
    }
    values.insert(18, vec![1]);
    aux_frame(
        id,
        aux_record(58, &[4, 6, 35, 36, 37, 38, 39, 40, 41, 42, 44, 45], values),
    )
}

pub fn server_parameters(sector: &Sector) -> Frame {
    let mut data = vec![0; 70];
    for (offset, value) in [
        (0, sector.bounds.min[2]),
        (4, sector.bounds.max[2]),
        (8, sector.bounds.min[0]),
        (12, sector.bounds.min[1]),
        (16, sector.bounds.max[0]),
        (20, sector.bounds.max[1]),
        (24, 100000.),
        (28, 100000.),
        (47, 1.22137),
        (52, 250.),
        (56, 9.33033),
        (60, 9.33033),
    ] {
        float(&mut data, offset, value);
    }
    data[51] = 1;
    // A zero backdrop leaves the legacy renderer with no skybox to draw.
    // Asset 220 is the public database's Asteroid Belt Alpha skybox.
    data[64..66].copy_from_slice(&sector.backdrop_asset.to_le_bytes());
    put32(&mut data, 66, sector.id);
    Frame::new(0x42, data)
}

pub fn message(value: &str) -> Frame {
    let mut data = Vec::from(((value.len() + 1) as u16).to_le_bytes());
    data.push(11);
    data.extend_from_slice(value.as_bytes());
    data.push(0);
    Frame::new(0x1d, data)
}

/// UpdateVerbs has two counted lists: default disabled state then current
/// state. IDs/counts use network byte order; each attribute/verb pair is LE.
pub fn target_verbs(target: u32, verbs: &[(u16, u16)]) -> Frame {
    let mut body = Vec::from(target.to_be_bytes());
    for defaults in [true, false] {
        body.extend_from_slice(&(verbs.len() as u32).to_be_bytes());
        for (verb, attribute) in verbs {
            body.extend_from_slice(&(if defaults { 2u16 } else { *attribute }).to_le_bytes());
            body.extend_from_slice(&verb.to_le_bytes());
        }
    }
    Frame::new(0x5c, body)
}

pub fn gate_render_state(gate: u32, state: u32) -> Frame {
    Frame::new(0x31, [gate.to_le_bytes(), state.to_le_bytes()].concat())
}
pub fn gate_camera(state: u32, target: u32) -> Frame {
    Frame::new(0x92, [state.to_be_bytes(), target.to_be_bytes()].concat())
}

/// Group the private ship initialization inputs so packet encoding has one
/// explicit snapshot, instead of an expanding list of positional parameters.
pub struct ShipInitialization<'a> {
    pub catalog: &'a ItemCatalog,
    pub item_templates: Vec<&'a ItemTemplate>,
    pub inventory: &'a Inventory,
    pub stats: &'a ShipStats,
    pub resources: ShipResources,
}

pub fn login_world(
    sector: &Sector,
    position: [f32; 3],
    tick: u32,
    profile: &CharacterProfile,
    motion: &Motion,
    ship: &ShipInitialization<'_>,
) -> Vec<Frame> {
    let inventory = ship.inventory;
    let mut avatar_data = Vec::from(profile.game_id.to_le_bytes());
    avatar_data.extend(avatar(profile));
    avatar_data.extend_from_slice(&[0; 7]);
    avatar_data.extend_from_slice(&1f32.to_le_bytes());
    avatar_data.extend_from_slice(&1f32.to_le_bytes());
    let mut frames: Vec<Frame> = ship
        .item_templates
        .iter()
        .map(|item| item.packet())
        .collect();
    if let Some(station) = &sector.station {
        // StationLogin/StationLogin2: the client owns interior rendering. Do not
        // send a space ClientType, map objects or space ServerParameters here.
        frames.extend([
            player_aux(sector, profile, inventory, ship.catalog),
            create(profile.game_id, profile.class().hull_asset()),
            ship_subparts(profile.game_id, profile.class()),
            ship_colors(profile.game_id),
            Frame::new(0x37, profile.game_id.to_le_bytes().to_vec()),
            Frame::new(0x47, profile.game_id.to_le_bytes().to_vec()),
            Frame::new(0x61, avatar_data),
            ship_snapshot(
                profile.game_id,
                &profile.name,
                ship.resources,
                Some(InventoryEncoding {
                    inventory,
                    catalog: ship.catalog,
                }),
                ship.stats,
            ),
            starbase_set(station.id, 0),
            station_layout(station),
            message(&format!(
                "Welcome aboard {}. Station services are under development.",
                station.name
            )),
            Frame::new(5, (profile.game_id & 0xffffff).to_le_bytes().to_vec()),
        ]);
        return frames;
    }
    frames.extend([
        sector_description(sector, profile.game_id),
        Frame::new(0x7f, 0u32.to_le_bytes().to_vec()),
        Frame::new(0x3c, 0u32.to_le_bytes().to_vec()),
        player_aux(sector, profile, inventory, ship.catalog),
        create(profile.game_id, profile.class().hull_asset()),
        ship_subparts(profile.game_id, profile.class()),
        ship_colors(profile.game_id),
        Frame::new(0x37, profile.game_id.to_le_bytes().to_vec()),
        Frame::new(0x47, profile.game_id.to_le_bytes().to_vec()),
        Frame::new(0x61, avatar_data),
        player_movement(profile.game_id, position, tick, motion),
        ship_snapshot(
            profile.game_id,
            &profile.name,
            ship.resources,
            Some(InventoryEncoding {
                inventory,
                catalog: ship.catalog,
            }),
            ship.stats,
        ),
        server_parameters(sector),
    ]);
    for nav in &sector.navpoints {
        frames.push(map_object(nav));
        if matches!(nav.object_type, 11 | 12) {
            let mut relation = Vec::from(nav.id.to_be_bytes());
            relation.extend_from_slice(&2u32.to_le_bytes());
            relation.push(0);
            frames.push(Frame::new(0x89, relation));
        }
        if nav.object_type == 11 {
            for (opcode, state) in [(0x30, 1u32), (0x31, 3)] {
                let mut data = Vec::from(nav.id.to_le_bytes());
                data.extend_from_slice(&state.to_le_bytes());
                frames.push(Frame::new(opcode, data));
            }
        }
    }
    frames.push(message(
        "Welcome to the local Rust sandbox. No connection to the live game service.",
    ));
    frames.push(Frame::new(
        5,
        (profile.game_id & 0xffffff).to_le_bytes().to_vec(),
    ));
    frames
}

pub fn starbase_set(station: u32, action: u8) -> Frame {
    let mut body = Vec::from(station.to_le_bytes());
    body.extend_from_slice(&[action, 0]);
    Frame::new(0x4f, body)
}

pub fn station_layout(station: &crate::content::Station) -> Frame {
    // Packed StationData, StationRooms[room count], terminal count/records,
    // followed by NPC count/records. Avatars are exported from public appearance
    // templates; dialogue and vendor services use separate request packets.
    let mut body = Vec::from(station.kind.to_le_bytes());
    body.extend_from_slice(&(station.rooms.len() as u32).to_le_bytes());
    for room in &station.rooms {
        body.extend_from_slice(&room.index.to_le_bytes());
        body.extend_from_slice(&room.style.to_le_bytes());
        body.extend_from_slice(&room.fog_near.to_le_bytes());
        body.extend_from_slice(&room.fog_far.to_le_bytes());
        for color in room.fog_color {
            body.extend_from_slice(&color.to_le_bytes());
        }
    }
    body.extend_from_slice(&(station.terminals.len() as u32).to_le_bytes());
    for terminal in &station.terminals {
        for value in [terminal.room, terminal.location, terminal.kind, 0] {
            body.extend_from_slice(&value.to_le_bytes());
        }
    }
    body.extend_from_slice(&(station.npcs.len() as u32).to_le_bytes());
    for npc in &station.npcs {
        for value in [npc.room, npc.location, npc.id] {
            body.extend_from_slice(&value.to_le_bytes());
        }
        body.extend_from_slice(&npc.booth.to_le_bytes());
        body.extend_from_slice(&[0; 8]);
        body.extend_from_slice(&npc.avatar);
    }
    Frame::new(0x52, body)
}

/// Packed StationAvatarChange_S2C places room last, unlike the incoming pose.
pub fn station_avatar_pose(id: u32, pose: &crate::peers::StationPose) -> Frame {
    let mut body = Vec::from(id.to_le_bytes());
    body.extend_from_slice(&pose.orientation.to_le_bytes());
    for coordinate in pose.position {
        body.extend_from_slice(&coordinate.to_le_bytes());
    }
    body.extend_from_slice(&pose.action.to_le_bytes());
    body.extend_from_slice(&pose.room.to_le_bytes());
    Frame::new(0x9e, body)
}

pub fn station_avatar_room(id: u32, new_room: u32, old_room: u32) -> Frame {
    Frame::new(
        0xa0,
        [id, new_room, old_room]
            .into_iter()
            .flat_map(u32::to_le_bytes)
            .collect(),
    )
}

/// Station avatars need no ship Create, equipment, resource or impulse data.
pub fn station_avatar_description(profile: &CharacterProfile) -> Frame {
    let mut body = Vec::from(profile.game_id.to_le_bytes());
    body.extend(avatar(profile));
    body.extend_from_slice(&[0; 7]);
    body.extend_from_slice(&1f32.to_le_bytes());
    body.extend_from_slice(&1f32.to_le_bytes());
    Frame::new(0x61, body)
}

pub fn peer_spawn(peer: &crate::peers::PeerSnapshot, tick: u32) -> Vec<Frame> {
    let id = peer.profile.game_id;
    let mut avatar_data = Vec::from(id.to_le_bytes());
    avatar_data.extend(avatar(&peer.profile));
    avatar_data.extend_from_slice(&[0; 7]);
    avatar_data.extend_from_slice(&1f32.to_le_bytes());
    avatar_data.extend_from_slice(&1f32.to_le_bytes());
    vec![
        create(id, peer.profile.class().hull_asset()),
        ship_subparts(id, peer.profile.class()),
        ship_colors(id),
        Frame::new(0x61, avatar_data),
        player_movement(id, peer.position, tick, &peer.motion),
        ship_snapshot(
            id,
            &peer.profile.name,
            ShipResources {
                energy: 0.,
                shield: peer.shield,
                hull: peer.hull,
            },
            None,
            &peer.stats,
        ),
        cloak(id, peer.cloaked),
        incapacitated(id, peer.hull <= 0.),
    ]
}

#[cfg(test)]
mod tests {
    #[test]
    fn station_avatar_packets_use_cpp_room_and_pose_field_order() {
        let pose = crate::peers::StationPose {
            room: 2,
            orientation: 0.5,
            position: [1., 2., 3.],
            action: 0x41,
        };
        let frame = station_avatar_pose(0x40000002, &pose);
        let mut expected = Vec::from(0x40000002u32.to_le_bytes());
        expected.extend([0.5f32, 1., 2., 3.].into_iter().flat_map(f32::to_le_bytes));
        expected.extend([0x41u32, 2].into_iter().flat_map(u32::to_le_bytes));
        assert_eq!(frame, Frame::new(0x9e, expected));
        assert_eq!(
            station_avatar_room(0x40000002, 2, u32::MAX).body,
            [0x40000002u32, 2, u32::MAX]
                .into_iter()
                .flat_map(u32::to_le_bytes)
                .collect::<Vec<_>>()
        );
    }
    #[test]
    fn resource_aux_encodes_shield_and_energy_fractions_but_absolute_hull() {
        let stats = ShipStats {
            energy_capacity: 142.,
            shield_capacity: 45.,
            ..ShipStats::default()
        };
        let frame = ship_resources(0x40000001, &stats, Some(71.), Some(9.), Some(100.));
        // ShipIndex one-bit flags: 8 bytes. Each AuxPercent reset record has
        // two flag bytes and a four-byte StartValue, in field order 4,6,8.
        assert_eq!(
            f32::from_le_bytes(frame.body[17..21].try_into().unwrap()),
            0.5
        );
        assert_eq!(
            f32::from_le_bytes(frame.body[23..27].try_into().unwrap()),
            0.2
        );
        assert_eq!(
            f32::from_le_bytes(frame.body[27..31].try_into().unwrap()),
            100.
        );
        assert_eq!(resource_fraction(1., 0.), 0.);
        assert_eq!(resource_fraction(100., 45.), 1.);
        assert_eq!(resource_fraction(-1., 45.), 0.);
    }
    #[test]
    fn peer_parameter_delta_updates_caps_without_private_inventory_or_energy() {
        let stats = ShipStats {
            shield_capacity: 250.,
            hull_capacity: 1000.,
            impulse_speed: 192.,
            ..ShipStats::default()
        };
        let frame = public_ship_parameters(0x40000002, &stats);
        let present =
            |field: usize| frame.body[7 + (field + 4) / 8] & (1 << ((field + 4) % 8)) != 0;
        for field in [7, 9, 13, 14, 15, 44] {
            assert!(present(field));
        }
        for field in [4, 5, 36] {
            assert!(!present(field));
        }
    }
    #[test]
    fn cargo_and_equipment_match_distinct_cpp_item_record_layouts() {
        let item = ItemInstance {
            template: 2505,
            quantity: 1,
            structure: 0.5,
            quality: 1.25,
            builder_name: String::new(),
        };
        let mut values = Vec::new();
        values.extend_from_slice(&2505u32.to_le_bytes());
        values.extend_from_slice(&1u32.to_le_bytes());
        values.extend_from_slice(&0.5f32.to_le_bytes());
        values.extend_from_slice(&1.25f32.to_le_bytes());
        // C++ AuxItem: ten fields, three flag bytes, Structure before Quality.
        values.extend_from_slice(&17u16.to_le_bytes());
        values.extend_from_slice(b"31:67.00^34:4.57^");
        let catalog = ItemCatalog::load().unwrap();
        let mut cargo = vec![0x16, 0x36, 0x07, 0xe3];
        cargo.extend_from_slice(&values);
        assert_eq!(
            instance_inventory(&[Some(item.clone())], 1, 10, &catalog),
            cargo
        );
        // AuxEquipItem: fourteen fields, four flag bytes, nested field 13 reset.
        let mut equipped = vec![0x16, 0x36, 0x07, 0x30, 0xfe];
        equipped.extend_from_slice(&values);
        equipped.push(5);
        assert_eq!(
            instance_inventory(&[Some(item.clone())], 1, 14, &catalog),
            equipped
        );
        let mut crafted = item;
        crafted.builder_name = "Third".into();
        let mut made_cargo = vec![0x16, 0x36, 0x27, 0x63];
        made_cargo.extend_from_slice(&values);
        made_cargo.extend_from_slice(&5u16.to_le_bytes());
        made_cargo.extend_from_slice(b"Third");
        assert_eq!(
            instance_inventory(&[Some(crafted.clone())], 1, 10, &catalog),
            made_cargo
        );
        let mut made_equipped = vec![0x16, 0x36, 0x27, 0x30, 0xf6];
        made_equipped.extend_from_slice(&values);
        made_equipped.extend_from_slice(&5u16.to_le_bytes());
        made_equipped.extend_from_slice(b"Third");
        made_equipped.push(5);
        assert_eq!(
            instance_inventory(&[Some(crafted)], 1, 14, &catalog),
            made_equipped
        );
    }
    #[test]
    fn ordinary_static_navs_set_native_name_signature_marker_and_refresh_without_create() {
        let cooper = crate::sectors::SectorCatalog::load()
            .unwrap()
            .sector(4535)
            .unwrap();
        let nav = cooper
            .navpoints
            .iter()
            .find(|point| {
                point.object_type == 37 && point.navigation_present && point.navigation_type == 1
            })
            .unwrap();
        let compact = map_object(nav);
        assert_eq!(compact.body[57] & 0x30, 0x30);
        let refresh = static_navigation_refresh(nav);
        assert_eq!(
            refresh.iter().map(|frame| frame.opcode).collect::<Vec<_>>(),
            [0x1b, 0x99]
        );
        let mut aux = crate::protocol::Reader::new(&refresh[0].body);
        assert_eq!(aux.u32().unwrap(), nav.id);
        assert_eq!(aux.u16().unwrap() as usize, nav.name.len() + 9);
        assert_eq!(aux.take(2).unwrap(), [1, 0x72]);
        assert_eq!(aux.string().unwrap(), nav.name);
        assert_eq!(aux.take(1).unwrap(), [1]);
        assert_eq!(aux.f32().unwrap(), nav.signature);
        assert!(aux.rest().is_empty());
        let mut map = crate::protocol::Reader::new(&refresh[1].body);
        assert_eq!(map.u32().unwrap(), nav.id);
        assert_eq!(map.f32().unwrap(), nav.signature);
        assert_eq!(map.take(1).unwrap(), [1]);
        assert_eq!(map.u32().unwrap(), 1);
        assert_eq!(map.take(1).unwrap(), [u8::from(nav.huge)]);
        assert!(map.rest().is_empty());
        let mut hidden = nav.clone();
        hidden.navigation_present = false;
        assert_eq!(map_object(&hidden).body[57] & 0x30, 0x20);
        assert_eq!(static_navigation_refresh(&hidden).len(), 1);
        hidden.navigation_type = 0;
        assert_eq!(map_object(&hidden).body[57] & 0x30, 0);
    }

    #[test]
    fn database_only_actor_types_do_not_reach_native_create() {
        let sector: super::Sector =
            serde_json::from_str(include_str!("../data/sector.json")).unwrap();
        let mut nav = sector.navpoints[0].clone();
        for unsupported in [41, 42, 255] {
            nav.object_type = unsupported;
            assert_eq!(super::map_object(&nav).body[4], 37);
        }
        nav.object_type = 11;
        assert_eq!(super::map_object(&nav).body[4], 11);
    }
    use super::*;

    #[test]
    fn warp_ready_uses_client_value_two_instead_of_boolean_one() {
        let packet = warp_state(FIRST_PLAYER_ID, WarpPhase::Idle, None, 100);
        let mut reader = crate::protocol::Reader::new(&packet.body);
        reader.take(15).unwrap(); // identity, AUX header, eight one-bit flags
        assert_eq!(reader.take(2).unwrap(), &[0, 0]); // movement unlocked
        assert_eq!(reader.u32().unwrap(), 0); // private state: available
        assert_eq!(reader.u32().unwrap(), 0); // global state: not warping
        assert_eq!(reader.u32().unwrap(), 2); // C++ SetWarpAvailable(2)
        for phase in [
            WarpPhase::Charging,
            WarpPhase::Traveling,
            WarpPhase::Recovering,
        ] {
            let packet = warp_state(FIRST_PLAYER_ID, phase, None, 100);
            let mut reader = crate::protocol::Reader::new(&packet.body);
            reader.take(25).unwrap(); // header, flags, locks and private/global state
            assert_eq!(reader.u32().unwrap(), 2); // Q must remain usable in warp
        }
    }

    #[test]
    fn client_damage_matches_native_float_and_identity_field_order() {
        let packet = client_damage(0x10000001, 0x40000002, 42.5, 0.25, 5, 3);
        assert_eq!(packet.opcode, 0x64);
        assert_eq!(packet.body.len(), 24);
        let mut reader = crate::protocol::Reader::new(&packet.body);
        assert_eq!(reader.f32().unwrap(), 42.5);
        assert_eq!(reader.f32().unwrap(), 0.25);
        assert_eq!(reader.u32().unwrap(), 5);
        assert_eq!(reader.u32().unwrap(), 3);
        assert_eq!(reader.u32().unwrap(), 0x40000002);
        assert_eq!(reader.u32().unwrap(), 0x10000001);
        assert!(reader.rest().is_empty());
    }

    #[test]
    fn impulse_packet_uses_milliseconds_and_preserves_movement_identity() {
        let motion = Motion {
            speed: 250.,
            set_speed: 250.,
            movement_id: 42,
            ..Motion::default()
        };
        let packet = player_movement(FIRST_PLAYER_ID, [1., 2., 3.], 100, &motion);
        assert_eq!(packet.body.len(), 70);
        let mut reader = crate::protocol::Reader::new(&packet.body);
        assert_eq!(reader.u16().unwrap(), 0x7f);
        assert_eq!(reader.u32().unwrap(), FIRST_PLAYER_ID);
        assert_eq!(reader.u32().unwrap(), 100);
        for expected in [1., 2., 3., 0., 0., 0., 1.] {
            assert_eq!(reader.f32().unwrap(), expected);
        }
        assert_eq!(reader.u32().unwrap(), 42);
        assert_eq!(reader.f32().unwrap(), 0.25);
        assert_eq!(reader.f32().unwrap(), 0.25);
        assert_eq!(reader.f32().unwrap(), 0.);
    }

    #[test]
    fn graphical_sector_parameters_include_a_real_backdrop() {
        let sector: Sector = serde_json::from_str(include_str!("../data/sector.json")).unwrap();
        let frame = server_parameters(&sector);
        // Packed Win32 ServerParameters: backdrop u16 at 64, sector u32 at 66.
        assert_eq!(frame.body.len(), 70);
        assert_eq!(&frame.body[64..], &[220, 0, 0x34, 4, 0, 0]);
        let mut invalid = sector;
        invalid.backdrop_asset = 0;
        assert!(invalid.validate().is_err());
    }

    #[test]
    fn jenquai_subparts_decode_as_four_named_attachments() {
        let frame = ship_subparts(FIRST_PLAYER_ID, crate::classes::CharacterClass(5));
        let mut reader = crate::protocol::Reader::new(&frame.body);
        assert_eq!(reader.be_u32().unwrap(), FIRST_PLAYER_ID);
        assert_eq!(reader.be_u32().unwrap(), 4);
        for (bone, asset) in [
            ("~01", 1642),
            ("~01/~03_01", 1684),
            ("~01/~03_02", 1684),
            ("~02", 1669),
        ] {
            let length = reader.rest().iter().position(|byte| *byte == 0).unwrap();
            assert_eq!(reader.take(length).unwrap(), bone.as_bytes());
            assert_eq!(reader.take(1).unwrap(), &[0]);
            assert_eq!(reader.be_u32().unwrap(), asset);
        }
        assert!(reader.rest().is_empty());
    }

    #[test]
    fn welcome_message_length_covers_the_complete_text() {
        let frame = message("Welcome to Rust Sandbox.");
        let length = u16::from_le_bytes(frame.body[..2].try_into().unwrap()) as usize;
        assert_eq!(length + 3, frame.body.len());
        assert_eq!(&frame.body[3..], b"Welcome to Rust Sandbox.\0");
    }
    #[test]
    fn aux_lengths_match_header_and_inventory_contains_empty_ids() {
        let frame = ship_aux(FIRST_PLAYER_ID, "Test", 1000., 1000.);
        assert_eq!(
            u16::from_le_bytes([frame.body[4], frame.body[5]]) as usize,
            frame.body.len() - 6
        );
        assert!(frame.body.windows(4).any(|s| s == u32::MAX.to_le_bytes()));
    }
    #[test]
    fn two_bit_flags_and_nested_reset_match_cpp() {
        assert_eq!(aux_record(3, &[], BTreeMap::new()), [0x86, 0x03]);
        assert_eq!(aux_record(1, &[0], BTreeMap::new()), [0x26, 0x05]);
    }

    #[test]
    fn mining_proxy_packets_match_cpp_lengths_and_field_order() {
        let beam = start_prospect(1, 2, 3, 4, 5000);
        assert_eq!(beam.opcode, 0x2012);
        assert_eq!(
            beam.body,
            [1u32, 2, 3, 4, 5000]
                .into_iter()
                .flat_map(u32::to_le_bytes)
                .collect::<Vec<_>>()
        );
        let items = ItemCatalog::load().unwrap();
        let item = items.get(2837).unwrap();
        let tractor = tractor_ore(&OreTractor {
            player: 1,
            article: 2,
            effect: 3,
            tick: 4,
            milliseconds: 5000,
            speed: 200.,
            position: [10., 20., 30.],
            item,
        });
        assert_eq!(tractor.opcode, 0x2013);
        let mut reader = crate::protocol::Reader::new(&tractor.body);
        for value in [1, 2, 3] {
            assert_eq!(reader.u32().unwrap(), value);
        }
        assert_eq!(reader.u16().unwrap(), item.asset);
        assert_eq!(reader.u32().unwrap(), 4);
        let length = reader.u16().unwrap() as usize;
        assert_eq!(reader.take(length).unwrap(), item.name.as_bytes());
        // AddDataLS writes no trailing NUL: duration immediately follows text.
        assert_eq!(reader.u32().unwrap(), 5000);
        for value in [200., 10., 20., 30.] {
            assert_eq!(reader.f32().unwrap(), value);
        }
        assert!(reader.rest().is_empty());
        assert_eq!(resource_interface(true).body, vec![0; 8]);
        assert_eq!(
            resource_interface(false).body,
            [1u32, 0]
                .into_iter()
                .flat_map(u32::to_le_bytes)
                .collect::<Vec<_>>()
        );
    }
}
