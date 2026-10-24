//! The class catalog is generated once from public content; runtime cooldowns
//! and casts are separate state. This keeps wire indices out of gameplay rules.
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io;
#[path = "jumpstart_rules.rs"]
mod jumpstart_rules;
pub use jumpstart_rules::JumpstartPlan;

#[derive(Clone, Debug, Deserialize)]
pub struct SkillDefinition {
    pub id: u32,
    pub name: String,
    pub level: u32,
    pub active: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AbilityKind {
    Cloak,
    Contemplation,
    Wormhole,
    Environment,
    Fold,
    Jumpstart,
    Leech,
    Unsupported,
}

#[derive(Clone, Debug, Deserialize)]
pub struct AbilityDefinition {
    pub id: u32,
    pub skill: u32,
    pub rank: u32,
    pub name: String,
    pub description: String,
    pub kind: AbilityKind,
    pub energy: f32,
    pub charge: f32,
    pub cooldown: f32,
    pub range: u32,
    pub radius: u32,
    pub destination: Option<u32>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct SkillCatalog {
    #[serde(default = "je_index")]
    pub class_index: usize,
    pub skills: Vec<SkillDefinition>,
    pub abilities: Vec<AbilityDefinition>,
}

fn je_index() -> usize {
    5
}

impl SkillCatalog {
    pub fn for_profile(profile: &crate::account::CharacterProfile) -> io::Result<Self> {
        let catalogs: Vec<Self> = serde_json::from_str(include_str!("../data/class_skills.json"))?;
        if catalogs.len() != 9 {
            return Err(invalid("Incomplete class skill catalog."));
        }
        let class = profile.class();
        let catalog = catalogs
            .into_iter()
            .nth(class.0)
            .ok_or_else(|| invalid("Missing class catalog."))?;
        if catalog.class_index != class.0
            || catalog.skills.is_empty()
            || catalog
                .skills
                .iter()
                .any(|skill| skill.id >= 64 || skill.level == 0 || skill.level > 9)
            || catalog
                .abilities
                .iter()
                .any(|ability| ability.id >= 170 || catalog.level(ability.skill) < ability.rank)
        {
            return Err(invalid("Invalid class skill catalog."));
        }
        Ok(catalog)
    }
    pub fn load() -> io::Result<Self> {
        let catalog: Self = serde_json::from_str(include_str!("../data/je_skills.json"))?;
        if catalog.skills.len() != 18
            || catalog.abilities.len() != 27
            || catalog.abilities.iter().any(|ability| {
                ability.id >= 170
                    || !catalog
                        .skills
                        .iter()
                        .any(|skill| skill.id == ability.skill && skill.level >= ability.rank)
            })
        {
            return Err(invalid("invalid Jenquai Explorer skill catalog"));
        }
        Ok(catalog)
    }
    pub fn ability(&self, id: u32) -> io::Result<AbilityDefinition> {
        self.abilities
            .iter()
            .find(|ability| ability.id == id)
            .cloned()
            .ok_or_else(|| invalid("This character does not have that ability."))
    }
    pub fn level(&self, skill: u32) -> u32 {
        self.skills
            .iter()
            .find(|definition| definition.id == skill)
            .map_or(0, |definition| definition.level)
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Cast {
    pub ability: u32,
    pub target: u32,
    pub remaining: f32,
    pub effect: u32,
    /// Absent only for old checkpoints or other abilities. Old in-flight
    /// Jumpstarts retain their committed cost, remaining time and legacy repair.
    #[serde(default)]
    pub jumpstart: Option<JumpstartPlan>,
}

impl Cast {
    pub fn valid(&self) -> bool {
        self.ability < 170
            && self.remaining.is_finite()
            && self.remaining >= 0.
            && self.jumpstart.as_ref().is_none_or(|plan| {
                self.ability == 65 && plan.valid() && self.remaining <= plan.charge_seconds
            })
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct SkillState {
    pub cast: Option<Cast>,
    pub cooldowns: BTreeMap<u32, f32>,
    pub cloak_rank: u32,
    pub cloak_effect: Option<u32>,
    pub environment_rank: u32,
    pub environment_seconds: f32,
    pub environment_effect: Option<u32>,
    pub combat_cloak_seconds: f32,
    #[serde(default)]
    pub cloak_source: Option<u32>,
    #[serde(default)]
    pub cloak_stealth: Option<u32>,
    #[serde(default)]
    pub temporary_effects: BTreeMap<u32, f32>,
}

#[derive(Clone, Debug)]
pub struct GroupContact {
    pub id: u32,
    pub sector: u32,
    pub position: [f32; 3],
    pub incapacitated: bool,
}

#[derive(Clone, Debug)]
pub enum SupportAction {
    Cloak {
        enabled: bool,
        rank: u32,
        stealth: u32,
    },
    Environment {
        target: u32,
        rank: u32,
    },
    Jumpstart {
        target: u32,
        repair_fraction: f32,
        range: f32,
    },
    Fold {
        target: u32,
    },
    Energy {
        amount: f32,
    },
    Wormhole {
        destination: u32,
        origin_sector: u32,
        origin: [f32; 3],
    },
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_indices_and_class_limits_are_preserved() {
        let catalog = SkillCatalog::load().unwrap();
        assert_eq!(catalog.level(24), 5); // JE has Fold Space 5, not area fold 7.
        assert_eq!(catalog.level(1), 9);
        assert_eq!(catalog.ability(0).unwrap().kind, AbilityKind::Cloak);
        assert_eq!(catalog.ability(37).unwrap().destination, Some(2210));
        assert!(catalog.ability(17).is_err()); // Area Teleport is not a JE ability.
    }
}
