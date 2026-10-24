//! Radius tests use explicit scenario geometry rather than a client installation.
use super::*;

fn mob() -> Mob {
    let mut definition: MobDefinition = serde_json::from_str(r#"{"source_id":71,"template_id":46,"name":"Radius test","level":1,"asset":48,"radius":500,"scale":3,"spawn":[0,0,0],"respawn_seconds":20,"aggressive":true,"aggression":5,"spawn_region":{"center":[0,0,0],"radius":0}}"#).unwrap();
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
fn range_subtracts_only_receiver_unscaled_radius_and_clamps_inside_surface() {
    let mob = mob();
    assert_eq!(mob.range_to([1600., 0., 0.]), 1100.);
    assert_eq!(mob.range_to([300., 400., 0.]), 0.);
    assert_eq!(mob.range_to([10., 0., 0.]), 0.);
    assert_eq!(mob.range_to([0.; 3]), 0.);
    assert!(mob.range_to([f32::NAN, 0., 0.]).is_nan());
    let mut definition = serde_json::to_value(&mob.definition).unwrap();
    definition.as_object_mut().unwrap().remove("radius");
    let legacy: MobDefinition = serde_json::from_value(definition).unwrap();
    assert_eq!(legacy.radius, 0.);
    for radius in [-1., f32::NAN, f32::INFINITY, 2_000_001.] {
        let mut invalid = mob.definition.clone();
        invalid.radius = radius;
        assert!(invalid.validate().is_err());
    }
}

#[test]
fn sensors_use_npc_edge_range_for_strict_entry_and_retention() {
    let mut mob = mob();
    assert_eq!(mob.select_target(&[player(3150.)]), None); // 2650 edge
    assert_eq!(mob.select_target(&[player(3149.)]), Some(0));
    assert_eq!(mob.select_target(&[player(3350.)]), Some(0)); // +200 hysteresis
    assert_eq!(mob.select_target(&[player(3350.1)]), None);
    assert_eq!(mob.select_target(&[player(f32::NAN)]), None);
}

#[test]
fn native_pursuit_standoff_uses_own_edge_but_faces_the_target_center() {
    let mut mob = mob();
    mob.definition.spawn_region.as_mut().unwrap().radius = 4000.;
    mob.tick_target(0.1, &player(1100.)); // edge600 holds
    assert_eq!(mob.motion.speed, 0.);
    assert_eq!(mob.position, [0.; 3]);
    mob.tick_target(0.1, &player(600.)); // edge100 backs off
    assert_eq!(mob.motion.speed, -50.);
    assert!(mob.position[0] < 0. && mob.motion.heading()[0] > 0.99);
}

#[test]
fn launcher_admission_and_frozen_flight_delay_use_npc_edge_range() {
    let mut actor = mob();
    actor.definition.weapon = Some(crate::mob_stats::MobWeapon {
        item: 1,
        item_type: 15,
        damage: 1.,
        damage_type: 3,
        reload: 2.,
        effect: 35,
    });
    let mut world = crate::sector_combat::SectorCombat::from_actors(vec![actor]);
    world.bind_sector(1076).unwrap();
    world.advance_at(0.1, &[player(1999.)], 10000).unwrap();
    let shots = world.pending_mob_shots();
    assert_eq!(shots.len(), 1); // center1999 exceeds nominal1500 weapon range
    assert_eq!(shots[0].duration_ms, 4497); // (1999-500)*3
    assert_eq!(shots[0].due_ms, 14497);
}

#[test]
fn launch_at_edge_range_reserves_rollback_before_failed_world_save() {
    let root = std::env::temp_dir().join(format!("enb-radius-{}", rand::random::<u64>()));
    std::fs::create_dir_all(&root).unwrap();
    let path = root.join("1076.json");
    let initial = crate::sector_combat::SectorCombat::from_actors(vec![mob()]);
    let mut world =
        crate::sector_combat::SectorCombat::persistent(path.clone(), 1076, &initial).unwrap();
    let before = serde_json::to_vec(&world.actors).unwrap();
    std::fs::create_dir(path.with_extension("json.next")).unwrap();
    assert!(world.advance_at(0.1, &[player(1999.)], 10000).is_err());
    assert_eq!(serde_json::to_vec(&world.actors).unwrap(), before);
    assert!(world.pending_mob_shots().is_empty());
    assert!(world.take_mob_launch_visuals().is_empty());
    std::fs::remove_dir_all(root).unwrap();
}
