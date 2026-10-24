//! Native class hulls and upgrade stages from StaticData.h, Player::ShipUpgrade,
//! ResetWeaponMounts/ResetDeviceMounts and Player::SetHullUpgrade.
//! Values here are base ship data; equipment modifiers belong in EquipmentStats.
use crate::classes::CharacterClass;
use crate::protocol::invalid;
use std::io;

pub const MAX_HULL_UPGRADE: u32 = 6;
pub const SANDBOX_HULL_UPGRADE: u32 = MAX_HULL_UPGRADE;

// Native order: Terran W/T/E, Jenquai W/T/E, Progen W/T/E.
const HULL: [[u32; 7]; 9] = [
    [18, 70, 280, 1100, 4500, 18000, 72000],
    [13, 50, 210, 850, 3400, 13400, 56000],
    [12, 48, 210, 850, 3300, 13200, 55500],
    [16, 65, 260, 1000, 4100, 16500, 66300],
    [12, 50, 200, 800, 3200, 13000, 55000],
    [11, 45, 170, 680, 2700, 11000, 42000],
    [20, 80, 320, 1300, 5100, 20500, 82000],
    [14, 65, 260, 1000, 4000, 17000, 66300],
    [13, 50, 210, 850, 3500, 13700, 55000],
];
const WEAPON_ADDITIONS: [[usize; 7]; 9] = [
    [2, 0, 1, 0, 1, 0, 1],
    [1, 0, 1, 0, 1, 0, 1],
    [1, 0, 1, 0, 1, 0, 1],
    [2, 0, 1, 0, 1, 0, 1],
    [1, 0, 1, 0, 1, 0, 1],
    [1, 0, 1, 0, 0, 1, 0],
    [2, 1, 0, 1, 1, 0, 1],
    // Archived WeaponTable totals4 for PP, although MaxWeaponSlots says5.
    [2, 0, 1, 0, 0, 1, 0],
    [2, 0, 1, 0, 0, 1, 0],
];
const DEVICE_ADDITIONS: [[usize; 7]; 9] = [
    [1, 1, 0, 1, 0, 1, 0],
    [2, 1, 0, 1, 0, 1, 0],
    [2, 1, 0, 1, 0, 1, 0],
    [1, 1, 0, 1, 0, 1, 0],
    [2, 1, 0, 1, 0, 1, 0],
    [2, 1, 0, 1, 1, 0, 1],
    [1, 0, 1, 0, 0, 1, 0],
    [1, 1, 0, 1, 0, 1, 0],
    [1, 1, 0, 1, 1, 0, 1],
];
const BASE_CARGO: [usize; 9] = [23, 28, 25, 18, 22, 20, 16, 20, 18];
const MINIMUM_LEVEL: [u32; 7] = [0, 10, 30, 50, 75, 100, 135];

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ShipProgression {
    pub upgrade: u32,
    pub minimum_total_level: u32,
    pub hull_capacity: f32,
    pub weapon_mounts: usize,
    pub device_mounts: usize,
    pub cargo_capacity: usize,
    /// Add to the four default class hull/profession/wing/engine asset IDs.
    pub appearance_tier: u16,
    pub warp_power_level: u32,
}

pub fn progression(class: CharacterClass, upgrade: u32) -> io::Result<ShipProgression> {
    let hull = HULL
        .get(class.0)
        .ok_or_else(|| invalid("Invalid ship class index."))?;
    let stage = stage_index(upgrade)?;
    Ok(ShipProgression {
        upgrade,
        minimum_total_level: MINIMUM_LEVEL[stage],
        hull_capacity: hull[stage] as f32,
        weapon_mounts: WEAPON_ADDITIONS[class.0][..=stage].iter().sum(),
        device_mounts: DEVICE_ADDITIONS[class.0][..=stage].iter().sum(),
        cargo_capacity: BASE_CARGO[class.0] + 2 * stage,
        appearance_tier: match upgrade {
            0..=2 => 0,
            3..=4 => 1,
            _ => 2,
        },
        warp_power_level: 3 + upgrade / 2,
    })
}

/// Apply passive/equipped STAT_HULL modifiers once to this returned base.
pub fn hull_capacity(class: CharacterClass, upgrade: u32) -> io::Result<f32> {
    Ok(progression(class, upgrade)?.hull_capacity)
}

