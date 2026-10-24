use super::*;

fn mob(radius: f32) -> Mob {
    let mut definition: MobDefinition = serde_json::from_str(r#"{"source_id":123,"template_id":46,"name":"Roam test","level":1,"asset":48,"spawn":[0,0,0],"respawn_seconds":20,"aggressive":false,"faction":0,"aggression":0}"#).unwrap();
    definition.spawn_region = Some(SpawnRegion {
        center: [0.; 3],
        radius,
    });
    definition.combat_override = Some(MobStats {
        hull: 100.,
        shield: 150.,
        dps: 2.5,
        reload: 2.,
        range: 1500.,
        effect: 35,
        damage_type: 3,
        variance: false,
    });
    Mob::new(definition)
}

fn player(x: f32) -> PlayerThreat {
    PlayerThreat {
        id: 0x40000001,
        session_epoch: 1,
        position: [x, 0., 0.],
        visible: true,
        signature: 0.,
        stealth: None,
        factions: crate::factions::FactionReactions::neutral(),
    }
}

#[test]
fn native_drift_uses_sourced_speed_turn_and_saved_clock_rng_without_offline_catchup() {
    let mut original = mob(4000.);
    original.tick_idle(0.5, &[]);
    assert!((130. ..=229.).contains(&original.roaming.default_speed));
    assert!(original.roaming.default_turn != 0. && original.roaming.default_turn.abs() <= 0.05);
    assert!(distance(original.position, [0.; 3]) > 0.);
    let mut restored = mob(4000.);
    let snapshot = serde_json::from_slice(&serde_json::to_vec(&original).unwrap()).unwrap();
    restored.restore_from(snapshot).unwrap();
    for _ in 0..100 {
        original.tick_idle(0.5, &[]);
        restored.tick_idle(0.5, &[]);
        assert_eq!(original.position, restored.position);
        assert_eq!(original.motion.orientation, restored.motion.orientation);
        assert_eq!(original.roaming.random_state, restored.roaming.random_state);
        assert_eq!(
            original.roaming.decision_seconds,
            restored.roaming.decision_seconds
        );
        assert!(distance(original.position, [0.; 3]) <= 4000. + 229. * 10. + 1.);
    }
    let before = original.position;
    let clock = original.roaming.decision_seconds;
    original.tick_idle(60., &[]);
    assert_eq!(original.position, before);
    assert_eq!(original.roaming.decision_seconds, clock);
}

#[test]
fn drift_boundary_faces_the_real_group_center_and_curiosity_uses_actual_contacts() {
    let mut mob = mob(4000.);
    mob.tick_idle(0., &[]);
    mob.position = [5000., 0., 0.];
    mob.roaming.decision_seconds = 0.1;
    mob.tick_idle(0.1, &[]);
    assert!(mob.motion.heading()[0] < -0.99);
    mob.position = [0.; 3];
    let mut target = player(2000.);
    mob.select_target(std::slice::from_ref(&target));
    // Inject the deterministic stream's state, not a fabricated route; choose
    // a seed whose two native rand()%50 draws enter the >75 curiosity branch.
    let seed = (0..1000)
        .find(|seed| {
            let mut random = WorldRandom(*seed);
            random.below(50) + random.below(50) > 75
        })
        .unwrap();
    mob.roaming.random_state = seed;
    mob.choose_idle_change(std::slice::from_ref(&target));
    assert!(matches!(
        mob.roaming.behavior,
        IdleBehavior::Curious {
            player: 0x40000001,
            epoch: 1
        }
    ));
    mob.tick_idle(0.5, std::slice::from_ref(&target));
    assert!(mob.position[0] > 0. && mob.motion.heading()[0] > 0.99);
    target.position[0] = mob.position[0] + 10.;
    mob.tick_idle(0.1, std::slice::from_ref(&target));
    assert!(matches!(mob.roaming.behavior, IdleBehavior::Drift));
    assert_eq!(mob.motion.speed, 0.);
    mob.roaming.behavior = IdleBehavior::Curious {
        player: target.id,
        epoch: target.session_epoch,
    };
    target.session_epoch += 1;
    mob.tick_idle(0.1, std::slice::from_ref(&target));
    assert!(matches!(mob.roaming.behavior, IdleBehavior::Drift));
}

#[test]
fn native_pursuit_moves_while_firing_holds_standoff_and_backs_up_facing_the_enemy() {
    let mut mob = mob(4000.);
    let target = player(1000.);
    assert_eq!(mob.tick_target(0.1, &target), 5.);
    assert_eq!(mob.state, MobState::Attacking);
    assert!(mob.position[0] > 0. && mob.motion.speed >= 130.);
    let target = player(mob.position[0] + 600.);
    mob.tick_target(0.5, &target);
    assert_eq!(mob.motion.speed, 0.);
    let target = player(mob.position[0] + 100.);
    let before = mob.position;
    mob.tick_target(0.5, &target);
    assert!(mob.position[0] < before[0]);
    assert_eq!(mob.motion.speed, -50.);
    assert_eq!(mob.speed, 50.);
    assert!(mob.motion.heading()[0] > 0.99);
    let packet = crate::packets::mob_movement(0x10000001, &mob, 123);
    let native_speed = f32::from_le_bytes(packet.body[42..46].try_into().unwrap());
    assert_eq!(native_speed, -0.05);
    let mut restored = self::mob(4000.);
    restored
        .restore_from(serde_json::from_slice(&serde_json::to_vec(&mob).unwrap()).unwrap())
        .unwrap();
    assert_eq!(restored.motion.speed, -50.);
    mob.motion.max_speed = -1.;
    assert!(restored.restore_from(mob).is_err());
}

#[test]
fn explicit_static_regions_stay_stationary_and_legacy_fields_default_without_losing_state() {
    let mut stationary = mob(0.);
    stationary.tick_idle(1., &[]);
    stationary.tick_target(1., &player(2000.));
    assert_eq!(stationary.position, [0.; 3]);
    assert_eq!(stationary.motion.speed, 0.);
    let mut value = serde_json::to_value(mob(4000.)).unwrap();
    value.as_object_mut().unwrap().remove("roaming");
    for key in ["spawn_region", "faction", "aggression"] {
        value["definition"].as_object_mut().unwrap().remove(key);
    }
    let mut restored = mob(4000.);
    restored
        .restore_from(serde_json::from_value(value).unwrap())
        .unwrap();
    restored.tick_idle(0.5, &[]);
    assert!(restored.roaming.initialized);
    let mut corrupt = restored.clone();
    corrupt.roaming.decision_seconds = f32::NAN;
    assert!(restored.restore_from(corrupt).is_err());
}

#[test]
fn entering_weapon_range_during_native_pursuit_has_a_rollback_snapshot_before_admission() {
    let root = std::env::temp_dir().join(format!("enb-roam-admit-{}", rand::random::<u64>()));
    std::fs::create_dir_all(&root).unwrap();
    let path = root.join("1076.json");
    let mut actor = mob(4000.);
    actor.definition.aggressive = true;
    actor.definition.aggression = Some(5);
    let initial = crate::sector_combat::SectorCombat::from_actors(vec![actor]);
    let mut world =
        crate::sector_combat::SectorCombat::persistent(path.clone(), 1076, &initial).unwrap();
    let before = serde_json::to_vec(&world.actors).unwrap();
    std::fs::create_dir(path.with_extension("json.next")).unwrap();
    assert!(world.advance_at(0.1, &[player(1501.)], 10000).is_err());
    assert_eq!(serde_json::to_vec(&world.actors).unwrap(), before);
    assert!(world.pending_mob_shots().is_empty());
    assert!(world.take_mob_launch_visuals().is_empty());
}

#[test]
fn region_and_native_movement_thresholds_reject_bad_data_without_guessing_paths() {
    assert!(!SpawnRegion {
        center: [0.; 3],
        radius: -1.
    }
    .valid());
    assert!(!SpawnRegion {
        center: [2_000_000.; 3],
        radius: 1.
    }
    .valid());
    assert_eq!(pursuit_speed(399., 200.), -50.);
    assert_eq!(pursuit_speed(400., 200.), 0.);
    assert_eq!(pursuit_speed(749., 200.), 0.);
    assert_eq!(pursuit_speed(750., 200.), 200.);
    assert_eq!(curious_speed(900., 130.), 130.); // preserve source horizon ordering
    assert_eq!(curious_speed(900., 229.), 0.);
}

#[test]
fn curiosity_does_not_survive_departure_contemplation_or_a_new_npc_life() {
    let mut mob = mob(4000.);
    mob.tick_idle(0.1, &[]);
    for action in 0..3 {
        mob.roaming.behavior = IdleBehavior::Curious {
            player: 0x40000001,
            epoch: 1,
        };
        match action {
            0 => mob.remove_player_hate(0x40000001),
            1 => mob.pacify(30.),
            _ => mob.damage(1000.),
        }
        assert!(matches!(mob.roaming.behavior, IdleBehavior::Drift));
    }
    mob.tick_visible(20., [0.; 3], false);
    assert_eq!(mob.state, MobState::Idle);
    assert!(matches!(mob.roaming.behavior, IdleBehavior::Drift));
}

#[test]
fn curiosity_uses_own_radius_but_spawn_boundary_does_not_reuse_mob_radius() {
    let mut mob = mob(4000.);
    mob.definition.radius = 500.;
    mob.roaming
        .initialize(mob.definition.source_id, mob.definition.template_id);
    mob.roaming.default_speed = 229.;
    let target = player(1400.); // edge900 is inside the native five-second horizon
    mob.roaming.behavior = IdleBehavior::Curious {
        player: target.id,
        epoch: target.session_epoch,
    };
    mob.tick_idle(0.1, std::slice::from_ref(&target));
    assert_eq!(mob.motion.speed, 0.);
    assert!(matches!(mob.roaming.behavior, IdleBehavior::Drift));
    // The marker, not the drifting NPC, is RangeFrom's receiver here. Until
    // marker radii are imported, use the existing center boundary unchanged.
    mob.position = [4100., 0., 0.];
    mob.choose_idle_change(&[]);
    assert!(mob.motion.heading()[0] < -0.99);
}
