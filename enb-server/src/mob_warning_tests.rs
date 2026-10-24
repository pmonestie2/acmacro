use super::*;

fn mob() -> Mob {
    Mob::new(serde_json::from_str(r#"{"source_id":71,"template_id":46,"name":"Warning test","level":1,"asset":48,"spawn":[0,0,0],"respawn_seconds":20,"aggressive":false,"aggression":1,"faction":3}"#).unwrap())
}

fn player(id: u32) -> PlayerThreat {
    PlayerThreat {
        id,
        session_epoch: 1,
        position: [500., 0., 0.],
        visible: true,
        signature: 0.,
        stealth: None,
        factions: crate::factions::FactionReactions::neutral(),
    }
}

fn seed_for(roll: u32) -> u64 {
    (1..100000)
        .find(|seed| WorldRandom(*seed).below(100) == roll)
        .unwrap()
}

#[test]
fn source_strict_rolls_have_exact_warning_probabilities_and_hate() {
    for (aggression, count, hate) in [(1, 2, 1.), (2, 4, 5.), (3, 6, 20.), (4, 9, 100.)] {
        let values: Vec<_> = (0..100)
            .map(|roll| warning_hate(aggression, roll))
            .filter(|value| *value > 0.)
            .collect();
        assert_eq!(values, vec![hate; count]);
    }
    for aggression in [0, 5, 9, 255] {
        assert_eq!(warning_hate(aggression, 99), 0.);
    }
    assert_eq!(warning_hate(4, 100), 0.);
}

#[test]
fn warning_requires_previous_visibility_pass_and_obeys_half_second_active_clock() {
    let mut mob = mob();
    let players = [player(0x40000001)];
    mob.warnings.random_state = seed_for(98);
    let before = mob.warnings.random_state;
    for _ in 0..5 {
        assert_eq!(mob.select_target_with_clock(0.1, &players), None);
    }
    assert_eq!(mob.warnings.random_state, before); // first entry never warns
    for _ in 0..4 {
        assert_eq!(mob.select_target_with_clock(0.1, &players), None);
    }
    assert_eq!(mob.select_target_with_clock(0.1, &players), Some(0));
    assert!(mob.targets(players[0].id));
    let before = serde_json::to_value(&mob.warnings).unwrap();
    mob.select_target(&players); // target query does not advance simulation
    mob.select_target_with_clock(60., &players); // no offline warning rolls
    assert_eq!(serde_json::to_value(&mob.warnings).unwrap(), before);
}

#[test]
fn warning_rejects_friends_boundary_unavailable_new_epochs_and_undetectable_cloak() {
    use crate::classes::CharacterClass;
    use crate::factions::FactionCatalog;
    for case in 0..5 {
        let mut mob = mob();
        let mut player = player(0x40000001);
        mob.select_target_with_clock(0.5, std::slice::from_ref(&player));
        mob.warnings.random_state = seed_for(99);
        let before = mob.warnings.random_state;
        match case {
            0 => {
                player.factions = FactionCatalog::load()
                    .unwrap()
                    .reactions(
                        CharacterClass::parse("pw").unwrap(),
                        &BTreeMap::from([(3, 1.)]),
                    )
                    .unwrap()
            }
            1 => player.position[0] = 1000.,
            2 => player.visible = false,
            3 => player.session_epoch += 1,
            _ => player.stealth = Some(4), // level1 detects only stealth<=3
        }
        assert_eq!(
            mob.select_target_with_clock(0.5, std::slice::from_ref(&player)),
            None
        );
        if case < 4 {
            assert_eq!(mob.warnings.random_state, before);
        } else {
            assert_ne!(mob.warnings.random_state, before); // native checks cloak later
            assert_eq!(mob.aggro.most_hated(|_| true), None);
        }
    }
}

#[test]
fn saved_warning_stream_clock_and_stable_player_order_continue_without_rerolls() {
    let mut original = mob();
    let players = [player(0x40000002), player(0x40000001)];
    original.select_target_with_clock(0.5, &players);
    original.select_target_with_clock(0.2, &players);
    let saved = serde_json::from_slice(&serde_json::to_vec(&original).unwrap()).unwrap();
    let mut restored = mob();
    restored.restore_from(saved).unwrap();
    let reversed = [players[1].clone(), players[0].clone()];
    for _ in 0..30 {
        original.select_target_with_clock(0.1, &players);
        restored.select_target_with_clock(0.1, &reversed);
        assert_eq!(
            serde_json::to_value(&original.warnings).unwrap(),
            serde_json::to_value(&restored.warnings).unwrap()
        );
        assert_eq!(
            serde_json::to_value(&original.aggro).unwrap(),
            serde_json::to_value(&restored.aggro).unwrap()
        );
    }
    let mut legacy = serde_json::to_value(&original).unwrap();
    legacy.as_object_mut().unwrap().remove("warnings");
    restored
        .restore_from(serde_json::from_value(legacy).unwrap())
        .unwrap();
    assert!(restored.warnings.contacts.is_empty());
    let mut corrupt = original.clone();
    corrupt.warnings.elapsed = f32::NAN;
    assert!(restored.restore_from(corrupt).is_err());
}

#[test]
fn warning_contacts_clear_on_departure_pacify_and_death() {
    for action in 0..3 {
        let mut mob = mob();
        let player = player(0x40000001);
        mob.select_target_with_clock(0.5, std::slice::from_ref(&player));
        assert!(!mob.warnings.contacts.is_empty());
        match action {
            0 => mob.remove_player_hate(player.id),
            1 => mob.pacify(10.),
            _ => mob.damage(1_000_000.),
        }
        assert!(mob.warnings.contacts.is_empty());
    }
}

#[test]
fn warning_induced_launch_failure_restores_hate_clock_rng_and_contacts() {
    let root = std::env::temp_dir().join(format!("enb-warning-{}", rand::random::<u64>()));
    std::fs::create_dir_all(&root).unwrap();
    let path = root.join("1076.json");
    let mut actor = mob();
    let players = [player(0x40000001)];
    actor.select_target_with_clock(0.5, &players);
    actor.warnings.random_state = seed_for(99);
    let initial = crate::sector_combat::SectorCombat::from_actors(vec![actor]);
    let mut world =
        crate::sector_combat::SectorCombat::persistent(path.clone(), 1076, &initial).unwrap();
    let before = serde_json::to_vec(&world.actors).unwrap();
    std::fs::create_dir(path.with_extension("json.next")).unwrap();
    assert!(world.advance_at(0.5, &players, 10000).is_err());
    assert_eq!(serde_json::to_vec(&world.actors).unwrap(), before);
    assert!(world.pending_mob_shots().is_empty());
    assert!(world.take_mob_launch_visuals().is_empty());
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn warning_proximity_uses_unscaled_npc_edge_with_strict_thousand_metre_boundary() {
    for (center, expected) in [(1499., Some(0)), (1500., None)] {
        let mut mob = mob();
        mob.definition.radius = 500.;
        mob.definition.scale = 4.;
        let mut player = player(0x40000001);
        player.position[0] = center;
        mob.select_target_with_clock(0.5, std::slice::from_ref(&player));
        mob.warnings.random_state = seed_for(99);
        assert_eq!(mob.select_target_with_clock(0.5, &[player]), expected);
    }
}
