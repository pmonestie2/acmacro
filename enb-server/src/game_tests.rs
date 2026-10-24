//! Behavioral integration tests with two authenticated sessions and one shared
//! simulation. These construct packets locally; they never launch a live client.
use super::*;
use crate::account::FIRST_PLAYER_ID;
use crate::mob::distance;
use crate::protocol::SECTOR_PORT;
use crate::sector_combat::MOB_ID;
use std::fs;

pub(super) fn game() -> GameState {
    let root = std::env::temp_dir().join(format!("enb-game-test-{}", rand::random::<u64>()));
    let accounts = AccountDirectory::new(&root);
    accounts
        .create("alice".into(), "example", "Alice".into())
        .unwrap();
    accounts
        .create("bob".into(), "example", "Bob".into())
        .unwrap();
    // These tests exercise expensive high-rank support abilities. Supply real
    // level-nine reactors instead of relying on the former fixed 1000 energy.
    let catalog = crate::items::ItemCatalog::load().unwrap();
    for directory in [&root, &root.join("accounts/bob")] {
        let mut inventory = crate::inventory::Inventory::starter(&catalog).unwrap();
        inventory.equipped[1].as_mut().unwrap().template = 3340;
        fs::write(
            directory.join("inventory.json"),
            serde_json::to_vec(&inventory).unwrap(),
        )
        .unwrap();
    }
    GameState::new(
        serde_json::from_str(include_str!("../data/sector.json")).unwrap(),
        Ipv4Addr::new(127, 0, 0, 2),
        root,
    )
    .unwrap()
}

pub(super) fn login(game: &mut GameState, id: u32, port: u16, username: &str) {
    let peer: SocketAddr = format!("127.0.0.1:{port}").parse().unwrap();
    game.handle_udp(
        3806,
        peer,
        Datagram {
            opcode: 0x1000,
            player: 0,
            sequence: 0,
            body: 246u32.to_le_bytes().to_vec(),
        },
    )
    .unwrap();
    let ticket = game.issue_ticket(username, "example", peer.ip()).unwrap();
    assert_eq!(game.consume_ticket(&ticket, peer.ip()), Some(id));
    let reply = game.select(id, 0, peer.ip(), false).unwrap();
    game.handle_udp(
        3808,
        peer,
        Datagram {
            opcode: 0x2008,
            player: id,
            sequence: 0,
            body: 1076u32.to_le_bytes().to_vec(),
        },
    )
    .unwrap();
    let mut body = vec![0; 137];
    body[..64].copy_from_slice(&reply.body[4..]);
    game.handle_udp(
        SECTOR_PORT,
        peer,
        Datagram {
            opcode: 2,
            player: id,
            sequence: 1,
            body,
        },
    )
    .unwrap();
}

fn activate(game: &mut GameState, owner: u32, target: u32, ability: u32) {
    game.handle_input(
        owner,
        0x17,
        &[owner.to_le_bytes(), target.to_le_bytes()].concat(),
    )
    .unwrap();
    game.handle_input(
        owner,
        0x58,
        &[
            owner.to_le_bytes(),
            0u32.to_le_bytes(),
            ability.to_le_bytes(),
        ]
        .concat(),
    )
    .unwrap();
}

fn advance(game: &mut GameState, seconds: f32) {
    for _ in 0..(seconds / 0.1).ceil() as usize {
        game.advance(0.1).unwrap();
    }
}

fn land_npc_shots(game: &mut GameState) {
    let deadline = game
        .combats
        .values()
        .filter_map(|combat| {
            lock_combat(combat)
                .unwrap()
                .pending_mob_shots()
                .iter()
                .map(|shot| shot.due_ms)
                .max()
        })
        .max()
        .expect("expected a launched NPC shot");
    game.advance_at(0., deadline).unwrap();
}

