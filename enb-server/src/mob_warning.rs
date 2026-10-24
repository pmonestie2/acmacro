//! MOBClass::CheckVisibility/CheckWarningShots: close, already-observed ships
//! can provoke numeric aggression tiers 1..4 without first firing a weapon.
//! Keep a separate saved stream so adding this behavior never rerolls damage.
use super::*;
use crate::world_random::WorldRandom;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub(super) struct WarningState {
    elapsed: f32,
    random_state: u64,
    /// Membership at the previous native 500ms visibility pass. Merely
    /// entering sensor range does not also run CheckWarningShots that pass.
    contacts: BTreeMap<u32, u64>,
}

impl WarningState {
    pub fn validate(&self) -> std::io::Result<()> {
        if !self.elapsed.is_finite()
            || !(0. ..0.5).contains(&self.elapsed)
            || self.contacts.len() > 65535
            || self
                .contacts
                .iter()
                .any(|(id, epoch)| *id == 0 || *epoch == 0)
        {
            return Err(crate::protocol::invalid("Invalid MOB warning checkpoint."));
        }
        Ok(())
    }

    pub fn clear_contacts(&mut self) {
        self.contacts.clear();
    }

    pub fn forget_player(&mut self, player: u32) {
        self.contacts.remove(&player);
    }

    fn passes(&mut self, dt: f32) -> u32 {
        // Match the existing active-AI hibernation guard, without offline rolls.
        if !dt.is_finite() || dt <= 0. || dt >= 5. {
            return 0;
        }
        self.elapsed += dt;
        let passes = ((self.elapsed + 0.000001) / 0.5).floor() as u32;
        self.elapsed = (self.elapsed - passes as f32 * 0.5).max(0.);
        passes
    }
}

/// Native rand()%100 uses strict comparisons, not rounded percentages.
fn warning_hate(aggression: u32, roll: u32) -> f32 {
    if roll >= 100 {
        return 0.;
    }
    match aggression {
        1 if roll > 97 => 1.,
        2 if roll > 95 => 5.,
        3 if roll > 93 => 20.,
        4 if roll > 90 => 100.,
        _ => 0.,
    }
}

impl Mob {
    pub(super) fn check_warning_shots(&mut self, dt: f32, players: &[PlayerThreat]) {
        let Some(aggression @ 1..=4) = self.definition.aggression else {
            return;
        };
        let passes = self.warnings.passes(dt);
        if passes == 0 {
            return;
        }
        let mut contacts: Vec<&PlayerThreat> = players
            .iter()
            .filter(|player| self.sensors.contains(player))
            .collect();
        // Game sessions originate in a HashMap. Stable ordering preserves the
        // saved stream across process replacement and recipient enumeration.
        contacts.sort_by_key(|player| (player.id, player.session_epoch));
        if self.warnings.random_state == 0 {
            self.warnings.random_state = u64::from(self.definition.source_id)
                ^ (u64::from(self.definition.template_id) << 32)
                ^ 0x7761_726e_696e_6701;
        }
        for _ in 0..passes {
            for player in &contacts {
                if self.warnings.contacts.get(&player.id) != Some(&player.session_epoch)
                    || self.range_to(player.position) >= 1000.
                    || !player
                        .factions
                        .allows_spontaneous_attack(self.definition.faction)
                {
                    continue;
                }
                // Native visibility samples cloak independently: warning hate
                // is subsequently removed by select_target if undetectable.
                let mut random = WorldRandom(self.warnings.random_state);
                let hate = warning_hate(aggression, random.below(100));
                self.warnings.random_state = random.0;
                if hate > 0. {
                    self.aggro.add(player.id, hate);
                }
            }
            self.warnings.contacts = contacts
                .iter()
                .map(|player| (player.id, player.session_epoch))
                .collect();
        }
    }
}

#[cfg(test)]
#[path = "mob_warning_tests.rs"]
mod tests;
