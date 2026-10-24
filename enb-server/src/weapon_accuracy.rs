//! Deterministic portions of PlayerCombat::CalcDamage and Equipable::CheckOrientation.
//! Callers own the random stream, launch journal, ammunition, and energy costs.
use crate::protocol::invalid;
use std::io;

pub const HIT_ROLL_MAX: u32 = 10_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct HitChances {
    miss: u32,
    critical: u32,
}

impl HitChances {
    /// Thresholds in basis points: 500 means the legacy nominal five percent.
    pub fn new(miss: u32, critical: u32) -> io::Result<Self> {
        if miss > HIT_ROLL_MAX || critical > HIT_ROLL_MAX {
            return Err(invalid("Weapon hit chance exceeds 100 percent."));
        }
        Ok(Self { miss, critical })
    }

    /// Buff values are percentage points, already combined across stat operations.
    /// The native short conversion truncates the bonus before applying baseline5%.
    /// Clamp instead of reproducing signed-short overflow or the legacy >100%
    /// critical assignment typo, which reset the chance to zero.
    pub fn from_bonuses(accuracy: f32, critical: f32) -> io::Result<Self> {
        if !accuracy.is_finite() || !critical.is_finite() {
            return Err(invalid("Non-finite weapon accuracy bonus."));
        }
        let threshold = |bonus: f32, sign: f64| {
            (500. + sign * (f64::from(bonus) * 100.).trunc()).clamp(0., f64::from(HIT_ROLL_MAX))
                as u32
        };
        Self::new(threshold(accuracy, -1.), threshold(critical, 1.))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HitOutcome {
    Miss,
    Hit,
    Critical,
}

impl HitOutcome {
    /// Native DamageMOB callers encode critical as3 and both normal/missed as0.
    pub fn inflicted(self) -> u32 {
        if self == Self::Critical {
            3
        } else {
            0
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct WeaponRoll {
    pub damage: f32,
    pub outcome: HitOutcome,
}

/// Exactly one inclusive1..10000 roll decides a shot. Miss wins over critical.
/// Preserve the archived inclusive critical threshold: nominal5% occupies501
/// outcomes, and zero critical chance still includes roll10000. Equipment/skill
/// damage bonuses and combat cloak must already be included in `damage` once.
pub fn roll_weapon_damage(damage: f32, roll: u32, chances: HitChances) -> io::Result<WeaponRoll> {
    if !damage.is_finite() || damage < 0. || !(1..=HIT_ROLL_MAX).contains(&roll) {
        return Err(invalid("Invalid weapon damage or deterministic hit roll."));
    }
    let (outcome, multiplier) = if roll <= chances.miss {
        (HitOutcome::Miss, 0.)
    } else if roll >= HIT_ROLL_MAX - chances.critical {
        (HitOutcome::Critical, 2.)
    } else {
        (HitOutcome::Hit, 1.)
    };
    let damage = damage * multiplier;
    if !damage.is_finite() {
        return Err(invalid("Weapon critical damage overflow."));
    }
    Ok(WeaponRoll { damage, outcome })
}

/// Native item types14=beam,16=projectile launcher require a strict40-degree
/// three-dimensional cone; type15=missile launcher can fire in any direction.
/// Coincident targets are explicitly in range of the cone instead of relying on
/// the archive's division by zero. Other item types are rejected.
pub fn weapon_in_firing_arc(
    item_type: u32,
    heading: [f32; 3],
    source: [f32; 3],
    target: [f32; 3],
) -> io::Result<bool> {
    if !matches!(item_type, 14..=16)
        || heading
            .iter()
            .chain(source.iter())
            .chain(target.iter())
            .any(|v| !v.is_finite())
    {
        return Err(invalid("Invalid weapon type or firing arc vector."));
    }
    if item_type == 15 {
        return Ok(true);
    }
    let heading = heading.map(f64::from);
    let delta =
        std::array::from_fn::<_, 3, _>(|axis| f64::from(target[axis]) - f64::from(source[axis]));
    let norm = |vector: [f64; 3]| vector.iter().map(|v| v * v).sum::<f64>().sqrt();
    let heading_length = norm(heading);
    if heading_length == 0. {
        return Err(invalid("Directional weapon has no heading."));
    }
    let distance = norm(delta);
    if distance == 0. {
        return Ok(true);
    }
    let dot = (0..3).map(|axis| heading[axis] * delta[axis]).sum::<f64>();
    Ok(dot / (heading_length * distance) > 40_f64.to_radians().cos())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_roll_preserves_native_miss_and_inclusive_critical_boundaries() {
        let chance = HitChances::new(500, 500).unwrap();
        let mut counts = [0; 3];
        for roll in 1..=HIT_ROLL_MAX {
            let result = roll_weapon_damage(17., roll, chance).unwrap();
            let (index, damage, inflicted) = match result.outcome {
                HitOutcome::Miss => (0, 0., 0),
                HitOutcome::Hit => (1, 17., 0),
                HitOutcome::Critical => (2, 34., 3),
            };
            counts[index] += 1;
            assert_eq!(result.damage, damage);
            assert_eq!(result.outcome.inflicted(), inflicted);
        }
        assert_eq!(counts, [500, 8999, 501]);
    }

    #[test]
    fn misses_precede_even_full_critical_chance() {
        let chance = HitChances::new(500, 10_000).unwrap();
        assert_eq!(
            roll_weapon_damage(1., 500, chance).unwrap().outcome,
            HitOutcome::Miss
        );
        assert_eq!(
            roll_weapon_damage(1., 501, chance).unwrap().outcome,
            HitOutcome::Critical
        );
        let zero = HitChances::new(0, 0).unwrap();
        assert_eq!(
            roll_weapon_damage(1., 9999, zero).unwrap().outcome,
            HitOutcome::Hit
        );
        assert_eq!(
            roll_weapon_damage(1., 10_000, zero).unwrap().outcome,
            HitOutcome::Critical
        );
    }

    #[test]
    fn buff_conversion_keeps_percentage_points_truncation_and_safe_caps() {
        assert_eq!(
            HitChances::from_bonuses(1.239, 70.).unwrap(),
            HitChances::new(377, 7500).unwrap()
        );
        assert_eq!(
            HitChances::from_bonuses(-1000., 1000.).unwrap(),
            HitChances::new(10_000, 10_000).unwrap()
        );
        assert!(HitChances::from_bonuses(f32::NAN, 0.).is_err());
        assert!(HitChances::new(10_001, 0).is_err());
    }

    #[test]
    fn directional_weapons_use_a_three_dimensional_forty_degree_cone() {
        for kind in [14, 16] {
            for (angle, expected) in [(0_f32, true), (39.9, true), (40.1, false), (180., false)] {
                let angle = angle.to_radians();
                // Elevation matters as much as yaw; heading length need not be1.
                assert_eq!(
                    weapon_in_firing_arc(
                        kind,
                        [2., 0., 0.],
                        [100., 0., 0.],
                        [100. + 1000. * angle.cos(), 0., 1000. * angle.sin()]
                    )
                    .unwrap(),
                    expected
                );
            }
            assert!(weapon_in_firing_arc(kind, [1., 0., 0.], [0.; 3], [0.; 3]).unwrap());
        }
        assert!(weapon_in_firing_arc(15, [1., 0., 0.], [0.; 3], [-100., 0., 0.]).unwrap());
    }

    #[test]
    fn malformed_vectors_and_rolls_cannot_reach_launch_arithmetic() {
        assert!(weapon_in_firing_arc(14, [0.; 3], [0.; 3], [1.; 3]).is_err());
        assert!(weapon_in_firing_arc(15, [1.; 3], [0.; 3], [f32::NAN; 3]).is_err());
        assert!(weapon_in_firing_arc(99, [1.; 3], [0.; 3], [1.; 3]).is_err());
        let chance = HitChances::new(0, 10_000).unwrap();
        assert!(roll_weapon_damage(1., 0, chance).is_err());
        assert!(roll_weapon_damage(1., 10_001, chance).is_err());
        assert!(roll_weapon_damage(f32::MAX, 1, chance).is_err());
    }
}
