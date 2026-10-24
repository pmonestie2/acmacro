//! Client verb IDs differ from ActionPacket action numbers. Availability must
//! change as the ship approaches/leaves its target, not only when selected.
use super::*;

impl PlayerSession {
    pub(super) fn refresh_target_verbs(&mut self, force: bool) {
        if !self.logged_in || self.target == u32::MAX || self.sector.station.is_some() {
            return;
        }
        let mut verbs = Vec::new();
        let can_act = self.hull > 0. && !self.warp.active() && self.gate_transit.is_none();
        if self.target == self.player_id() && self.hull <= 0. && self.jumpstart_definition().is_ok()
        {
            verbs.push((
                12,
                if !self.warp.active() && self.gate_transit.is_none() {
                    0
                } else {
                    2
                },
            ));
        }
        if let Some(peer) = self.peer_contacts.get(&self.target) {
            // Native Player::OnTargeted advertises GROUP with unlimited range
            // for living peers. Verb4 opens Group/Invite; action10 sends it.
            let alive = self.hull > 0. && !peer.incapacitated;
            verbs.push((4, if alive { 0 } else { 2 }));
            // The follow button uses its native surface-distance threshold;
            // keyboard follow remains handled by the existing impulse command.
            let range =
                (distance(self.position, peer.position) - crate::following::PLAYER_RADIUS).max(0.);
            verbs.push((13, if alive && range <= 1500. { 0 } else { 2 }));
            if peer.incapacitated && self.jumpstart_definition().is_ok() {
                // Player::OnTargeted's UI range is one skill step shorter than
                // AJumpStart::CalculateRange; retain that native distinction.
                let range_limit =
                    1000. + self.skill_catalog.level(31).saturating_sub(1) as f32 * 250.;
                verbs.push((
                    12,
                    if can_act && range <= range_limit {
                        0
                    } else {
                        2
                    },
                ));
            }
        }
        if self.visible_corpses.contains_key(&self.target) {
            verbs.push((
                3,
                if can_act
                    && lock_loot(&self.loot_world).is_ok_and(|world| {
                        world.get(self.target).is_ok_and(|corpse| {
                            corpse
                                .range_to(self.position)
                                .is_ok_and(|range| range <= 6000.)
                        })
                    })
                {
                    0
                } else {
                    2
                },
            ));
        }
        if let Some(nav) = self
            .sector
            .navpoints
            .iter()
            .find(|nav| nav.id == self.target)
        {
            if let Some((verb, range)) = match nav.object_type {
                11 => Some((10, 2500.)), // native Gate -> action 18
                12 => Some((8, 5000.)),  // native Dock -> action 28
                _ => None,
            } {
                let attribute = if can_act && distance(self.position, nav.position) <= range {
                    0
                } else {
                    2
                };
                verbs.push((verb, attribute));
            }
        }
        if let Some(resource) = self
            .sector
            .resources
            .iter()
            .find(|node| node.object.id == self.target)
        {
            if self.account.character.profession == 2 && self.skill_catalog.level(41) > 0 {
                let available = lock_resources(&self.resource_field)
                    .is_ok_and(|field| field.get(self.target).is_ok_and(|node| node.available()));
                let attribute = if can_act
                    && available
                    && self.skills.cloak_rank == 0
                    && resource.object.range_to(self.position) <= self.prospect_range()
                {
                    0
                } else {
                    2
                };
                verbs.push((9, attribute));
            }
        }
        let frame = packets::target_verbs(self.target, &verbs);
        if force || self.last_verbs.as_ref() != Some(&frame) {
            self.pending.push(frame.clone());
            self.last_verbs = Some(frame);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_player_target_advertises_group_and_updates_follow_range_and_incapacity() {
        let mut ship = super::super::tests::session();
        ship.logged_in = true;
        ship.position = [0.; 3];
        let mut peer = ship.peer_snapshot().unwrap();
        peer.profile.game_id += 1;
        peer.position = [2000., 0., 0.];
        ship.target = peer.profile.game_id;
        ship.update_peer_view(&[peer.clone()], &[]);
        ship.refresh_target_verbs(true);
        assert_eq!(
            ship.last_verbs,
            Some(packets::target_verbs(ship.target, &[(4, 0), (13, 2)]))
        );
        peer.position[0] = 1745.; // Native player radius245 + follow range1500.
        ship.update_peer_view(&[peer.clone()], &[]);
        ship.refresh_target_verbs(false);
        assert_eq!(
            ship.last_verbs,
            Some(packets::target_verbs(ship.target, &[(4, 0), (13, 0)]))
        );
        peer.hull = 0.;
        ship.update_peer_view(&[peer], &[]);
        ship.refresh_target_verbs(false);
        assert_eq!(
            ship.last_verbs,
            Some(packets::target_verbs(
                ship.target,
                &[(4, 2), (13, 2), (12, 0)]
            ))
        );
    }

    #[test]
    fn hidden_or_departed_peer_does_not_retain_native_group_actions() {
        let mut ship = super::super::tests::session();
        ship.logged_in = true;
        let mut peer = ship.peer_snapshot().unwrap();
        peer.profile.game_id += 1;
        ship.target = peer.profile.game_id;
        ship.update_peer_view(&[peer.clone()], &[]);
        ship.refresh_target_verbs(true);
        assert!(!ship.peer_contacts.is_empty());
        peer.cloaked = true;
        ship.update_peer_view(&[peer], &[]);
        ship.refresh_target_verbs(false);
        assert!(ship.peer_contacts.is_empty());
        assert_eq!(
            ship.last_verbs,
            Some(packets::target_verbs(ship.target, &[]))
        );
        ship.target = ship.player_id();
        ship.refresh_target_verbs(true);
        assert_eq!(
            ship.last_verbs,
            Some(packets::target_verbs(ship.target, &[]))
        );
    }
}
