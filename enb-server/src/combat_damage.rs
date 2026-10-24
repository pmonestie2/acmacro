//! Active PlayerCombat.cpp damage ordering, separated from effects and I/O.
//! MOBClass::DamageMOB has its deflect block commented out in the archive; this
//! module therefore does not invent species resistances for public MOBs.
use crate::protocol::invalid;
use std::io;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum DamageType {
    Impact = 0,
    Explosive = 1,
    Plasma = 2,
    Energy = 3,
    Emp = 4,
    Chemical = 5,
    // Extra runtime deflect slots are retained as ordinary damage categories.
    // Imported native weapons currently use only the first six C++ values.
    Absolute = 6,
    System = 7,
    Psionic = 8,
}

impl DamageType {
    pub fn from_index(index: usize) -> io::Result<Self> {
        match index {
            0 => Ok(Self::Impact),
            1 => Ok(Self::Explosive),
            2 => Ok(Self::Plasma),
            3 => Ok(Self::Energy),
            4 => Ok(Self::Emp),
            5 => Ok(Self::Chemical),
            6 => Ok(Self::Absolute),
            7 => Ok(Self::System),
            8 => Ok(Self::Psionic),
            _ => Err(invalid("Unknown damage type.")),
        }
    }
    pub fn index(self) -> usize {
        self as usize
    }
}

