//! Active PlayerConnection::Action26 -> AJumpStart::Execute/Update rules.
//! The unused CalculateChargeUpTime and the old 7% comment are not runtime rules.
use crate::protocol::invalid;
use crate::ship_progression::MAX_HULL_UPGRADE;
use serde::{Deserialize, Serialize};
use std::io;

/// Frozen at activation, after validation and before energy is charged. Neither
/// resuming a checkpoint nor changing the current selection recalculates it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JumpstartPlan {
    pub trained_level: u32,
    pub target_hull_upgrade: u32,
    pub energy: f32,
    pub range: f32,
    pub charge_seconds: f32,
    pub cooldown_seconds: f32,
    pub repair_fraction: f32,
}

impl JumpstartPlan {
    pub fn new(trained_level: u32, target_hull_upgrade: u32) -> io::Result<Self> {
        if !(1..=7).contains(&trained_level) || target_hull_upgrade > MAX_HULL_UPGRADE {
            return Err(invalid("Invalid Jumpstart skill or hull upgrade."));
        }
        if target_hull_upgrade > trained_level + 2 {
            return Err(invalid(
                "Jumpstart requires more skill for this hull upgrade.",
            ));
        }
        let multiplier = target_hull_upgrade.saturating_sub(trained_level).max(1);
        Ok(Self {
            trained_level,
            target_hull_upgrade,
            energy: (20 * target_hull_upgrade * multiplier) as f32,
            range: (1000 + 250 * trained_level) as f32,
            // Execute calls SetTimer(5000); CalculateChargeUpTime is unused.
            charge_seconds: 5.,
            cooldown_seconds: 0.,
            repair_fraction: ((10 * trained_level) as f32 / 100.).min(1.),
        })
    }

    pub fn valid(&self) -> bool {
        Self::new(self.trained_level, self.target_hull_upgrade)
            .is_ok_and(|expected| expected == *self)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_native_hull_upgrades_follow_skill_limit_energy_and_surcharge() {
        for hull in 0..=6 {
            for skill in 1..=7 {
                let plan = JumpstartPlan::new(skill, hull);
                if hull > skill + 2 {
                    assert!(plan.is_err());
                } else {
                    let plan = plan.unwrap();
                    let base = (20 * hull) as f32;
                    let expected = if hull > skill {
                        base * (hull - skill) as f32
                    } else {
                        base
                    };
                    assert_eq!(plan.energy, expected);
                    assert_eq!(plan.range, (1000 + skill * 250) as f32);
                    assert_eq!(plan.repair_fraction, skill as f32 / 10.);
                    assert_eq!(plan.charge_seconds, 5.);
                    assert_eq!(plan.cooldown_seconds, 0.);
                    assert!(plan.valid());
                }
            }
        }
        assert_eq!(JumpstartPlan::new(4, 6).unwrap().energy, 240.);
        assert_eq!(JumpstartPlan::new(5, 6).unwrap().energy, 120.);
        assert_eq!(JumpstartPlan::new(7, 6).unwrap().repair_fraction, 0.7);
    }

    #[test]
    fn invalid_and_tampered_frozen_plans_are_rejected() {
        for (skill, hull) in [(0, 0), (8, 0), (7, 7), (u32::MAX, u32::MAX)] {
            assert!(JumpstartPlan::new(skill, hull).is_err());
        }
        let mut plan = JumpstartPlan::new(7, 6).unwrap();
        plan.energy = f32::NAN;
        assert!(!plan.valid());
        let mut plan = JumpstartPlan::new(7, 6).unwrap();
        plan.repair_fraction = 1.;
        assert!(!plan.valid());
        let mut plan = JumpstartPlan::new(7, 6).unwrap();
        plan.charge_seconds = 7.;
        assert!(!plan.valid());
    }
}