#[test]
fn native_jumpstart_action_recovers_an_incapacitated_player_without_a_group() {
    let mut game = game();
    let source = FIRST_PLAYER_ID;
    let target = source + 1;
    login(&mut game, source, 22611, "alice");
    login(&mut game, target, 22612, "bob");
    {
        let victim = game.sessions.get_mut(&target).unwrap();
        victim.position = [1000., 0., 0.];
        victim
            .receive_mob_hit(&crate::npc_projectiles::MobHit {
                sector: 1076,
                sequence: 1,
                target,
                target_epoch: victim.threat().session_epoch,
                attacker_index: 0,
                attacker_source: 46,
                attacker_template: 0,
                attacker_generation: 0,
                damage: 1_000_000.,
                damage_type: 3,
            })
            .unwrap();
    }
    game.advance(0.1).unwrap();
    assert!(!game.groups.together(source, target));
    game.handle_input(
        source,
        0x2c,
        &[source, 26, target, 0].map(u32::to_le_bytes).concat(),
    )
    .unwrap();
    advance(&mut game, 8.);
    assert!(game.sessions[&target].peer_snapshot().unwrap().hull > 0.);
    assert!(!game.groups.together(source, target));
}

#[test]
fn npc_ack_failure_and_server_replacement_never_repeat_committed_player_damage() {
    let mut game = game();
    let player = FIRST_PLAYER_ID;
    login(&mut game, player, 22431, "alice");
    game.sessions.get_mut(&player).unwrap().position = [0.; 3];
    let combat = Arc::clone(&game.sessions[&player].combat);
    {
        let mut world = lock_combat(&combat).unwrap();
        world.actors[0].position = [100., 0., 0.];
        world.actors[0].definition.spawn = [100., 0., 0.];
    }
    game.advance(0.1).unwrap();
    let shot = lock_combat(&combat).unwrap().pending_mob_shots()[0].clone();
    let before = game.sessions[&player].peer_snapshot().unwrap().shield;
    let blocked = game.root.join("combat/1076.json.next");
    fs::create_dir(&blocked).unwrap();
    game.advance_at(0., shot.due_ms).unwrap();
    assert_eq!(
        game.sessions[&player].peer_snapshot().unwrap().shield,
        before - shot.hit.damage
    );
    assert_eq!(lock_combat(&combat).unwrap().pending_mob_shots().len(), 1);
    let root = game.root.clone();
    let sector = game.initial_sector.clone();
    let bind = game.bind;
    drop(game);
    fs::remove_dir(blocked).unwrap();
    let mut restored = GameState::new(sector, bind, root).unwrap();
    assert_eq!(
        restored.sessions[&player].peer_snapshot().unwrap().shield,
        before - shot.hit.damage
    );
    restored.advance_at(0., shot.due_ms).unwrap();
    assert_eq!(
        restored.sessions[&player].peer_snapshot().unwrap().shield,
        before - shot.hit.damage
    );
    assert!(lock_combat(&restored.sessions[&player].combat)
        .unwrap()
        .pending_mob_shots()
        .is_empty());
}

