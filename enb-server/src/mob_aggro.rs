//! Four-slot native MOB hate table (MOBClass::{Add,Subtract,Remove}Hate).
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::io;

#[derive(Clone, Debug)]
pub struct PlayerThreat {
    pub id: u32,
    pub session_epoch: u64,
    pub position: [f32; 3],
    /// The coordinator excludes incapacitated and unavailable ships. Cloak is
    /// evaluated separately for each MOB's actual detection capability.
    pub visible: bool,
    pub signature: f32,
    pub stealth: Option<u32>,
    pub factions: std::sync::Arc<crate::factions::FactionReactions>,
}

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize)]
struct HateEntry {
    player: u32,
    hate: i32,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub(crate) struct MobAggro {
    // Keep slot order: native equal-hate ties choose the first occupied slot.
    entries: [HateEntry; 4],
    target: Option<u32>,
}

impl MobAggro {
    pub fn validate(&self) -> io::Result<()> {
        for (index, entry) in self.entries.iter().enumerate() {
            if entry.player == 0 && entry.hate != 0
                || entry.player != 0
                    && self.entries[..index]
                        .iter()
                        .any(|other| other.player == entry.player)
            {
                return Err(invalid("Invalid MOB hate checkpoint."));
            }
        }
        if self.target == Some(0) {
            return Err(invalid("Invalid MOB target checkpoint."));
        }
        Ok(())
    }

    pub fn add(&mut self, player: u32, damage: f32) {
        if player == 0 || !damage.is_finite() || damage < 0. {
            return;
        }
        let hate = damage as i32;
        // Match identity before reusing an empty slot, avoiding the archive's
        // duplicate-ID bug after an earlier slot has been vacated.
        let index = self
            .entries
            .iter()
            .position(|entry| entry.player == player)
            .or_else(|| self.entries.iter().position(|entry| entry.hate == 0));
        if let Some(index) = index {
            let entry = &mut self.entries[index];
            entry.player = player;
            entry.hate = entry.hate.saturating_add(hate);
        } else if let Some((index, lowest)) = self
            .entries
            .iter()
            .enumerate()
            .min_by_key(|(index, entry)| (entry.hate, *index))
        {
            if hate > lowest.hate {
                self.entries[index] = HateEntry { player, hate };
            }
        }
    }

    pub fn subtract(&mut self, player: u32, damage: f32) {
        if !damage.is_finite() || damage <= 0. {
            return;
        }
        if let Some(entry) = self.entries.iter_mut().find(|entry| entry.player == player) {
            // MOB::FireWeapon removes ten percent of dealt damage, truncated.
            entry.hate = entry.hate.saturating_sub((damage / 10.) as i32);
        }
    }

    pub fn retain(&mut self, mut eligible: impl FnMut(u32) -> bool) {
        for entry in &mut self.entries {
            if entry.player != 0 && !eligible(entry.player) {
                *entry = HateEntry::default();
            }
        }
        if self.target.is_some_and(|player| !eligible(player)) {
            self.target = None;
        }
    }

    pub fn most_hated(&self, mut eligible: impl FnMut(u32) -> bool) -> Option<u32> {
        let mut largest = 0;
        let mut target = None;
        for entry in self.entries {
            if entry.hate > largest && eligible(entry.player) {
                largest = entry.hate;
                target = Some(entry.player);
            }
        }
        target
    }

    pub fn target(&mut self, player: Option<u32>) {
        self.target = player;
    }
    pub fn targets(&self, player: u32) -> bool {
        self.target == Some(player)
    }

    pub fn remove(&mut self, player: u32) {
        self.retain(|id| id != player);
    }

    pub fn clear(&mut self) {
        *self = Self::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_four_slots_accumulate_truncate_and_replace_only_lower_hate() {
        let mut hate = MobAggro::default();
        for player in 1..=4 {
            hate.add(player, player as f32 * 10.9);
        }
        assert_eq!(hate.most_hated(|_| true), Some(4));
        hate.add(5, 10.);
        assert!(!hate.entries.iter().any(|entry| entry.player == 5));
        hate.add(5, 11.);
        assert_eq!(hate.entries[0].player, 5);
        hate.add(5, 100.9);
        assert_eq!(hate.entries[0].hate, 111);
        assert_eq!(hate.most_hated(|_| true), Some(5));
        hate.subtract(5, 1000.);
        assert_eq!(hate.most_hated(|_| true), Some(4));
        hate.validate().unwrap();
    }

    #[test]
    fn positive_hate_and_slot_order_control_ties_without_distance_flapping() {
        let mut hate = MobAggro::default();
        hate.add(5, 0.9);
        assert_eq!(hate.most_hated(|_| true), None);
        hate.add(2, 10.);
        hate.add(1, 10.);
        assert_eq!(hate.most_hated(|_| true), Some(2));
        hate.retain(|player| player != 2);
        assert_eq!(hate.most_hated(|_| true), Some(1));
        hate.add(1, 2.);
        assert_eq!(
            hate.entries
                .iter()
                .filter(|entry| entry.player == 1)
                .count(),
            1
        );
        assert_eq!(hate.entries[1].hate, 12);
        hate.subtract(1, 200.);
        assert_eq!(hate.most_hated(|_| true), None);
        hate.validate().unwrap();
    }

    #[test]
    fn persistence_retains_hate_and_rejects_duplicate_or_invalid_identity() {
        let mut hate = MobAggro::default();
        hate.add(3, 20.);
        hate.add(7, 30.);
        hate.target(Some(7));
        let restored: MobAggro =
            serde_json::from_slice(&serde_json::to_vec(&hate).unwrap()).unwrap();
        restored.validate().unwrap();
        assert_eq!(restored.most_hated(|_| true), Some(7));
        let mut corrupt = restored.clone();
        corrupt.entries[1].player = 3;
        assert!(corrupt.validate().is_err());
        corrupt = restored;
        corrupt.entries[2].hate = 1;
        assert!(corrupt.validate().is_err());
    }
}
