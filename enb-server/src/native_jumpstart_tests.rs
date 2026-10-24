use super::*;

fn ship() -> PlayerSession {
    let mut ship = super::super::tests::session();
    ship.logged_in = true;
    ship.position = [0.; 3];
    ship.energy = ship.equipment.ship.energy_capacity.max(1000.);
    ship
}

fn action(target: u32, ship: &mut PlayerSession) {
    let body = [ship.player_id(), 26, target, 0]
        .map(u32::to_le_bytes)
        .concat();
    ship.handle_input(0x2c, &body).unwrap();
}

fn peer(ship: &mut PlayerSession, x: f32) -> crate::peers::PeerSnapshot {
    let mut peer = ship.peer_snapshot().unwrap();
    peer.profile.game_id += 1;
    peer.position = [x, 0., 0.];
    peer.hull = 0.;
    ship.update_peer_view(std::slice::from_ref(&peer), &[]);
    peer
}

#[test]
fn incapacitated_native_self_verb_and_action_use_catalog_cast_without_changing_selection() {
    let mut ship = ship();
    ship.hull = 0.;
    ship.target = ship.player_id();
    ship.refresh_target_verbs(true);
    assert_eq!(
        ship.last_verbs,
        Some(packets::target_verbs(ship.player_id(), &[(12, 0)]))
    );
    let selected = ship.target;
    let energy = ship.energy;
    action(ship.player_id(), &mut ship);
    let cast = ship.skills.cast.as_ref().unwrap();
    assert_eq!(cast.ability, 65);
    assert_eq!(cast.target, ship.player_id());
    assert_eq!(ship.target, selected);
    assert_eq!(ship.energy, energy - 120.);
    assert_eq!(cast.remaining, 5.);
    assert_eq!(cast.jumpstart.as_ref().unwrap().repair_fraction, 0.7);
    ship.advance_abilities(4.9).unwrap();
    assert_eq!(ship.hull, 0.);
    ship.advance_abilities(0.2).unwrap();
    assert!(ship.hull > 0.);
    assert!(ship.skills.cast.is_none());
    assert!(!ship.skills.cooldowns.contains_key(&31));
    assert_eq!(ship.hull, ship.equipment.ship.hull_capacity * 0.7);
    assert_eq!(ship.shield, 0.);
}

#[test]
fn nongroup_incapped_peer_is_eligible_and_packet_target_stays_frozen_during_cast() {
    let mut ship = ship();
    let peer = peer(&mut ship, 1000.);
    assert!(ship.group_contacts.is_empty());
    ship.target = ship.player_id();
    action(peer.profile.game_id, &mut ship);
    assert_eq!(
        ship.skills.cast.as_ref().unwrap().target,
        peer.profile.game_id
    );
    assert_eq!(ship.target, ship.player_id());
    ship.advance_abilities(8.).unwrap();
    assert!(
        matches!(ship.support_actions.last(), Some(SupportAction::Jumpstart { target, repair_fraction, range }) if *target == peer.profile.game_id && *repair_fraction == 0.7 && *range == 2750.)
    );
    assert!(ship.can_deliver_jumpstart(&peer, 2750.));
    let mut departed = peer.clone();
    departed.sector += 1;
    assert!(!ship.can_deliver_jumpstart(&departed, 2750.));
    departed = peer;
    departed.hull = 1.;
    assert!(!ship.can_deliver_jumpstart(&departed, 2750.));
}

#[test]
fn range_uses_native_player_surface_and_ui_range_remains_one_step_shorter() {
    let mut ship = ship();
    let mut peer = peer(&mut ship, 2745.); // UI2500 plus player245
    ship.target = peer.profile.game_id;
    ship.refresh_target_verbs(true);
    assert_eq!(
        ship.last_verbs,
        Some(packets::target_verbs(
            ship.target,
            &[(4, 2), (13, 2), (12, 0)]
        ))
    );
    peer.position[0] += 1.;
    ship.update_peer_view(std::slice::from_ref(&peer), &[]);
    ship.refresh_target_verbs(false);
    assert_eq!(
        ship.last_verbs,
        Some(packets::target_verbs(
            ship.target,
            &[(4, 2), (13, 2), (12, 2)]
        ))
    );
    peer.position[0] = 2995.; // catalog2750 plus player245
    ship.update_peer_view(std::slice::from_ref(&peer), &[]);
    ship.begin_jumpstart(ship.target).unwrap();
    assert!(ship.skills.cast.is_some());
    peer.position[0] += 1.;
    ship.update_peer_view(std::slice::from_ref(&peer), &[]);
    ship.advance_abilities(0.1).unwrap();
    assert!(ship.skills.cast.is_none());
    assert!(ship.support_actions.is_empty());
    assert!(!ship.skills.cooldowns.contains_key(&31));
    assert!(ship.begin_jumpstart(ship.target).is_err());
}

