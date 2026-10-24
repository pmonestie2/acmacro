use super::*;

#[test]
fn wreck_surface_allows_tractor_and_crossing_range_cancels_with_stock_preserved() {
    let mut ship = super::super::tests::session();
    ship.logged_in = true;
    ship.position = [0.; 3];
    let shared = Arc::clone(&ship.loot_world);
    let id = lock_loot(&shared)
        .unwrap()
        .spawn(
            crate::loot_world::CorpseBirth {
                death: crate::loot_world::MobDeath {
                    spawn: 1,
                    generation: 1,
                },
                template: 2010,
                name: "Guaranteed source drop".into(),
                level: 10,
                killer: ship.player_id(),
                position: [2001., 0., 0.],
                now_ms: unix_ms(),
            },
            &crate::loot::LootCatalog::load().unwrap(),
            &ship.items,
        )
        .unwrap()
        .unwrap();
    ship.target = id;
    ship.open_loot().unwrap();
    ship.begin_looting(0).unwrap(); // center2001, surface<2000.
    let expected = (2001. / ship.equipment.ship.tractor_speed - 0.15).max(0.1);
    assert!((ship.loot_cast.as_ref().unwrap().remaining - expected).abs() < 0.001);
    let stock = lock_loot(&shared)
        .unwrap()
        .get(id)
        .unwrap()
        .remaining()
        .unwrap()
        .items;
    ship.position[0] = -2.;
    ship.advance_looting(0.1).unwrap();
    assert!(ship.loot_cast.is_none());
    assert!(ship.inventory.cargo.iter().all(Option::is_none));
    assert_eq!(
        lock_loot(&shared)
            .unwrap()
            .get(id)
            .unwrap()
            .remaining()
            .unwrap()
            .items,
        stock
    );
}
