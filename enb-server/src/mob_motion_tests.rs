use super::*;
use crate::packets;

fn mob() -> Mob {
    let mut definition: MobDefinition = serde_json::from_str(r#"{"source_id":1,"template_id":46,"name":"Motion test","level":1,"asset":48,"spawn":[0,0,0],"respawn_seconds":20,"aggressive":true}"#).unwrap();
    definition.combat_override = Some(MobStats {
        hull: 100.,
        shield: 150.,
        dps: 2.5,
        reload: 2.,
        range: 1000.,
        effect: 35,
        damage_type: 3,
        variance: false,
    });
    Mob::new(definition)
}

fn number(body: &[u8], offset: usize) -> f32 {
    f32::from_le_bytes(body[offset..offset + 4].try_into().unwrap())
}

fn verify_packet(mob: &Mob, expected_speed: f32) {
    let frame = packets::mob_movement(0x10000001, mob, 12345);
    assert_eq!(frame.opcode, 0x3e);
    assert_eq!(frame.body.len(), 70);
    assert_eq!(
        u16::from_le_bytes(frame.body[..2].try_into().unwrap()),
        0x7f
    );
    assert_eq!(
        u32::from_le_bytes(frame.body[38..42].try_into().unwrap()),
        mob.motion.movement_id
    );
    for axis in 0..4 {
        assert_eq!(
            number(&frame.body, 22 + axis * 4),
            mob.motion.orientation[axis]
        );
    }
    assert!((number(&frame.body, 42) - expected_speed / 1000.).abs() < 0.00001);
    assert_eq!(number(&frame.body, 42), number(&frame.body, 46));
    assert_eq!(number(&frame.body, 50), 0.);
    assert_eq!(number(&frame.body, 54), 0.);
    assert_eq!(number(&frame.body, 62), 0.);
}

#[test]
fn chasing_faces_actual_three_dimensional_motion_and_native_velocity() {
    let mut mob = mob();
    let initial_id = mob.motion.movement_id;
    mob.tick(1., [0., 2000., 500.]);
    assert_eq!(mob.state, MobState::Chasing);
    assert!((mob.speed - 150.).abs() < 0.001);
    let heading = mob.motion.heading();
    assert!(heading[0].abs() < 0.0001 && heading[1] > 0.9 && heading[2] > 0.2);
    assert_ne!(mob.motion.orientation, [0., 0., 0., 1.]);
    assert_eq!(mob.motion.movement_id, initial_id + 5);
    verify_packet(&mob, 150.);
    let frames = packets::mob_spawn(0x10000001, &mob, 12345, 0);
    assert_eq!(
        frames[2].body,
        packets::mob_movement(0x10000001, &mob, 12345).body
    );
}

#[test]
fn return_home_faces_spawn_and_resting_stops_native_extrapolation() {
    let mut mob = mob();
    mob.position = [1000., 1000., 500.];
    mob.damage(1.);
    mob.tick(1., [20_000., 0., 0.]);
    assert_eq!(mob.state, MobState::Idle);
    let heading = mob.motion.heading();
    assert!(heading[0] < -0.6 && heading[1] < -0.6 && heading[2] < -0.3);
    verify_packet(&mob, 100.);
    mob.tick(20., [20_000., 0., 0.]);
    assert_eq!(mob.position, mob.definition.spawn);
    mob.tick(1., [20_000., 0., 0.]);
    verify_packet(&mob, 0.);
    let resting_id = mob.motion.movement_id;
    let resting_heading = mob.motion.orientation;
    mob.tick(1., [20_000., 0., 0.]);
    assert_eq!(mob.motion.movement_id, resting_id);
    assert_eq!(mob.motion.orientation, resting_heading);
}

#[test]
fn contemplation_and_death_stop_immediately_and_saved_motion_restores() {
    let mut mob = mob();
    mob.tick(1., [0., 2000., 500.]);
    let snapshot: Mob = serde_json::from_slice(&serde_json::to_vec(&mob).unwrap()).unwrap();
    let mut restored = self::mob();
    restored.restore_from(snapshot).unwrap();
    assert_eq!(restored.motion.orientation, mob.motion.orientation);
    assert_eq!(restored.motion.movement_id, mob.motion.movement_id);
    verify_packet(&restored, 150.);
    restored.pacify(30.);
    verify_packet(&restored, 0.);
    restored.damage(1.);
    restored.tick(1., [0., 2000., 500.]);
    restored.damage(1000.);
    verify_packet(&restored, 0.);
    restored.tick_visible(20., [0.; 3], false);
    verify_packet(&restored, 0.);
}

#[test]
fn legacy_snapshots_default_motion_and_corrupt_heading_is_rejected_before_restore() {
    let original = mob();
    let mut value = serde_json::to_value(&original).unwrap();
    value.as_object_mut().unwrap().remove("motion");
    let mut restored = mob();
    restored
        .restore_from(serde_json::from_value(value).unwrap())
        .unwrap();
    verify_packet(&restored, 0.);
    let mut corrupt = original;
    corrupt.motion.orientation = [0.; 4];
    assert!(restored.restore_from(corrupt).is_err());
    assert_eq!(restored.motion.orientation, [0., 0., 0., 1.]);
}

#[test]
fn moving_legacy_checkpoints_restore_position_and_derive_heading_on_the_next_tick() {
    let mut original = mob();
    original.tick(1., [0., 2000., 500.]);
    assert!(original.speed > 0.);
    let position = original.position;
    let mut legacy = serde_json::to_value(&original).unwrap();
    legacy.as_object_mut().unwrap().remove("motion");
    let mut restored = mob();
    restored
        .restore_from(serde_json::from_value(legacy).unwrap())
        .unwrap();
    assert_eq!(restored.position, position);
    assert_eq!(restored.speed, original.speed);
    // Legacy files contain no historical heading. Keep a stationary render
    // default until the next real displacement establishes a safe direction.
    verify_packet(&restored, 0.);
    restored.tick(1., [0., 2000., 500.]);
    verify_packet(&restored, 150.);
    assert!(restored.motion.heading()[1] > 0.9);
}

#[test]
fn expired_legacy_respawn_countdowns_restore_and_new_respawns_never_save_negative_time() {
    for state in [MobState::Idle, MobState::Dead] {
        let mut saved = mob();
        saved.state = state;
        saved.respawn_remaining = -0.02729712; // observed legacy shared-world value
        if state == MobState::Dead {
            saved.health = 0.;
            saved.shield = 0.;
        }
        let mut restored = mob();
        restored.restore_from(saved).unwrap();
        assert_eq!(restored.respawn_remaining, 0.);
        restored.tick_visible(0., [0.; 3], false);
        assert_eq!(restored.state, MobState::Idle);
    }
    let mut original = mob();
    original.damage(1000.);
    original.tick_visible(20.027, [0.; 3], false);
    assert_eq!(original.state, MobState::Idle);
    assert_eq!(original.respawn_remaining, 0.);
    let saved: Mob = serde_json::from_slice(&serde_json::to_vec(&original).unwrap()).unwrap();
    mob().restore_from(saved).unwrap();
    original.respawn_remaining = f32::NAN;
    assert!(mob().restore_from(original).is_err());
}
