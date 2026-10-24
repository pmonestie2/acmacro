//! Player-side sensor publication and trusted group-cloak delivery.
use super::*;

fn ship() -> PlayerSession {
    let mut ship = super::super::tests::session();
    ship.logged_in = true;
    ship.equipment.ship.scan_range = 1000.;
    ship.position = [0.; 3];
    ship.pending.clear();
    ship
}

fn move_mob(ship: &PlayerSession, range: f32) {
    lock_combat(&ship.combat).unwrap().actors[0].position = [range, 0., 0.];
}

#[test]
fn scan_visibility_hysteresis_controls_create_remove_and_live_target_consumers() {
    let mut ship = ship();
    move_mob(&ship, 3999.);
    ship.publish_mobs().unwrap();
    assert!(ship.mob_visible[0]);
    assert!(ship
        .pending
        .iter()
        .any(|frame| frame.opcode == 4 && frame.body[..4] == MOB_ID.to_le_bytes()));
    ship.target = MOB_ID;
    ship.weapon_firing[3] = true;
    ship.player_firing = true;
    move_mob(&ship, 4200.);
    ship.publish_mobs().unwrap();
    assert!(ship.mob_visible[0]);
    assert!(ship.warp_waypoint(MOB_ID).is_ok());
    move_mob(&ship, 4200.5);
    ship.pending.clear();
    ship.publish_mobs().unwrap();
    assert!(!ship.mob_visible[0]);
    assert_eq!(ship.target, u32::MAX);
    assert!(!ship.player_firing);
    assert!(ship
        .pending
        .iter()
        .any(|frame| frame.opcode == 7 && frame.body == MOB_ID.to_le_bytes()));
    assert!(ship.warp_waypoint(MOB_ID).is_err());
    move_mob(&ship, 4100.);
    ship.publish_mobs().unwrap();
    assert!(!ship.mob_visible[0]);
    let owner = ship.player_id();
    ship.handle_input(0x17, &[owner.to_le_bytes(), MOB_ID.to_le_bytes()].concat())
        .unwrap();
    assert_eq!(ship.target, u32::MAX);
    move_mob(&ship, 3999.);
    ship.publish_mobs().unwrap();
    assert!(ship.mob_visible[0]);
}

#[test]
fn cloak_stealth_uses_trained_skill_and_group_recipients_keep_caster_detection_strength() {
    let mut owner = ship();
    owner.skills.cloak_rank = 1; // selected variant is not the trained rank7
    let threat = owner.threat();
    assert!(threat.visible);
    assert_eq!(threat.stealth, Some(185));
    assert_eq!(threat.signature, owner.equipment.ship.signature);
    let mut recipient = ship();
    recipient.account.character.race = 2;
    recipient.account.character.profession = 0;
    recipient.skill_catalog = SkillCatalog::for_profile(&recipient.account.character).unwrap();
    assert_eq!(recipient.skill_catalog.level(12), 0);
    recipient
        .receive_support(
            owner.player_id(),
            &SupportAction::Cloak {
                enabled: true,
                rank: 7,
                stealth: 185,
            },
        )
        .unwrap();
    assert_eq!(recipient.threat().stealth, Some(185));
    recipient
        .receive_support(
            owner.player_id(),
            &SupportAction::Cloak {
                enabled: false,
                rank: 0,
                stealth: 0,
            },
        )
        .unwrap();
    assert_eq!(recipient.threat().stealth, None);
}

#[test]
fn sensor_hysteresis_and_cloak_strength_survive_a_short_session_replacement() {
    let mut ship = ship();
    ship.selected_peer = Some("127.0.0.1".parse().unwrap());
    ship.endpoint = Some("127.0.0.1:22343".parse().unwrap());
    ship.skills.cloak_rank = 1;
    ship.skills.cloak_stealth = Some(185);
    // Use the actual equipped range because equipment is derived again on load.
    ship.refresh_equipment().unwrap();
    let edge = ship.equipment.ship.scan_range + 3000.;
    move_mob(&ship, edge - 1.);
    ship.publish_mobs().unwrap();
    move_mob(&ship, edge + 100.);
    ship.publish_mobs().unwrap();
    assert!(ship.mob_visible[0]);
    ship.save().unwrap();
    ship.checkpoint_session(true).unwrap();
    let restored =
        PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
    assert!(restored.logged_in);
    assert!(restored.mob_visible[0]);
    assert_eq!(restored.threat().stealth, Some(185));
}