#[test]
fn class_training_energy_and_caster_incapacity_are_authoritative() {
    for case in 0..4 {
        let mut ship = ship();
        let peer = peer(&mut ship, 500.);
        match case {
            0 => ship.account.character.profession = 0,
            1 => {
                ship.skill_catalog
                    .skills
                    .iter_mut()
                    .find(|skill| skill.id == 31)
                    .unwrap()
                    .level = 0
            }
            2 => ship.energy = 0.,
            _ => ship.hull = 0.,
        }
        let energy = ship.energy;
        assert!(ship.begin_jumpstart(peer.profile.game_id).is_err());
        assert!(ship.skills.cast.is_none());
        assert_eq!(ship.energy, energy);
        if case < 2 {
            ship.target = peer.profile.game_id;
            ship.refresh_target_verbs(true);
            assert_eq!(
                ship.last_verbs,
                Some(packets::target_verbs(ship.target, &[(4, 2), (13, 2)]))
            );
        }
    }
}

#[test]
fn peer_departure_revival_or_warp_cancels_cast_and_other_support_stays_group_only() {
    for case in 0..3 {
        let mut ship = ship();
        let mut peer = peer(&mut ship, 500.);
        ship.begin_jumpstart(peer.profile.game_id).unwrap();
        match case {
            0 => ship.update_peer_view(&[], &[]),
            1 => {
                peer.hull = 1.;
                ship.update_peer_view(&[peer], &[]);
            }
            _ => {
                ship.warp
                    .start(
                        std::collections::VecDeque::from([crate::warp::Waypoint {
                            id: 71,
                            position: [10000., 0., 0.],
                            stand_off: 100.,
                        }]),
                        ship.position,
                        &mut ship.motion,
                    )
                    .unwrap();
            }
        }
        ship.advance_abilities(8.).unwrap();
        assert!(ship.skills.cast.is_none());
        assert!(ship.support_actions.is_empty());
    }
    let mut ship = ship();
    let peer = peer(&mut ship, 500.);
    ship.target = peer.profile.game_id;
    assert!(ship.begin_ability(125).is_err()); // targeted Fold still requires group
    assert!(ship.begin_jumpstart(ship.player_id()).is_err()); // living self
    ship.hull = 0.;
    assert!(ship.begin_jumpstart(u32::MAX).is_err()); // native action needs an object
}

#[test]
fn frozen_native_plan_survives_process_replacement_without_recharging_or_repair_reroll() {
    let mut original = super::super::tests::journal_ship();
    original.hull = 0.;
    original.energy = original.equipment.ship.energy_capacity;
    original.begin_jumpstart(original.player_id()).unwrap();
    original.advance_abilities(2.).unwrap();
    let energy = original.energy;
    let plan = original.skills.cast.as_ref().unwrap().jumpstart.clone();
    original.checkpoint_session(true).unwrap();
    let mut resumed = PlayerSession::new(
        original.sector.clone(),
        original.bind,
        original.state_dir.clone(),
    )
    .unwrap();
    assert!(resumed.logged_in);
    let cast = resumed.skills.cast.as_ref().unwrap();
    assert_eq!(cast.remaining, 3.);
    assert_eq!(cast.jumpstart, plan);
    assert_eq!(resumed.energy, energy);
    // The source level was frozen when Execute committed, not at Update.
    resumed
        .skill_catalog
        .skills
        .iter_mut()
        .find(|skill| skill.id == 31)
        .unwrap()
        .level = 4;
    resumed.advance_abilities(2.9).unwrap();
    assert_eq!(resumed.hull, 0.);
    resumed.advance_abilities(0.2).unwrap();
    assert_eq!(resumed.hull, resumed.equipment.ship.hull_capacity * 0.7);
    assert_eq!(resumed.energy, energy);
    assert!(!resumed.skills.cooldowns.contains_key(&31));
}

