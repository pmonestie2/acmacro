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
    assert_eq!(
        ship.energy,
        energy - ship.skill_catalog.ability(65).unwrap().energy
    );
    ship.advance_abilities(8.).unwrap();
    assert!(ship.hull > 0.);
    assert!(ship.skills.cast.is_none());
    assert!(ship
        .skills
        .cooldowns
        .get(&31)
        .is_some_and(|cooldown| *cooldown > 0.));
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
        matches!(ship.support_actions.last(), Some(SupportAction::Jumpstart { target }) if *target == peer.profile.game_id)
    );
    assert!(ship.can_deliver_jumpstart(&peer));
    let mut departed = peer.clone();
    departed.sector += 1;
    assert!(!ship.can_deliver_jumpstart(&departed));
    departed = peer;
    departed.hull = 1.;
    assert!(!ship.can_deliver_jumpstart(&departed));
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
