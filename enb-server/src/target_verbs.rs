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
        if let Some(position) = self.visible_corpses.get(&self.target) {
            verbs.push((
                3,
                if can_act && distance(self.position, *position) <= 6000. {
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
                    && distance(self.position, resource.object.position) <= self.prospect_range()
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
