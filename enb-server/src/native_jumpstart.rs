//! Native target verb12 / Action26 uses the existing catalog cast lifecycle.
//! Jumpstart can target any visible incapacitated player, including oneself;
//! group membership remains a requirement of the other group support skills.
use super::*;
use crate::skills::{AbilityDefinition, AbilityKind};

impl PlayerSession {
    pub(super) fn jumpstart_definition(&self) -> io::Result<AbilityDefinition> {
        let trained = self.skill_catalog.level(31);
        if self.account.character.profession != 2 || trained == 0 {
            return Err(invalid("Jumpstart requires a trained explorer skill."));
        }
        let ability = self.skill_catalog.ability(65)?;
        if ability.kind != AbilityKind::Jumpstart || ability.skill != 31 || ability.rank > trained {
            return Err(invalid(
                "This character does not have a trained Jumpstart ability.",
            ));
        }
        Ok(ability)
    }

    pub(super) fn begin_jumpstart(&mut self, target: u32) -> io::Result<()> {
        // Action26 requires an actual player object. The untargeted ability
        // hotkey can still request self recovery through begin_ability.
        if target == u32::MAX || target == 0 {
            return Err(invalid(
                "Select a player before using the Jumpstart action.",
            ));
        }
        let ability = self.jumpstart_definition()?;
        self.begin_ability_for(ability.id, target)
    }

    pub(super) fn jumpstart_target(
        &self,
        ability: &AbilityDefinition,
        target: u32,
    ) -> io::Result<u32> {
        if target == self.player_id() || target == u32::MAX {
            return if self.hull <= 0. {
                Ok(self.player_id())
            } else {
                Err(invalid("This ship does not need a jumpstart."))
            };
        }
        let contact = self
            .peer_contacts
            .get(&target)
            .ok_or_else(|| invalid("Select a visible incapacitated player."))?;
        let range =
            (distance(self.position, contact.position) - crate::following::PLAYER_RADIUS).max(0.);
        if !contact.incapacitated || range > ability.range as f32 {
            return Err(invalid(
                "The player is out of range or does not need a jumpstart.",
            ));
        }
        Ok(target)
    }

    /// The coordinator calls this against a fresh authoritative target snapshot
    /// immediately before delivery, after the cast has finished and been taken.
    pub(crate) fn can_deliver_jumpstart(&self, peer: &crate::peers::PeerSnapshot) -> bool {
        self.logged_in
            && self.hull > 0.
            && self.sector.station.is_none()
            && !self.warp.active()
            && self.gate_transit.is_none()
            && peer.sector == self.sector.id
            && peer.hull <= 0.
            && self.jumpstart_definition().is_ok_and(|ability| {
                self.peer_contacts.contains_key(&peer.profile.game_id)
                    && (distance(self.position, peer.position) - crate::following::PLAYER_RADIUS)
                        .max(0.)
                        <= ability.range as f32
            })
    }
}

#[cfg(test)]
#[path = "native_jumpstart_tests.rs"]
mod tests;