#[derive(Clone, Copy, Debug)]
pub struct PlayerDefense {
    pub shield: f32,
    pub hull: f32,
    /// Stat display percentages, e.g. 25 means a 25% deflect.
    pub deflect_percent: f32,
    /// C++ STAT_DAMAGE_CONTROL is a fraction, and applies only to hull spill.
    /// The caller supplies zero for classes without the passive skill.
    pub damage_control: f32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DamageResult {
    pub shield: f32,
    pub hull: f32,
    pub shield_damage: f32,
    pub hull_damage: f32,
    /// Native floating damage text reports the post-deflect hit before the
    /// plasma/chemical branch bonuses, matching SendClientDamage's call site.
    pub reported_damage: f32,
    /// Native damage modifiers are negative for absorbed damage.
    pub reported_modifier: f32,
    pub hull_branch: bool,
}

pub fn player_damage(
    damage: f32,
    kind: DamageType,
    defense: PlayerDefense,
) -> io::Result<DamageResult> {
    if !damage.is_finite()
        || damage < 0.
        || !defense.shield.is_finite()
        || defense.shield < 0.
        || !defense.hull.is_finite()
        || defense.hull < 0.
        || !defense.deflect_percent.is_finite()
        || !defense.damage_control.is_finite()
    {
        return Err(invalid("Invalid damage or player defense values."));
    }
    // The active legacy switch caps positive native deflects at 50%, while
    // negative vulnerability values amplify damage. Unimplemented categories
    // fall through unchanged instead of borrowing a different damage formula.
    let reduced = if kind.index() < 6 {
        damage * (1. - defense.deflect_percent.min(50.) / 100.)
    } else {
        damage
    };
    let hull_branch = reduced > defense.shield;
    let (shield_damage, hull_damage) = if hull_branch {
        let mut hull = reduced - defense.shield;
        if kind == DamageType::Chemical {
            hull *= 1.2;
        }
        hull *= (1. - defense.damage_control).max(0.);
        if kind == DamageType::Emp {
            hull = 0.;
        }
        (defense.shield, hull.min(defense.hull))
    } else {
        // Preserve the archive's branch ordering: the bonus is applied only
        // after choosing the shield-only branch. Its excess cannot become a
        // second hull hit. A later balance change should be explicit.
        let shield = if kind == DamageType::Plasma {
            reduced * 1.2
        } else {
            reduced
        };
        (shield.min(defense.shield), 0.)
    };
    let result = DamageResult {
        shield: defense.shield - shield_damage,
        hull: defense.hull - hull_damage,
        shield_damage,
        hull_damage,
        reported_damage: reduced,
        reported_modifier: reduced - damage,
        hull_branch,
    };
    if [
        result.shield,
        result.hull,
        result.shield_damage,
        result.hull_damage,
        result.reported_damage,
        result.reported_modifier,
    ]
    .iter()
    .any(|value| !value.is_finite())
    {
        return Err(invalid("Damage arithmetic overflow."));
    }
    Ok(result)
}

/// PlayerSkills.cpp, rank zero has no passive; TE/PW callers apply it to hull.
pub fn damage_control_fraction(rank: u32) -> f32 {
    if rank == 0 {
        0.
    } else {
        (0.15 + rank as f32 * 0.05).min(1.)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn defense(shield: f32) -> PlayerDefense {
        PlayerDefense {
            shield,
            hull: 100.,
            deflect_percent: 0.,
            damage_control: 0.,
        }
    }
    #[test]
    fn chemical_bonuses_only_hull_and_emp_never_damages_hull() {
        let chemical = player_damage(70., DamageType::Chemical, defense(50.)).unwrap();
        assert_eq!(chemical.shield, 0.);
        assert_eq!(chemical.hull, 76.);
        let ordinary = player_damage(40., DamageType::Chemical, defense(50.)).unwrap();
        assert_eq!(ordinary.shield, 10.);
        assert_eq!(ordinary.hull, 100.);
        let emp = player_damage(70., DamageType::Emp, defense(50.)).unwrap();
        assert_eq!(emp.shield, 0.);
        assert_eq!(emp.hull, 100.);
        assert!(emp.hull_branch);
        let emp_bare = player_damage(70., DamageType::Emp, defense(0.)).unwrap();
        assert_eq!(emp_bare.hull_damage, 0.);
    }
    #[test]
    fn plasma_retains_the_archived_pre_bonus_overflow_branch_boundary() {
        let shield_hit = player_damage(40., DamageType::Plasma, defense(100.)).unwrap();
        assert_eq!(shield_hit.shield, 52.);
        assert_eq!(shield_hit.hull, 100.);
        let clipped_bonus = player_damage(95., DamageType::Plasma, defense(100.)).unwrap();
        assert_eq!(clipped_bonus.shield, 0.);
        assert_eq!(clipped_bonus.hull, 100.);
        let overflow = player_damage(101., DamageType::Plasma, defense(100.)).unwrap();
        assert_eq!(overflow.hull, 99.);
        assert_eq!(overflow.reported_damage, 101.);
    }
    #[test]
    fn deflects_cap_at_fifty_percent_and_damage_control_reduces_only_hull_spill() {
        let mut guarded = defense(50.);
        guarded.deflect_percent = 90.;
        guarded.damage_control = damage_control_fraction(7);
        let hit = player_damage(200., DamageType::Impact, guarded).unwrap();
        assert_eq!(hit.shield, 0.);
        assert_eq!(hit.hull, 75.);
        assert_eq!(hit.reported_damage, 100.);
        assert_eq!(hit.reported_modifier, -100.);
        guarded.deflect_percent = -25.;
        guarded.damage_control = 0.;
        assert_eq!(
            player_damage(40., DamageType::Energy, guarded)
                .unwrap()
                .shield,
            0.
        );
        assert_eq!(damage_control_fraction(0), 0.);
        assert_eq!(damage_control_fraction(5), 0.4);
    }
    #[test]
    fn invalid_inputs_cannot_produce_nan_health_or_arbitrary_type_indices() {
        assert!(DamageType::from_index(9).is_err());
        assert!(player_damage(f32::NAN, DamageType::Energy, defense(1.)).is_err());
        assert!(player_damage(-1., DamageType::Energy, defense(1.)).is_err());
        let mut invalid = defense(1.);
        invalid.hull = f32::NAN;
        assert!(player_damage(1., DamageType::Energy, invalid).is_err());
    }
}
