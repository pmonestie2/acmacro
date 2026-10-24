//! Frozen launched rounds. Targets use public spawn identity plus life
//! generation, so a late projectile cannot damage a replacement or respawn.
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Projectile {
    pub owner: u32,
    pub sector: u32,
    pub slot: usize,
    pub emitted_ms: u64,
    pub duration_ms: u16,
    pub effect: u16,
    pub effect_id: u32,
    #[serde(default)]
    pub visual_sent: bool,
    pub sequence: u64,
    pub target: u32,
    pub template: u32,
    pub generation: u64,
    pub due_ms: u64,
    pub damage: f32,
    pub damage_type: usize,
    /// Frozen native damage outcome: 0 normal/miss, 3 critical. Old journals
    /// predate random outcomes and therefore deserialize as normal hits.
    #[serde(default)]
    pub inflicted: u32,
}

impl Projectile {
    pub fn valid(&self) -> bool {
        (0x40000001..=0x4000ffff).contains(&self.owner)
            && (1..10000).contains(&self.sector)
            && (3..=8).contains(&self.slot)
            && self.emitted_ms > 0
            && self.emitted_ms <= self.due_ms
            && self.sequence > 0
            && self.target > 0
            && self.due_ms > 0
            && self.damage.is_finite()
            && self.damage >= 0.
            && self.damage <= 1e12
            && self.damage_type < 9
            && matches!(self.inflicted, 0 | 3)
            && (self.damage > 0. || self.inflicted == 0)
    }
}

#[derive(Clone, Debug)]
pub struct ProjectileVisual {
    pub owner: u32,
    pub target: u32,
    pub effect: u16,
    pub effect_id: u32,
    pub duration_ms: u16,
}

#[derive(Clone, Debug)]
pub struct DamageReport {
    pub owner: u32,
    pub target: u32,
    pub damage: f32,
    pub damage_type: usize,
    pub inflicted: u32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LaunchJournal {
    pub owner: u32,
    pub sector: u32,
    pub sequence: u64,
    pub energy_after: f32,
    pub created_ms: u64,
    pub reload_seconds: [f32; 20],
    pub reloads: [u64; 20],
    pub next_effect: u32,
    pub projectiles: Vec<Projectile>,
}

impl LaunchJournal {
    pub fn valid(&self) -> bool {
        let mut previous = 0;
        self.created_ms > 0
            && self
                .reload_seconds
                .iter()
                .all(|seconds| seconds.is_finite() && *seconds >= 0. && *seconds <= 3600.)
            && self.energy_after.is_finite()
            && self.energy_after >= 0.
            && self.energy_after <= 1e12
            && !self.projectiles.is_empty()
            && self.projectiles.len() <= 384
            && self
                .projectiles
                .last()
                .is_some_and(|shot| shot.sequence == self.sequence)
            && self.projectiles.iter().all(|shot| {
                let ordered = shot.sequence > previous;
                previous = shot.sequence;
                shot.valid()
                    && ordered
                    && shot.owner == self.owner
                    && shot.sector == self.sector
                    && self.reloads[shot.slot] >= self.created_ms
                    && self.reload_seconds[shot.slot] > 0.
                    && shot.emitted_ms >= self.created_ms
            })
    }
}

/// C++ Equipable::ShootAmmo: milliseconds per metre, PL=1.5 and ML=2.
pub fn travel_ms(item_type: u32, range: f32) -> u16 {
    let factor = match item_type {
        16 => 1.5,
        15 => 2.,
        _ => 0.,
    };
    (range.max(0.) * factor).min(u16::MAX as f32) as u16
}

/// Retain the archived integer-seconds cast before its 400ms burst ceiling.
pub fn burst_interval_ms(reload: f32, rounds: u32) -> u64 {
    ((reload / rounds.max(1) as f32 / 2.).trunc() * 1000.).clamp(0., 400.) as u64
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_projectile_travel_and_burst_spacing_follow_weapon_type() {
        assert_eq!(travel_ms(14, 1000.), 0);
        assert_eq!(travel_ms(16, 1000.), 1500);
        assert_eq!(travel_ms(15, 1000.), 2000);
        assert_eq!(travel_ms(15, 100_000.), u16::MAX);
        assert_eq!(burst_interval_ms(1.5, 2), 0);
        assert_eq!(burst_interval_ms(8., 2), 400);
    }
}