/// Upgrade requests must advance exactly one stage and meet total-level gates.
/// This checks progression only; NPC mission/payment conditions are separate.
pub fn can_upgrade(current: u32, requested: u32, total_level: u32) -> io::Result<bool> {
    stage_index(current)?;
    let requested_index = stage_index(requested)?;
    Ok(requested == current + 1 && total_level >= MINIMUM_LEVEL[requested_index])
}

fn stage_index(upgrade: u32) -> io::Result<usize> {
    if upgrade > MAX_HULL_UPGRADE {
        return Err(invalid("Hull upgrade must be between 0 and 6."));
    }
    Ok(upgrade as usize)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn all_class_hulls_use_native_upgrade_columns_without_modifiers() {
        let cases = [
            ("te", 18., 72000.),
            ("tt", 13., 56000.),
            ("ts", 12., 55500.),
            ("jd", 16., 66300.),
            ("js", 12., 55000.),
            ("je", 11., 42000.),
            ("pw", 20., 82000.),
            ("pp", 14., 66300.),
            ("ps", 13., 55000.),
        ];
        for (code, initial, maximum) in cases {
            let class = CharacterClass::parse(code).unwrap();
            assert_eq!(hull_capacity(class, 0).unwrap(), initial);
            assert_eq!(hull_capacity(class, SANDBOX_HULL_UPGRADE).unwrap(), maximum);
            let mut previous = 0.;
            for stage in 0..=MAX_HULL_UPGRADE {
                let hull = hull_capacity(class, stage).unwrap();
                assert!(hull > previous);
                previous = hull;
            }
        }
        let je = CharacterClass::parse("je").unwrap();
        assert_eq!(
            (0..=6)
                .map(|stage| hull_capacity(je, stage).unwrap())
                .collect::<Vec<_>>(),
            [11., 45., 170., 680., 2700., 11000., 42000.]
        );
    }

    #[test]
    fn upgrade_gates_are_sequential_at_native_total_levels() {
        for (stage, level) in [(1, 10), (2, 30), (3, 50), (4, 75), (5, 100), (6, 135)] {
            assert!(!can_upgrade(stage - 1, stage, level - 1).unwrap());
            assert!(can_upgrade(stage - 1, stage, level).unwrap());
            assert!(!can_upgrade(stage, stage, 150).unwrap());
        }
        assert!(!can_upgrade(0, 6, 150).unwrap());
        assert!(!can_upgrade(6, 0, 150).unwrap());
    }

    #[test]
    fn mount_deltas_cargo_and_appearance_preserve_source_discrepancy() {
        for code in ["te", "tt", "ts", "jd", "js", "je", "pw", "pp", "ps"] {
            let class = CharacterClass::parse(code).unwrap();
            let maximum = progression(class, 6).unwrap();
            assert_eq!(
                maximum.weapon_mounts,
                if code == "pp" { 4 } else { class.weapons() }
            );
            assert_eq!(maximum.device_mounts, class.devices());
            for stage in 0..=6 {
                let ship = progression(class, stage).unwrap();
                assert_eq!(
                    ship.cargo_capacity,
                    BASE_CARGO[class.0] + 2 * stage as usize
                );
                assert!(ship.cargo_capacity <= 40);
                assert_eq!(
                    ship.appearance_tier,
                    if stage < 3 {
                        0
                    } else if stage < 5 {
                        1
                    } else {
                        2
                    }
                );
                assert_eq!(ship.warp_power_level, 3 + stage / 2);
            }
        }
        let pw = CharacterClass::parse("pw").unwrap();
        assert_eq!(
            (0..=6)
                .map(|stage| progression(pw, stage).unwrap().weapon_mounts)
                .collect::<Vec<_>>(),
            [2, 3, 3, 4, 5, 5, 6]
        );
    }

    #[test]
    fn invalid_public_tuple_indices_and_stages_return_errors_instead_of_panicking() {
        assert!(progression(CharacterClass(9), 0).is_err());
        assert!(hull_capacity(CharacterClass(usize::MAX), 6).is_err());
        assert!(progression(CharacterClass(0), 7).is_err());
        assert!(can_upgrade(u32::MAX, 0, 150).is_err());
        assert!(can_upgrade(6, u32::MAX, 150).is_err());
    }
}