#[test]
fn npc_projectile_cannot_damage_its_target_after_a_sector_handoff() {
    let mut game = game();
    let player = FIRST_PLAYER_ID;
    login(&mut game, player, 22441, "alice");
    game.sessions.get_mut(&player).unwrap().position = [0.; 3];
    let source = Arc::clone(&game.sessions[&player].combat);
    {
        let mut world = lock_combat(&source).unwrap();
        world.actors[0].position = [100., 0., 0.];
        world.actors[0].definition.spawn = [100., 0., 0.];
    }
    game.advance(0.1).unwrap();
    let shot = lock_combat(&source).unwrap().pending_mob_shots()[0].clone();
    let before = game.sessions[&player].peer_snapshot().unwrap().shield;
    game.handle_event(
        player,
        ClientEvent::Chat {
            channel: 0,
            text: "/d_jump Saturn".into(),
        },
    )
    .unwrap();
    game.advance_at(0., shot.due_ms).unwrap();
    assert_eq!(game.sessions[&player].sector.id, 1071);
    // Handoff sessions are intentionally absent from peer snapshots.
    let checkpoint: serde_json::Value = serde_json::from_slice(
        &fs::read(game.sessions[&player].state_dir.join("session.local.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(checkpoint["shield"].as_f64().unwrap() as f32, before);
    assert!(lock_combat(&source).unwrap().pending_mob_shots().is_empty());
}

#[test]
fn storage_failure_for_first_character_does_not_skip_other_players_or_groups() {
    let mut game = game();
    let alice = FIRST_PLAYER_ID;
    let bob = alice + 1;
    login(&mut game, alice, 22001, "alice");
    login(&mut game, bob, 22002, "bob");
    game.save().unwrap();
    let alice_dir = game.sessions[&alice].state_dir.clone();
    let bob_dir = game.sessions[&bob].state_dir.clone();
    fs::remove_file(alice_dir.join("groups.local.json")).unwrap();
    fs::create_dir(alice_dir.join("player.json.tmp")).unwrap();
    game.sessions.get_mut(&bob).unwrap().position = [100., 200., 0.];
    assert!(game.save().is_err());
    let player: serde_json::Value =
        serde_json::from_slice(&fs::read(bob_dir.join("player.json")).unwrap()).unwrap();
    let checkpoint: serde_json::Value =
        serde_json::from_slice(&fs::read(bob_dir.join("session.local.json")).unwrap()).unwrap();
    assert_eq!(player["position"], serde_json::json!([100., 200., 0.]));
    assert_eq!(checkpoint["position"], serde_json::json!([100., 200., 0.]));
    assert!(alice_dir.join("groups.local.json").is_file());
    fs::create_dir(alice_dir.join("session.local.json.tmp")).unwrap();
    game.sessions.get_mut(&bob).unwrap().position = [200., 300., 0.];
    assert!(game.checkpoint_session(true).is_err());
    let checkpoint: serde_json::Value =
        serde_json::from_slice(&fs::read(bob_dir.join("session.local.json")).unwrap()).unwrap();
    assert_eq!(checkpoint["position"], serde_json::json!([200., 300., 0.]));
}

#[test]
fn authentication_isolates_ids_endpoints_and_private_account_state() {
    let mut game = game();
    let a = FIRST_PLAYER_ID;
    let b = a + 1;
    login(&mut game, a, 22001, "alice");
    login(&mut game, b, 22002, "bob");
    game.advance(0.05).unwrap();
    assert!(Arc::ptr_eq(
        &game.sessions[&a].combat,
        &game.sessions[&b].combat
    ));
    assert_ne!(game.sessions[&a].state_dir, game.sessions[&b].state_dir);
    let peer: SocketAddr = "127.0.0.1:22001".parse().unwrap();
    assert!(game
        .handle_udp(
            SECTOR_PORT,
            peer,
            Datagram {
                opcode: 0x14,
                player: b,
                sequence: 2,
                body: [b.to_le_bytes().as_slice(), &[2]].concat()
            }
        )
        .is_err());
    let chars = game.characters(b).unwrap();
    assert_eq!(
        u32::from_be_bytes(chars.body[20..24].try_into().unwrap()),
        b & 0xffffff
    );
}

#[test]
fn combat_is_shared_and_group_jumpstart_and_cloak_apply_to_other_player() {
    let mut game = game();
    let a = FIRST_PLAYER_ID;
    let b = a + 1;
    login(&mut game, a, 22011, "alice");
    login(&mut game, b, 22012, "bob");
    game.sessions.get_mut(&a).unwrap().position = [0.; 3];
    game.sessions.get_mut(&b).unwrap().position = [50.; 3];
    lock_combat(&game.sessions[&a].combat).unwrap().actors[0].position = [100.; 3];
    lock_combat(&game.sessions[&a].combat).unwrap().actors[0]
        .definition
        .spawn = [100.; 3];
    let shield_before = game.sessions[&b].peer_snapshot().unwrap().shield;
    game.advance(0.1).unwrap();
    assert_eq!(
        game.sessions[&b].peer_snapshot().unwrap().shield,
        shield_before
    );
    land_npc_shots(&mut game);
    assert_eq!(
        game.sessions[&b].peer_snapshot().unwrap().shield,
        shield_before - 5.
    );
    activate(&mut game, a, MOB_ID, 106);
    advance(&mut game, 1.2);
    assert_eq!(
        lock_combat(&game.sessions[&b].combat).unwrap().actors[0].shield,
        75.
    );
    game.handle_event(
        a,
        ClientEvent::Group {
            action: 10,
            target: b,
        },
    )
    .unwrap();
    game.handle_event(
        b,
        ClientEvent::Group {
            action: 11,
            target: 0,
        },
    )
    .unwrap();
    let victim = game.sessions[&b].peer_snapshot().unwrap();
    let lethal_damage = (victim.hull + victim.shield) * 4.;
    let hull_capacity = crate::ship_progression::hull_capacity(
        victim.profile.class(),
        crate::ship_progression::SANDBOX_HULL_UPGRADE,
    )
    .unwrap();
    game.sessions
        .get_mut(&b)
        .unwrap()
        .receive_hit(0, lethal_damage)
        .unwrap();
    assert_eq!(game.sessions[&b].peer_snapshot().unwrap().hull, 0.);
    game.advance(0.1).unwrap();
    activate(&mut game, a, b, 65);
    advance(&mut game, 7.2);
    assert_eq!(
        game.sessions[&b].peer_snapshot().unwrap().hull,
        hull_capacity * 0.7
    );
    activate(&mut game, a, a, 51);
    advance(&mut game, 3.2);
    assert!(game.sessions[&a].peer_snapshot().unwrap().cloaked);
    assert!(game.sessions[&b].peer_snapshot().unwrap().cloaked);
    game.handle_event(
        b,
        ClientEvent::Group {
            action: 14,
            target: 0,
        },
    )
    .unwrap();
    game.advance(0.1).unwrap();
    assert!(!game.sessions[&b].peer_snapshot().unwrap().cloaked);
}

#[test]
fn groups_and_both_space_sessions_survive_restart_then_share_wormhole() {
    let mut game = game();
    let a = FIRST_PLAYER_ID;
    let b = a + 1;
    login(&mut game, a, 22021, "alice");
    login(&mut game, b, 22022, "bob");
    game.handle_event(
        a,
        ClientEvent::Group {
            action: 10,
            target: b,
        },
    )
    .unwrap();
    game.handle_event(
        b,
        ClientEvent::Group {
            action: 11,
            target: 0,
        },
    )
    .unwrap();
    game.advance(0.1).unwrap();
    game.save().unwrap();
    let mut replacement =
        GameState::new(game.initial_sector.clone(), game.bind, game.root.clone()).unwrap();
    assert!(replacement.groups.together(a, b));
    assert!(replacement.sessions[&a].logged_in && replacement.sessions[&b].logged_in);
    assert!(Arc::ptr_eq(
        &replacement.sessions[&a].combat,
        &replacement.sessions[&b].combat
    ));
    activate(&mut replacement, a, a, 67);
    advance(&mut replacement, 3.2);
    assert_eq!(replacement.sessions[&a].sector.id, 1910);
    assert_eq!(replacement.sessions[&b].sector.id, 1910);
    assert!(replacement.groups.together(a, b));
    let handoff_tick = replacement.sessions[&a].tick_ms();
    replacement.save().unwrap();
    let resumed = GameState::new(
        replacement.initial_sector.clone(),
        replacement.bind,
        replacement.root.clone(),
    )
    .unwrap();
    assert!(resumed.sessions[&a].connected() && !resumed.sessions[&a].logged_in);
    assert!(resumed.sessions[&a].tick_ms() >= handoff_tick);
    assert!(resumed.groups.together(a, b));
}

#[test]
fn formation_follows_rotated_leader_restores_and_detaches_on_manual_thrust() {
    let mut game = game();
    let a = FIRST_PLAYER_ID;
    let b = a + 1;
    login(&mut game, a, 22031, "alice");
    login(&mut game, b, 22032, "bob");
    game.groups.invite(a, b).unwrap();
    game.groups.accept(b).unwrap();
    let command = |action| ClientEvent::Formation { action, target: 0 };
    assert!(game.handle_event(b, command(5)).is_err());
    assert!(game.handle_event(b, command(7)).is_err());
    game.handle_event(a, command(5)).unwrap();
    game.sessions.get_mut(&b).unwrap().position = [10_000., 0., 0.];
    assert!(game.handle_event(b, command(7)).is_err());
    game.sessions.get_mut(&b).unwrap().position = [100., 0., 0.];
    game.handle_event(b, command(7)).unwrap();
    game.handle_input(a, 0x14, &[a.to_le_bytes().as_slice(), &[2]].concat())
        .unwrap();
    advance(&mut game, 1.);
    let leader = game.sessions[&a].peer_snapshot().unwrap();
    let follower = game.sessions[&b].peer_snapshot().unwrap();
    assert!(
        distance(
            follower.position,
            [
                leader.position[0],
                leader.position[1] - 200.,
                leader.position[2]
            ]
        ) < 0.01
    );
    assert_eq!(leader.motion.speed, follower.motion.speed);
    game.save().unwrap();
    let mut replacement =
        GameState::new(game.initial_sector.clone(), game.bind, game.root.clone()).unwrap();
    replacement.advance(0.1).unwrap();
    assert!(replacement.groups.group(b).unwrap().formed.contains(&b));
    replacement
        .handle_input(b, 0x14, &[b.to_le_bytes().as_slice(), &[4]].concat())
        .unwrap();
    replacement.advance(0.1).unwrap();
    assert!(!replacement.groups.group(b).unwrap().formed.contains(&b));
    replacement.handle_event(b, command(7)).unwrap();
    replacement.advance(0.1).unwrap();
    replacement.handle_event(a, command(9)).unwrap();
    replacement.advance(0.1).unwrap();
    assert_eq!(replacement.groups.group(a).unwrap().formation, 0);
}

#[test]
fn formed_group_keeps_relative_position_through_leader_warp() {
    let mut game = game();
    let leader = FIRST_PLAYER_ID;
    let follower = leader + 1;
    login(&mut game, leader, 22501, "alice");
    login(&mut game, follower, 22502, "bob");
    game.groups.invite(leader, follower).unwrap();
    game.groups.accept(follower).unwrap();
    game.handle_event(
        leader,
        ClientEvent::Formation {
            action: 5,
            target: 0,
        },
    )
    .unwrap();
    game.sessions.get_mut(&follower).unwrap().position = [100., 0., 0.];
    game.handle_event(
        follower,
        ClientEvent::Formation {
            action: 7,
            target: 0,
        },
    )
    .unwrap();
    advance(&mut game, 0.1);
    let route = [
        (leader & 0xffffff).to_le_bytes().as_slice(),
        &1u16.to_le_bytes(),
        &100000u32.to_le_bytes(),
    ]
    .concat();
    game.handle_input(leader, 0x9b, &route).unwrap();
    advance(&mut game, 5.);
    let lead = game.sessions[&leader].peer_snapshot().unwrap();
    let wing = game.sessions[&follower].peer_snapshot().unwrap();
    assert!(lead.position[0] > 6000.);
    assert!(game
        .groups
        .group(follower)
        .unwrap()
        .formed
        .contains(&follower));
    let offset = crate::groups::formation_offset(5, 1);
    let rotated = lead.motion.rotate(offset);
    assert!(
        distance(
            wing.position,
            std::array::from_fn(|axis| lead.position[axis] + rotated[axis])
        ) < 1.
    );
    game.handle_input(leader, 0x9b, &route).unwrap();
    advance(&mut game, 0.1);
    assert!(game
        .groups
        .group(follower)
        .unwrap()
        .formed
        .contains(&follower));
}

#[test]
fn failed_weapon_storage_for_one_player_does_not_stop_another_players_tick() {
    let mut game = game();
    let a = FIRST_PLAYER_ID;
    let b = a + 1;
    login(&mut game, a, 22401, "alice");
    login(&mut game, b, 22402, "bob");
    game.sessions.get_mut(&a).unwrap().position = [0.; 3];
    game.sessions.get_mut(&b).unwrap().position = [0.; 3];
    lock_combat(&game.sessions[&a].combat).unwrap().actors[0].position = [100., 0., 0.];
    game.advance(0.).unwrap();
    game.handle_input(a, 0x17, &[a.to_le_bytes(), MOB_ID.to_le_bytes()].concat())
        .unwrap();
    game.handle_input(a, 0x5d, &[a.to_le_bytes().as_slice(), &[2, 3]].concat())
        .unwrap();
    game.handle_input(b, 0x14, &[b.to_le_bytes().as_slice(), &[2]].concat())
        .unwrap();
    let blocked = game.root.join("combat/1076.json.next");
    fs::create_dir(&blocked).unwrap();
    game.advance(0.1).unwrap();
    assert!(game.sessions[&b].position[0] > 0.);
    let alice: serde_json::Value =
        serde_json::from_slice(&fs::read(game.root.join("inventory.json")).unwrap()).unwrap();
    let bob: serde_json::Value =
        serde_json::from_slice(&fs::read(game.root.join("accounts/bob/inventory.json")).unwrap())
            .unwrap();
    assert!(!alice["pending_launch"].is_null());
    assert!(bob["pending_launch"].is_null());
    fs::remove_dir(blocked).unwrap();
    game.sessions
        .get_mut(&a)
        .unwrap()
        .recover_weapon_launch()
        .unwrap();
}

#[test]
fn multiplayer_hate_targets_the_attacker_and_dev_jump_clears_the_departed_identity() {
    let mut game = game();
    let a = FIRST_PLAYER_ID;
    let b = a + 1;
    login(&mut game, a, 22411, "alice");
    login(&mut game, b, 22412, "bob");
    game.sessions.get_mut(&a).unwrap().position = [0.; 3];
    game.sessions.get_mut(&b).unwrap().position = [400., 0., 0.];
    let source = Arc::clone(&game.sessions[&a].combat);
    {
        let mut world = lock_combat(&source).unwrap();
        let mob = &mut world.actors[0];
        mob.position = [500., 0., 0.];
        mob.definition.spawn = mob.position;
        mob.definition.aggressive = false;
        mob.damage_by(1., a);
    }
    let shield_a = game.sessions[&a].peer_snapshot().unwrap().shield;
    let shield_b = game.sessions[&b].peer_snapshot().unwrap().shield;
    game.advance(0.1).unwrap();
    assert_eq!(game.sessions[&a].peer_snapshot().unwrap().shield, shield_a);
    land_npc_shots(&mut game);
    assert_eq!(
        game.sessions[&a].peer_snapshot().unwrap().shield,
        shield_a - 5.
    );
    assert_eq!(game.sessions[&b].peer_snapshot().unwrap().shield, shield_b);
    game.handle_event(
        a,
        ClientEvent::Chat {
            channel: 0,
            text: "/d_jump Saturn".into(),
        },
    )
    .unwrap();
    game.advance(0.).unwrap();
    let query = [crate::sector_combat::PlayerThreat {
        id: a,
        session_epoch: 1,
        position: [0.; 3],
        visible: true,
        signature: 0.,
        stealth: None,
        factions: crate::factions::FactionReactions::neutral(),
    }];
    assert_eq!(
        lock_combat(&source).unwrap().actors[0].select_target(&query),
        None
    );
}
