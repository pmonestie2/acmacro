//! Authenticated character selection replaces stale transport ownership.
use super::*;

fn packet(owner: u32, opcode: u16, sequence: u32, body: Vec<u8>) -> Datagram {
    Datagram {
        opcode,
        player: owner,
        sequence,
        body,
    }
}

#[test]
fn new_selection_takes_over_immediately_and_old_logout_packets_cannot_close_it() {
    let mut ship = super::tests::session();
    let ip: IpAddr = "127.0.0.1".parse().unwrap();
    let old_sector: SocketAddr = "127.0.0.1:22451".parse().unwrap();
    let old_comms: SocketAddr = "127.0.0.1:22452".parse().unwrap();
    let new_sector: SocketAddr = "127.0.0.1:22453".parse().unwrap();
    let new_comms: SocketAddr = "127.0.0.1:22454".parse().unwrap();
    ship.logged_in = true;
    ship.selected_peer = Some(ip);
    ship.endpoint = Some(old_sector);
    ship.comms = Some(old_comms);
    ship.weapon_firing[3] = true;
    ship.player_firing = true;
    ship.support_actions
        .push(SupportAction::Energy { amount: 1. });
    ship.client_events.push(ClientEvent::Chat {
        channel: 0,
        text: "old queued chat".into(),
    });
    let epoch = ship.session_epoch;
    let owner = ship.player_id();
    let reply = ship.select(0, ip, false).unwrap();
    assert!(!ship.logged_in);
    assert!(ship.endpoint.is_none() && ship.comms.is_none());
    assert!(!ship.player_firing);
    assert!(ship.support_actions.is_empty() && ship.client_events.is_empty());
    assert_ne!(ship.session_epoch, epoch);
    ship.handle_udp(
        3806,
        old_comms,
        packet(owner, 0x1008, 0, owner.to_le_bytes().to_vec()),
    )
    .unwrap();
    assert_eq!(ship.selected_peer, Some(ip));
    ship.handle_udp(3806, new_comms, packet(owner, 0x200f, 0, vec![]))
        .unwrap();
    ship.handle_udp(
        3808,
        new_sector,
        packet(owner, 0x2008, 0, ship.sector.id.to_le_bytes().to_vec()),
    )
    .unwrap();
    let mut login = vec![0; 137];
    login[..64].copy_from_slice(&reply.body[4..]);
    ship.handle_udp(SECTOR_PORT, new_sector, packet(owner, 2, 1, login))
        .unwrap();
    assert!(ship.logged_in);
    let active_epoch = ship.session_epoch;
    ship.handle_udp(
        3806,
        old_comms,
        packet(owner, 0x1008, 0, owner.to_le_bytes().to_vec()),
    )
    .unwrap();
    assert!(ship
        .handle_udp(SECTOR_PORT, old_sector, packet(owner, 3, 2, vec![]))
        .is_err());
    assert!(ship.logged_in);
    assert_eq!(ship.endpoint, Some(new_sector));
    assert_eq!(ship.session_epoch, active_epoch);
    ship.handle_udp(
        3806,
        new_comms,
        packet(owner, 0x1008, 0, owner.to_le_bytes().to_vec()),
    )
    .unwrap();
    assert!(!ship.logged_in);
}

#[test]
fn invalid_character_slot_does_not_displace_the_current_player() {
    let mut ship = super::tests::session();
    let ip: IpAddr = "127.0.0.1".parse().unwrap();
    ship.logged_in = true;
    ship.selected_peer = Some(ip);
    ship.following = true;
    assert!(ship.select(1, ip, false).is_err());
    assert!(ship.logged_in && ship.following);
    assert_eq!(ship.selected_peer, Some(ip));
}
