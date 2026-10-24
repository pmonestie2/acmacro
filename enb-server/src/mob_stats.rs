//! MOBClass::SetLevel interpolates at combat level * 3, in ten-level steps.
//! These are the archived MobDPS/MobDAC tables, including the top CL66 segment.
use crate::mob::MobDefinition;
use serde::{Deserialize, Serialize};

const DPS: [f32; 21] = [
    1.1, 3.2, 7.2, 14.7, 24., 41., 74., 131., 198., 310., 448., 648., 915., 1436., 2080., 2912.,
    3965., 5017., 6347., 8028., 14933.,
];
const CAPACITY: [f32; 21] = [
    29., 57., 160., 377., 864., 1365., 2845., 5203., 10146., 14171., 27030., 36864., 62797.,
    87172., 164296., 220191., 350057., 466263., 619402., 820852., 1085421.,
];

#[derive(Clone, Copy, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct MobStats {
    pub hull: f32,
    pub shield: f32,
    pub dps: f32,
    pub reload: f32,
    pub range: f32,
    pub effect: u16,
    pub damage_type: usize,
    #[serde(default)]
    pub variance: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MobWeapon {
    pub item: u32,
    pub item_type: u32,
    pub damage: f32,
    pub damage_type: usize,
    pub reload: f32,
    pub effect: u16,
}

fn interpolate(table: &[f32; 21], level: u32) -> f32 {
    let overall = level.min(66) * 3;
    let index = (overall / 10) as usize;
    let position = overall as f32 / 10.;
    table[index] + (table[index + 1] - table[index]) * (position - index as f32)
}

impl MobStats {
    pub fn derive(definition: &MobDefinition) -> Self {
        if let Some(stats) = definition.combat_override {
            return stats;
        }
        let capacity = interpolate(&CAPACITY, definition.level);
        let hull = if matches!(definition.mob_type, 0 | 1) {
            capacity * 0.3
        } else {
            0.
        };
        let mut stats = Self {
            hull,
            shield: (capacity - hull * 0.9) * definition.shield_modifier,
            dps: interpolate(&DPS, definition.level),
            reload: 4.,
            range: 1500. * definition.range_modifier,
            effect: match definition.mob_type {
                1 => 33,
                0 => 34,
                5 => 35,
                6 => 38,
                3 => 41,
                _ => 43,
            },
            damage_type: 1,
            variance: true,
        };
        // The C++ selects the first usable weapon in the MOB's item list.
        // Beam damage/reload replace table DPS; launchers retain the table DPS
        // with a two-second reload until full MOB ammunition AI is implemented.
        if let Some(weapon) = &definition.weapon {
            stats.effect = weapon.effect;
            stats.damage_type = weapon.damage_type;
            if weapon.item_type == 14 {
                stats.reload = weapon.reload;
                stats.dps = weapon.damage / weapon.reload;
            } else {
                stats.reload = 2.;
            }
        }
        stats.dps *= definition.damage_modifier;
        stats
    }
    pub fn valid(self) -> bool {
        [self.hull, self.shield, self.dps, self.reload, self.range]
            .iter()
            .all(|n| n.is_finite())
            && self.hull >= 0.
            && self.shield > 0.
            && self.dps > 0.
            && self.reload >= 0.1
            && self.reload <= 300.
            && self.range > 0.
            && self.range <= 200_000.
            && self.damage_type < 9
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn definition(level: u32, kind: i8) -> MobDefinition {
        let mut definition:MobDefinition=serde_json::from_str(r#"{"source_id":1,"name":"Test","level":1,"asset":48,"spawn":[0,0,0],"respawn_seconds":20,"aggressive":true}"#).unwrap();
        definition.level = level;
        definition.mob_type = kind;
        definition
    }
    #[test]
    fn archived_curves_interpolate_endpoints_and_split_only_mechanical_hulls() {
        let zero = MobStats::derive(&definition(0, 1));
        assert!((zero.hull - 8.7).abs() < 0.001);
        assert!((zero.shield - 21.17).abs() < 0.001);
        let mid = MobStats::derive(&definition(50, 1));
        assert!((mid.hull - 66057.3).abs() < 0.1);
        assert_eq!(mid.dps, 2912.);
        let top = MobStats::derive(&definition(66, 5));
        assert_eq!(top.hull, 0.);
        assert!((top.shield - 1_032_507.2).abs() < 1.);
        assert!((top.dps - 13_552.).abs() < 0.1);
        assert!(top.valid());
        assert_eq!(MobStats::derive(&definition(100, 5)), top);
    }
    #[test]
    fn sql_boss_modifiers_and_equipped_beam_stats_affect_actual_combat_parameters() {
        let mut mob = definition(66, 5);
        mob.shield_modifier = 10.;
        mob.damage_modifier = 2.;
        mob.range_modifier = 3.;
        mob.weapon = Some(MobWeapon {
            item: 1,
            item_type: 14,
            damage: 100.,
            damage_type: 7,
            reload: 2.,
            effect: 33,
        });
        let stats = MobStats::derive(&mob);
        assert!(stats.shield > 10_000_000.);
        assert_eq!(stats.dps, 100.);
        assert_eq!(stats.reload, 2.);
        assert_eq!(stats.range, 4500.);
        assert_eq!(stats.damage_type, 7);
    }
}
