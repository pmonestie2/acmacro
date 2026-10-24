//! Equipment-derived ship parameters. Public SQL fields, item quality and JE
//! passives feed one calculation shared by simulation and the client display.
use crate::inventory::Inventory;
use crate::items::{ItemCatalog, Modifier};
use crate::skills::SkillCatalog;
use std::collections::HashMap;
use std::io;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct ShipStats {
    pub weapon_mounts: usize,
    pub device_mounts: usize,
    pub hull_capacity: f32,
    pub energy_capacity: f32,
    pub energy_recharge: f32,
    pub shield_capacity: f32,
    pub shield_recharge: f32,
    pub impulse_speed: f32,
    pub warp_speed: f32,
    pub warp_drain: f32,
    pub warp_recovery: f32,
    pub acceleration: f32,
    pub scan_range: f32,
    pub signature: f32,
    pub turn_rate: f32,
    pub prospect_bonus: f32,
    pub tractor_speed: f32,
    pub deflects: [f32; 9],
}

#[derive(Clone, Debug)]
pub struct WeaponStats {
    pub slot: usize,
    pub item_type: u32,
    pub ammunition: Option<u32>,
    pub rounds: u32,
    pub damage: f32,
    pub damage_type: usize,
    pub range: f32,
    pub energy: f32,
    pub reload: f32,
    pub effect: u16,
}

pub struct EquipmentStats {
    pub ship: ShipStats,
    pub weapons: Vec<WeaponStats>,
}

/// The archived stat engine chooses the strongest equipped modifier for each
/// stat and operation in ITEM_EFFECTS, rather than stacking duplicate buffs.
#[derive(Default)]
struct EquippedModifiers(HashMap<(String, u32), f32>);

impl EquippedModifiers {
    fn add(&mut self, modifier: &Modifier) {
        let name = match modifier.stat.trim() {
            "STAT_ENGINE_TOP_SPEED" => "STAT_IMPULSE",
            "STAT_TURN_RATE" => "STAT_TURN",
            value => value,
        };
        if !(1..=4).contains(&modifier.operation) {
            return;
        } // 5 is a duration.
        let value = self
            .0
            .entry((name.to_owned(), modifier.operation))
            .or_insert(modifier.value);
        *value = value.max(modifier.value);
    }
    fn value(&self, name: &str, operation: u32) -> f32 {
        self.0
            .get(&(name.to_owned(), operation))
            .copied()
            .unwrap_or(0.)
    }
    fn apply(&self, name: &str, base: f32) -> f32 {
        self.apply_signed(name, base).max(0.)
    }
    fn apply_signed(&self, name: &str, base: f32) -> f32 {
        base * (1. + (self.value(name, 2) - self.value(name, 4)) / 100.) + self.value(name, 1)
            - self.value(name, 3)
    }
}

