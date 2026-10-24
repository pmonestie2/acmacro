//! Surface admission and absolute tractor travel are different native distances.
use super::*;

fn ship() -> PlayerSession {
    let mut ship = super::super::tests::session();
    ship.logged_in = true;
    ship.position = [0.; 3];
    let node: crate::content::ResourceNode = serde_json::from_str(
        r#"{
        "id":536870913,"name":"Large test asteroid","object_type":38,
        "asset":1822,"radius":800,"scale":10,"position":[3300,0,0],
        "field_id":1,"level":1,"resource_type":1,
        "ore_items":[{"item_id":2837,"frequency":1}]
    }"#,
    )
    .unwrap();
    ship.sector.resources = vec![node];
    ship.resource_field = Arc::new(Mutex::new(
        ResourceField::load(
            &ship.sector,
            &ship.items,
            ship.world_root.join("geometry-resources.json"),
        )
        .unwrap(),
    ));
    ship.target = 0x20000001;
    ship
}

fn start(ship: &mut PlayerSession) {
    ship.open_resource_window(ship.target).unwrap();
    ship.begin_mining(ItemTransfer {
        source: 18,
        source_slot: 0,
        destination: 0,
        destination_slot: 0,
        quantity: 0,
    })
    .unwrap();
}

#[test]
fn large_resource_is_mineable_at_surface_limit_but_tractor_uses_center_distance() {
    let mut ship = ship();
    assert_eq!(ship.prospect_range(), 2500.);
    start(&mut ship); // center3300 exceeds range, edge2500 is eligible.
    ship.mining.as_mut().unwrap().remaining = 0.;
    ship.mining.as_mut().unwrap().energy_left = 0.;
    ship.advance_mining(0.).unwrap();
    let cast = ship.mining.as_ref().unwrap();
    assert_eq!(cast.phase, MiningPhase::Tractor);
    let expected = (3300. / ship.equipment.ship.tractor_speed - 0.2).max(0.1);
    assert!((cast.remaining - expected).abs() < 0.001);
}

#[test]
fn crossing_surface_range_cancels_without_awarding_or_destroying_ore() {
    let mut ship = ship();
    start(&mut ship);
    let before = lock_resources(&ship.resource_field)
        .unwrap()
        .get(ship.target)
        .unwrap()
        .contents
        .clone();
    ship.position[0] = -1.; // Surface2501, outside the real mining limit.
    ship.advance_mining(0.1).unwrap();
    assert!(ship.mining.is_none());
    assert!(ship.inventory.cargo.iter().all(Option::is_none));
    assert_eq!(
        lock_resources(&ship.resource_field)
            .unwrap()
            .get(ship.target)
            .unwrap()
            .contents,
        before
    );
}
