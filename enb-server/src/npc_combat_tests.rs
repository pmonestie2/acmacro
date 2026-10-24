//! Deterministic world/IO tests; no live clients or wall-clock sleeps.
use super::*;
use crate::mob::MobState;
use crate::mob_stats::{MobStats, MobWeapon};
use std::fs;

fn initial(launcher: bool) -> SectorCombat {
    let mut definition: MobDefinition = serde_json::from_str(r#"{"source_id":123,"template_id":46,"name":"Test","level":1,"asset":48,"spawn":[0,0,0],"respawn_seconds":20,"aggressive":true}"#).unwrap();
    definition.combat_override = Some(MobStats {
        hull: 100.,
        shield: 150.,
        dps: 2.5,
        reload: 2.,
        range: 1500.,
        effect: 948,
        damage_type: 3,
        variance: false,
    });
    if launcher {
        definition.weapon = Some(MobWeapon {
            item: 3022,
            item_type: 16,
            damage: 11.,
            damage_type: 3,
            reload: 2.,
            effect: 948,
        });
    }
    SectorCombat::new(&[definition])
}

fn persistent(launcher: bool) -> (SectorCombat, PathBuf) {
    let root = std::env::temp_dir().join(format!("enb-npc-combat-{}", rand::random::<u64>()));
    fs::create_dir_all(&root).unwrap();
    let path = root.join("1076.json");
    let world = SectorCombat::persistent(path.clone(), 1076, &initial(launcher)).unwrap();
    (world, path)
}

fn player() -> PlayerThreat {
    PlayerThreat {
        id: 0x40000001,
        session_epoch: 1,
        position: [1000., 0., 0.],
        visible: true,
        signature: 0.,
        stealth: None,
        factions: crate::factions::FactionReactions::neutral(),
    }
}

#[test]
fn npc_launch_persists_before_effect_and_waits_for_due_time_then_receipt_ack() {
    for (launcher, travel) in [(false, 1000), (true, 3000)] {
        let (mut world, path) = persistent(launcher);
        assert!(world
            .advance_at(0.1, &[player()], 10_000)
            .unwrap()
            .is_empty());
        let pending = world.pending_mob_shots()[0].clone();
        assert_eq!(pending.duration_ms, travel);
        assert_eq!(pending.hit.damage, 5.);
        assert_eq!(pending.hit.damage_type, 3);
        let visuals = world.take_mob_launch_visuals();
        assert_eq!(visuals.len(), 1);
        assert_eq!(visuals[0].duration_ms, travel);
        assert_eq!(visuals[0].target, player().id);
        assert_eq!(visuals[0].effect, 948);
        let mut resumed = SectorCombat::persistent(path, 1076, &initial(launcher)).unwrap();
        assert_eq!(resumed.pending_mob_shots(), std::slice::from_ref(&pending));
        assert_eq!(resumed.pending_mob_shots()[0].hit.target_epoch, 1);
        assert!(resumed
            .advance_at(0., &[player()], pending.due_ms - 1)
            .unwrap()
            .is_empty());
        // A shot already fired is independent of later cloak and source death.
        let mut cloaked = player();
        cloaked.visible = false;
        resumed.actors[0].damage(1000.);
        resumed.actors[0].combat.damage_type = 4;
        let hits = resumed.advance_at(0., &[cloaked], pending.due_ms).unwrap();
        assert_eq!(hits.as_slice(), std::slice::from_ref(&pending.hit));
        assert_eq!(
            resumed.advance_at(0., &[], pending.due_ms + 1).unwrap(),
            hits
        );
        assert_eq!(resumed.pending_mob_shots().len(), 1);
        resumed
            .acknowledge_mob_hit(pending.hit.sequence, 10.)
            .unwrap();
        assert!(resumed.pending_mob_shots().is_empty());
        assert_eq!(resumed.actors[0].state, MobState::Dead);
        resumed
            .acknowledge_mob_hit(pending.hit.sequence, 10.)
            .unwrap();
        assert!(resumed
            .advance_at(0., &[], pending.due_ms + 2)
            .unwrap()
            .is_empty());
    }
}

#[test]
fn npc_launch_write_failure_rolls_back_cooldown_hate_rng_sequence_and_visual() {
    let (mut world, path) = persistent(true);
    let original = serde_json::to_vec(&world.actors).unwrap();
    fs::create_dir(path.with_extension("json.next")).unwrap();
    assert!(world.advance_at(0.1, &[player()], 10_000).is_err());
    assert_eq!(serde_json::to_vec(&world.actors).unwrap(), original);
    assert!(world.pending_mob_shots().is_empty());
    assert!(world.take_mob_launch_visuals().is_empty());
    assert_eq!(world.npc_sequence, 0);
    fs::remove_dir(path.with_extension("json.next")).unwrap();
    world.advance_at(0.1, &[player()], 10_000).unwrap();
    assert_eq!(world.pending_mob_shots()[0].hit.sequence, 1);
}

#[test]
fn reflection_and_ack_are_one_write_and_retry_never_reflects_twice() {
    let (mut world, path) = persistent(true);
    world.advance_at(0.1, &[player()], 10_000).unwrap();
    let shot = world.pending_mob_shots()[0].clone();
    let before = serde_json::to_vec(&world.actors).unwrap();
    fs::create_dir(path.with_extension("json.next")).unwrap();
    assert!(world.acknowledge_mob_hit(shot.hit.sequence, 10.).is_err());
    assert_eq!(serde_json::to_vec(&world.actors).unwrap(), before);
    assert_eq!(world.pending_mob_shots(), std::slice::from_ref(&shot));
    fs::remove_dir(path.with_extension("json.next")).unwrap();
    assert!(world
        .acknowledge_mob_hit(shot.hit.sequence, f32::NAN)
        .is_err());
    assert_eq!(world.pending_mob_shots().len(), 1);
    world.acknowledge_mob_hit(shot.hit.sequence, 10.).unwrap();
    assert_eq!(world.actors[0].shield, 140.);
    let mut resumed = SectorCombat::persistent(path, 1076, &initial(true)).unwrap();
    assert!(resumed.pending_mob_shots().is_empty());
    resumed.acknowledge_mob_hit(shot.hit.sequence, 10.).unwrap();
    assert_eq!(resumed.actors[0].shield, 140.);
}

#[test]
fn reflection_cannot_damage_a_respawned_or_replaced_source_life() {
    let (mut world, _) = persistent(true);
    world.advance_at(0.1, &[player()], 10_000).unwrap();
    let sequence = world.pending_mob_shots()[0].hit.sequence;
    world.actors[0].damage(1000.);
    world.actors[0].tick_visible(20., [0.; 3], false);
    assert_eq!(world.actors[0].shield, 150.);
    world.acknowledge_mob_hit(sequence, 50.).unwrap();
    assert_eq!(world.actors[0].shield, 150.);
}

#[test]
fn old_snapshots_default_to_empty_npc_queue_and_bad_sequences_are_rejected() {
    let (mut world, path) = persistent(true);
    world.persist().unwrap();
    let mut value: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    value.as_object_mut().unwrap().remove("npc_shots");
    value.as_object_mut().unwrap().remove("npc_sequence");
    fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(SectorCombat::persistent(path.clone(), 1076, &initial(true))
        .unwrap()
        .pending_mob_shots()
        .is_empty());
    world.advance_at(0.1, &[player()], 10_000).unwrap();
    value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    value["npc_sequence"] = serde_json::json!(0);
    fs::write(&path, serde_json::to_vec(&value).unwrap()).unwrap();
    assert!(SectorCombat::persistent(path, 1076, &initial(true)).is_err());
}

#[test]
fn a_full_pending_queue_still_returns_due_impacts_so_acknowledgments_can_make_room() {
    let mut world = initial(true);
    world.bind_sector(1076).unwrap();
    world.advance_at(0.1, &[player()], 10_000).unwrap();
    let mut shot = world.pending_mob_shots()[0].clone();
    world.npc_shots.clear();
    for sequence in 1..=16384 {
        shot.hit.sequence = sequence;
        world.npc_shots.push(shot.clone());
    }
    world.npc_sequence = 16384;
    let hits = world.advance_at(2., &[player()], shot.due_ms).unwrap();
    assert_eq!(hits.len(), 16384);
    world.acknowledge_mob_hit(1, 0.).unwrap();
    assert_eq!(world.pending_mob_shots().len(), 16383);
    world.advance_at(2., &[player()], shot.due_ms).unwrap();
    assert_eq!(world.pending_mob_shots().len(), 16384);
    assert_eq!(
        world.pending_mob_shots().last().unwrap().hit.sequence,
        16385
    );
}
