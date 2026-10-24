//! Per-player faction acquisition, native aggression tier and retaliation.
use super::*;
use crate::classes::CharacterClass;
use crate::factions::FactionCatalog;
use std::collections::BTreeMap;

fn mob() -> Mob {
    Mob::new(serde_json::from_str(r#"{"source_id":1,"name":"Guard","level":1,"asset":48,"spawn":[0,0,0],"respawn_seconds":20,"aggressive":false,"aggression":3,"faction":3}"#).unwrap())
}

fn player(id: u32, standing: f32) -> PlayerThreat {
    PlayerThreat {
        id,
        session_epoch: 1,
        position: [100., 0., 0.],
        visible: true,
        signature: 0.,
        stealth: None,
        factions: FactionCatalog::load()
            .unwrap()
            .reactions(
                CharacterClass::parse("pw").unwrap(),
                &BTreeMap::from([(3, standing)]),
            )
            .unwrap(),
    }
}

#[test]
fn friendly_faction_is_not_acquired_but_an_attack_can_still_provoke_retaliation() {
    let mut mob = mob();
    let friend = player(0x40000001, 7500.);
    assert_eq!(mob.select_target(std::slice::from_ref(&friend)), None);
    mob.damage_by(1., friend.id);
    assert_eq!(mob.select_target(std::slice::from_ref(&friend)), Some(0));
    assert!(mob.targets(friend.id));
    assert_eq!(friend.factions.relationship(3, mob.targets(friend.id)), 0);
}

#[test]
fn faction_eligibility_is_per_player_and_numeric_tier_three_is_attack_on_sight() {
    let mut mob = mob();
    let friend = player(0x40000001, 7500.);
    let enemy = player(0x40000002, -2500.);
    let players = [friend, enemy];
    assert_eq!(mob.select_target(&players), Some(1));
    mob.definition.aggression = Some(2);
    mob.definition.aggressive = true; // explicit native tier overrides legacy bool
    assert_eq!(mob.select_target(&players), None);
    mob.definition.aggression = Some(3);
    assert_eq!(mob.select_target(&players), Some(1));
}