#[test]
fn legacy_checkpoint_preserves_its_paid_cost_remaining_time_and_repair_until_completion() {
    let mut original = super::super::tests::journal_ship();
    original.hull = 0.;
    original.energy = original.equipment.ship.energy_capacity;
    original.begin_jumpstart(original.player_id()).unwrap();
    original.checkpoint_session(true).unwrap();
    let path = original.state_dir.join("session.local.json");
    let mut saved: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    saved["skills"]["cast"]
        .as_object_mut()
        .unwrap()
        .remove("jumpstart");
    saved["skills"]["cast"]["remaining"] = 3.25.into();
    let paid_energy = original.equipment.ship.energy_capacity - 140.;
    saved["energy"] = paid_energy.into();
    fs::write(&path, serde_json::to_vec(&saved).unwrap()).unwrap();
    let mut resumed = PlayerSession::new(
        original.sector.clone(),
        original.bind,
        original.state_dir.clone(),
    )
    .unwrap();
    assert!(resumed.logged_in);
    assert_eq!(resumed.energy, paid_energy);
    assert_eq!(resumed.skills.cast.as_ref().unwrap().remaining, 3.25);
    assert!(resumed.skills.cast.as_ref().unwrap().jumpstart.is_none());
    resumed.advance_abilities(3.).unwrap();
    assert_eq!(resumed.hull, 0.);
    resumed.advance_abilities(0.3).unwrap();
    assert_eq!(resumed.hull, resumed.equipment.ship.hull_capacity * 0.49);
    assert_eq!(resumed.energy, paid_energy);
    assert_eq!(resumed.skills.cooldowns[&31], 10.);
    resumed.hull = 0.;
    resumed.energy = resumed.equipment.ship.energy_capacity;
    resumed.skills.cooldowns.remove(&31);
    resumed.begin_jumpstart(resumed.player_id()).unwrap();
    assert!(resumed.skills.cast.as_ref().unwrap().jumpstart.is_some());
}

#[test]
fn native_effects_and_frozen_recipient_fraction_use_the_real_peer_target() {
    for own in [false, true] {
        let mut ship = ship();
        let target = if own {
            ship.hull = 0.;
            ship.player_id()
        } else {
            peer(&mut ship, 500.).profile.game_id
        };
        ship.begin_jumpstart(target).unwrap();
        let effect = ship
            .pending
            .iter()
            .rev()
            .find(|frame| frame.opcode == 0x0b)
            .unwrap();
        assert_eq!(
            u32::from_le_bytes(effect.body[6..10].try_into().unwrap()),
            target
        );
        assert_eq!(
            u16::from_le_bytes(effect.body[10..12].try_into().unwrap()),
            if own { 411 } else { 414 }
        );
    }
    let mut receiver = ship();
    receiver.hull = 0.;
    receiver.shield = 10.;
    let action = SupportAction::Jumpstart {
        target: receiver.player_id(),
        repair_fraction: 0.4,
        range: 2000.,
    };
    receiver
        .receive_support(receiver.player_id() + 1, &action)
        .unwrap();
    assert_eq!(receiver.hull, receiver.equipment.ship.hull_capacity * 0.4);
    assert_eq!(receiver.shield, 0.);
    receiver.hull = 0.;
    for fraction in [0., -1., 1.1, f32::NAN, f32::INFINITY] {
        assert!(receiver
            .receive_support(
                receiver.player_id() + 1,
                &SupportAction::Jumpstart {
                    target: receiver.player_id(),
                    repair_fraction: fraction,
                    range: 2000.,
                }
            )
            .is_err());
        assert_eq!(receiver.hull, 0.);
    }
}

#[test]
fn failed_native_skill_requirement_has_no_cost_and_checkpoint_plans_bind_to_jumpstart() {
    let mut ship = ship();
    ship.hull = 0.;
    ship.skill_catalog
        .skills
        .iter_mut()
        .find(|skill| skill.id == 31)
        .unwrap()
        .level = 3;
    let energy = ship.energy;
    assert!(ship.begin_jumpstart(ship.player_id()).is_err()); // hull6 > trained3 +2
    assert_eq!(ship.energy, energy);
    assert!(ship.skills.cast.is_none());
    ship.skill_catalog
        .skills
        .iter_mut()
        .find(|skill| skill.id == 31)
        .unwrap()
        .level = 7;
    ship.begin_jumpstart(ship.player_id()).unwrap();
    let mut cast = ship.skills.cast.clone().unwrap();
    assert!(cast.valid());
    cast.ability = 28;
    assert!(!cast.valid());
    cast.ability = 65;
    cast.remaining = 6.;
    assert!(!cast.valid());
    ship.selected_peer = Some("127.0.0.1".parse().unwrap());
    ship.endpoint = Some("127.0.0.1:22343".parse().unwrap());
    ship.save().unwrap();
    ship.checkpoint_session(true).unwrap();
    let path = ship.state_dir.join("session.local.json");
    let mut saved: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    saved["skills"]["cast"]["ability"] = 28.into();
    fs::write(&path, serde_json::to_vec(&saved).unwrap()).unwrap();
    let paid_energy = ship.energy;
    assert!(ship.restore_session().is_err());
    assert_eq!(ship.energy, paid_energy);
    assert_eq!(ship.hull, 0.);
}