impl EquipmentStats {
    pub fn derive(
        inventory: &Inventory,
        catalog: &ItemCatalog,
        skills: &SkillCatalog,
    ) -> io::Result<Self> {
        inventory.validate(catalog)?;
        let mut modifiers = EquippedModifiers::default();
        for item in inventory
            .equipped
            .iter()
            .flatten()
            .filter(|item| item.structure > 0.)
        {
            for effect in &catalog.get(item.template)?.equipped {
                for modifier in &effect.modifiers {
                    modifiers.add(modifier);
                }
            }
        }
        let field = |slot: usize, index: u32| -> io::Result<f32> {
            match inventory.equipped[slot]
                .as_ref()
                .filter(|item| item.structure > 0.)
            {
                Some(item) => Ok(catalog
                    .get(item.template)?
                    .number_at_quality(index, item.quality)),
                None => Ok(0.),
            }
        };
        let navigate = skills.level(35) as f32;
        let has_engine = inventory.equipped[2]
            .as_ref()
            .is_some_and(|item| item.structure > 0.);
        let impulse_bonus = if navigate >= 5. {
            -80. + 30. * navigate
        } else {
            0.
        };
        let warp_bonus = if navigate >= 6. {
            -1400. + 300. * navigate
        } else {
            0.
        };
        let mut deflects = [0.; 9];
        for (index, name) in [
            "STAT_IMPACT_DEFLECT",
            "STAT_EXPLOSIVE_DEFLECT",
            "STAT_PLASMA_DEFLECT",
            "STAT_ENERGY_DEFLECT",
            "STAT_EMP_DEFLECT",
            "STAT_CHEM_DEFLECT",
            "STAT_ABSOLUTE_DEFLECT",
            "STAT_SYSTEM_DEFLECT",
            "STAT_PSIONIC_DEFLECT",
        ]
        .into_iter()
        .enumerate()
        {
            deflects[index] = modifiers.apply_signed(name, 0.).min(50.);
        }
        let class = crate::classes::CharacterClass(skills.class_index);
        let ship = ShipStats {
            weapon_mounts: class.weapons(),
            device_mounts: class.devices(),
            hull_capacity: modifiers.apply(
                "STAT_HULL",
                crate::ship_progression::hull_capacity(
                    class,
                    crate::ship_progression::SANDBOX_HULL_UPGRADE,
                )?,
            ),
            energy_capacity: modifiers.apply("STAT_ENERGY", field(1, 16)?),
            energy_recharge: modifiers.apply("STAT_ENERGY_RECHARGE", field(1, 20)?),
            shield_capacity: modifiers.apply("STAT_SHIELD", field(0, 24)?),
            shield_recharge: modifiers.apply("STAT_SHIELD_RECHARGE", field(0, 26)?),
            impulse_speed: if has_engine {
                modifiers.apply("STAT_IMPULSE", field(2, 31)? + impulse_bonus)
            } else {
                0.
            },
            warp_speed: if has_engine {
                modifiers.apply("STAT_WARP", field(2, 33)? + warp_bonus)
                    + modifiers.apply("STAT_ADDITIVE_WARP", 0.)
            } else {
                0.
            },
            warp_drain: modifiers.apply("STAT_WARP_ENERGY", field(2, 34)?),
            warp_recovery: modifiers.apply("STAT_WARP_RECOVERY", 2.).max(0.1),
            acceleration: modifiers.apply("STAT_ACCELERATION", 100.),
            scan_range: modifiers.apply("STAT_SCAN_RANGE", 1000. + 250. * skills.level(49) as f32),
            signature: modifiers.apply("STAT_SIGNATURE", field(2, 29)?),
            turn_rate: modifiers.apply("STAT_TURN", 45.),
            prospect_bonus: modifiers.apply("STAT_SKILL_PROSPECT", 0.),
            tractor_speed: modifiers.apply("STAT_TRACTORBEAM", 200.).max(1.),
            deflects,
        };
        let mut weapons = Vec::new();
        for (slot, item) in inventory
            .equipped
            .iter()
            .enumerate()
            .filter_map(|(slot, item)| item.as_ref().map(|item| (slot, item)))
        {
            let template = catalog.get(item.template)?;
            if item.structure == 0. || !matches!(template.item_type, 14..=16) {
                continue;
            }
            let (damage_stat, range_stat, energy_stat, turbo_stat, skill) = match template.item_type
            {
                14 => (
                    "STAT_BEAM_DAMAGE",
                    "STAT_BEAM_RANGE",
                    "STAT_BEAM_ENERGY",
                    "STAT_BEAM_TURBO",
                    1,
                ),
                15 => (
                    "STAT_MISSILE_DAMAGE",
                    "STAT_MISSILE_RANGE",
                    "STAT_MISSILE_ENERGY",
                    "STAT_MISSILE_TURBO",
                    34,
                ),
                _ => (
                    "STAT_PROJECTILES_DAMAGE",
                    "STAT_PROJECTILES_RANGE",
                    "STAT_PROJECTILE_ENERGY",
                    "STAT_PROJECTILE_TURBO",
                    40,
                ),
            };
            let ammunition = inventory.ammunition[slot].as_ref();
            let ammo_template = ammunition
                .map(|item| catalog.get(item.template))
                .transpose()?;
            let damage_template = if template.item_type == 14 {
                Some(template)
            } else {
                ammo_template
            };
            let damage_quality = if template.item_type == 14 {
                item.quality
            } else {
                1.
            };
            let damage =
                damage_template.map_or(0., |source| source.number_at_quality(5, damage_quality));
            let damage = if damage_template.is_some() && damage <= 0. {
                3.
            } else {
                damage
            };
            let damage_type = damage_template.map_or(0, |item| item.integer(6).max(0) as usize);
            let range = if template.item_type == 15 {
                ammo_template.map_or(0., |ammo| ammo.number(19))
            } else {
                template.number(19)
            };
            let skill_bonus = 1. + (skills.level(skill) as f32 - 1.).max(0.) * 0.25;
            let turbo = (modifiers.apply("STAT_WEAPON_TURBO", 0.)
                + modifiers.apply(turbo_stat, 0.))
            .min(95.);
            weapons.push(WeaponStats {
                slot,
                item_type: template.item_type,
                ammunition: ammunition.map(|item| item.template),
                rounds: if template.item_type == 14 {
                    1
                } else {
                    (template.integer(22).max(1) as u32).min(64)
                },
                damage: modifiers.apply(damage_stat, damage * skill_bonus),
                damage_type,
                range: modifiers.apply(range_stat, range),
                energy: modifiers.apply(
                    energy_stat,
                    modifiers.apply("STAT_WEAPON_ENERGY", template.number(9)),
                ),
                reload: (template.number_at_quality(21, item.quality) * (1. - turbo / 100.))
                    .max(0.1),
                effect: damage_template
                    .map_or(template.use_effect, |item| item.use_effect)
                    .try_into()
                    .unwrap_or(35),
            });
        }
        Ok(Self { ship, weapons })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn real_equipped_chemical_vulnerability_reaches_damage_resolution_and_signed_display_stats() {
        let catalog = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let mut inventory = Inventory::starter(&catalog).unwrap();
        inventory.equipped[9] = Some(crate::inventory::ItemInstance::stock(2335, 1));
        let stats = EquipmentStats::derive(&inventory, &catalog, &skills).unwrap();
        assert_eq!(stats.ship.deflects[5], -33.);
        let hit = crate::combat_damage::player_damage(
            100.,
            crate::combat_damage::DamageType::Chemical,
            crate::combat_damage::PlayerDefense {
                shield: 1000.,
                hull: 100.,
                deflect_percent: stats.ship.deflects[5],
                damage_control: 0.,
            },
        )
        .unwrap();
        assert!((hit.shield - 867.).abs() < 0.001);
        let mut negative = EquippedModifiers::default();
        negative.add(&Modifier {
            stat: "STAT_SIGNATURE".into(),
            operation: 1,
            value: -50.,
        });
        assert_eq!(negative.apply_signed("STAT_SIGNATURE", 100.), 50.);
    }

    #[test]
    fn launchers_take_damage_effects_and_missile_range_from_owned_ammunition() {
        let catalog = ItemCatalog::load().unwrap();
        let mut inventory = Inventory::starter(&catalog).unwrap();
        inventory.equipped[3] = Some(crate::inventory::ItemInstance::stock(3022, 1));
        let je = SkillCatalog::load().unwrap();
        let dry = EquipmentStats::derive(&inventory, &catalog, &je).unwrap();
        assert_eq!(dry.weapons[0].damage, 0.);
        inventory.ammunition[3] = Some(crate::inventory::ItemInstance::stock(512, 1));
        let pl = EquipmentStats::derive(&inventory, &catalog, &je).unwrap();
        assert_eq!(pl.weapons[0].damage, 11.);
        assert_eq!(pl.weapons[0].range, 2900.);
        assert_eq!(pl.weapons[0].effect, 948);
        inventory.equipped[3] = Some(crate::inventory::ItemInstance::stock(2656, 1));
        inventory.ammunition[3] = Some(crate::inventory::ItemInstance::stock(507, 1));
        let mut profile = crate::account::Account::create("te".into(), "pass", "TE".into())
            .unwrap()
            .character;
        profile.race = 0;
        profile.profession = 0;
        let te = SkillCatalog::for_profile(&profile).unwrap();
        let ml = EquipmentStats::derive(&inventory, &catalog, &te).unwrap();
        assert_eq!(ml.weapons[0].damage, 60.);
        assert_eq!(ml.weapons[0].range, 4900.);
        assert_eq!(ml.weapons[0].effect, 886);
    }

    #[test]
    fn public_device_buffs_change_speed_warp_recovery_and_energy_cost() {
        let catalog = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let mut inventory = Inventory::starter(&catalog).unwrap();
        for (slot, template) in [(9, 1766), (10, 1765), (11, 1903), (12, 1876)] {
            inventory.equipped[slot] = Some(crate::inventory::ItemInstance {
                template,
                quantity: 1,
                quality: 1.,
                structure: 1.,
                builder_name: String::new(),
            });
        }
        let stats = EquipmentStats::derive(&inventory, &catalog, &skills).unwrap();
        assert_eq!(stats.ship.impulse_speed, 212.);
        assert_eq!(stats.ship.warp_speed, 2872.);
        assert!((stats.ship.warp_recovery - 1.5).abs() < 0.001);
        assert!((stats.weapons[0].energy - 11. * 0.76).abs() < 0.001);
    }
    #[test]
    fn starter_gear_passives_quality_and_missing_equipment_change_actual_stats() {
        let catalog = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let mut inventory = Inventory::starter(&catalog).unwrap();
        let base = EquipmentStats::derive(&inventory, &catalog, &skills).unwrap();
        assert_eq!(base.ship.energy_capacity, 142.);
        assert_eq!(base.ship.shield_capacity, 45.);
        assert_eq!(base.ship.impulse_speed, 192.);
        assert_eq!(base.ship.warp_speed, 2700.);
        assert_eq!(base.weapons[0].damage, 9.);
        assert_eq!(base.weapons[0].reload, 2.);
        inventory.equipped[2].as_mut().unwrap().quality = 2.;
        inventory.equipped[1].as_mut().unwrap().quality = 2.;
        let improved = EquipmentStats::derive(&inventory, &catalog, &skills).unwrap();
        assert_eq!(improved.ship.impulse_speed, 213.);
        assert!((improved.ship.energy_recharge - 5.4 * 1.55).abs() < 0.01);
        inventory.equipped[2] = None;
        inventory.equipped[1] = None;
        inventory.equipped[3] = None;
        let absent = EquipmentStats::derive(&inventory, &catalog, &skills).unwrap();
        assert_eq!(absent.ship.warp_speed, 0.);
        assert_eq!(absent.ship.impulse_speed, 0.);
        assert_eq!(absent.ship.energy_capacity, 0.);
        assert!(absent.weapons.is_empty());
    }
    #[test]
    fn equipped_modifier_duplicates_take_strongest_per_operation() {
        let mut modifiers = EquippedModifiers::default();
        for (operation, value) in [(1, 10.), (1, 20.), (2, 50.), (3, 5.), (4, 10.)] {
            modifiers.add(&Modifier {
                stat: "STAT_ENGINE_TOP_SPEED".into(),
                operation,
                value,
            });
        }
        assert_eq!(modifiers.apply("STAT_IMPULSE", 100.), 155.);
    }
}
