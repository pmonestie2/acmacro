//! Immutable public item catalog and explicit legacy template packet encoding.
//! Item templates never contain instance slots; inventory owns those separately.
use crate::account::CharacterProfile;
use crate::protocol::{invalid, string, Frame};
use crate::skills::SkillCatalog;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::io;
use std::sync::{Arc, OnceLock};

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "kind", content = "value", rename_all = "snake_case")]
pub enum FieldValue {
    Integer(i32),
    Float(f32),
    Text(String),
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ItemField {
    pub index: u32,
    pub value: FieldValue,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ItemTemplate {
    pub id: u32,
    pub name: String,
    pub description: String,
    pub manufacturer: String,
    pub category: i32,
    pub subcategory: i32,
    pub item_type: u32,
    pub level: u16,
    pub icon: u16,
    pub asset: u16,
    pub price: u32,
    pub fields: Vec<ItemField>,
    pub max_stack: u32,
    pub flags: u32,
    pub use_effect: u32,
    pub activated: Vec<ItemEffect>,
    pub equipped: Vec<ItemEffect>,
    pub activation: Activation,
    pub components: Vec<u32>,
    pub refines_into: u32,
    pub ammo_type: u32,
    #[serde(default)]
    pub crafting: CraftingData,
}

/// Public SQL values are retained even where the archived handlers lack an
/// algorithm. In particular 9,999,999 is a missing-cost sentinel, not a quote.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CraftingData {
    pub cost: u64,
    pub cost_base: u64,
    pub cost_modifier: u32,
    pub manufacture_difficulty: i32,
    pub analyze_difficulty: i32,
    pub dismantle_difficulty: i32,
    pub recipe_difficulty: i32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Modifier {
    pub stat: String,
    pub operation: u32,
    pub value: f32,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ItemEffect {
    pub id: u32,
    pub name: String,
    pub description: String,
    pub tooltip: String,
    pub buff_name: String,
    pub flag1: i32,
    pub flag2: i32,
    pub visual: i32,
    pub values: Vec<f32>,
    pub modifiers: Vec<Modifier>,
}

impl ItemEffect {
    fn encode(&self, data: &mut Vec<u8>) {
        for text in [&self.name, &self.description, &self.tooltip] {
            string(data, text);
        }
        data.extend_from_slice(&0u32.to_le_bytes());
        data.extend_from_slice(&(self.values.len() as u32).to_be_bytes());
        for value in &self.values {
            data.extend_from_slice(&value.to_bits().to_be_bytes());
        }
        data.extend_from_slice(&self.flag1.to_le_bytes());
        data.extend_from_slice(&self.flag2.to_le_bytes());
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Activation {
    pub recharge: f32,
    pub range: f32,
    pub energy: f32,
}

#[derive(Clone, Copy, Deserialize)]
pub struct StartingItem {
    pub template: u32,
    pub slot: usize,
}

#[derive(Clone)]
pub struct ItemCatalog(Arc<HashMap<u32, ItemTemplate>>);

impl ItemCatalog {
    pub fn load() -> io::Result<Self> {
        static CATALOG: OnceLock<Arc<HashMap<u32, ItemTemplate>>> = OnceLock::new();
        if let Some(items) = CATALOG.get() {
            return Ok(Self(Arc::clone(items)));
        }
        let records: Vec<ItemTemplate> = serde_json::from_str(include_str!("../data/items.json"))?;
        let mut items = HashMap::new();
        for item in records {
            item.validate()?;
            if items.insert(item.id, item).is_some() {
                return Err(invalid("duplicate item template"));
            }
        }
        let _ = CATALOG.set(Arc::new(items));
        Ok(Self(Arc::clone(CATALOG.get().unwrap())))
    }
    pub fn get(&self, id: u32) -> io::Result<&ItemTemplate> {
        self.0
            .get(&id)
            .ok_or_else(|| invalid("unknown item template"))
    }
    pub fn iter(&self) -> impl Iterator<Item = &ItemTemplate> {
        self.0.values()
    }
    pub fn starting_items(&self) -> io::Result<Vec<StartingItem>> {
        let items: Vec<StartingItem> =
            serde_json::from_str(include_str!("../data/je_loadout.json"))?;
        for item in &items {
            self.get(item.template)?;
        }
        Ok(items)
    }
}

impl ItemTemplate {
    /// The archived quality curve scales selected fields, then its instance
    /// parser truncates integral fields. Use the same values for UI overrides
    /// and simulation, so a high-quality item cannot display different stats.
    pub fn number_at_quality(&self, index: u32, quality: f32) -> f32 {
        let rule = match (self.item_type, index) {
            (14, 5) | (2, 26) | (7, 20) => Some((1.55, false)),
            (6, 31) => Some((1.35, false)),
            (6, 34) | (15, 21) | (16, 21) => Some((1.55, true)),
            _ => None,
        };
        let base = self.number(index);
        let Some((maximum, inverse)) = rule else {
            return base;
        };
        let effectiveness = if quality < 1. {
            quality * 0.99 + 0.01
        } else {
            1. + (quality - 1.) * (maximum - 1.)
        };
        let value = (base
            * if inverse {
                2. - effectiveness
            } else {
                effectiveness
            })
        .max(1.);
        if self
            .fields
            .iter()
            .any(|field| field.index == index && matches!(field.value, FieldValue::Integer(_)))
        {
            value.trunc()
        } else {
            (value * 100.).round() / 100.
        }
    }

    pub fn instance_info(&self, quality: f32) -> String {
        let fields: &[u32] = match self.item_type {
            14 => &[5],
            15 | 16 => &[21],
            2 => &[26],
            6 => &[31, 34],
            7 => &[20],
            _ => &[],
        };
        fields
            .iter()
            .map(|index| format!("{index}:{:.2}^", self.number_at_quality(*index, quality)))
            .collect()
    }

    pub fn check_requirements(
        &self,
        profile: &CharacterProfile,
        skills: &SkillCatalog,
        levels: [u32; 3],
    ) -> io::Result<()> {
        let race = 1u32.checked_shl(profile.race).unwrap_or(0);
        let profession = 1u32.checked_shl(profile.profession).unwrap_or(0);
        if race == 0
            || profession == 0
            || self.integer(18) as u32 & race != 0
            || self.integer(3) as u32 & profession != 0
        {
            return Err(invalid(
                "this character's race or profession cannot equip the item",
            ));
        }
        let required_skill = match self.subcategory {
            100 => Some(1),
            101 => Some(40),
            102 => Some(34),
            110 => Some(18),
            120 => Some(45),
            121 => Some(20),
            122 => Some(55),
            _ => None,
        };
        if required_skill.is_some_and(|id| skills.level(id) < self.level as u32) {
            return Err(invalid("the required equipment skill is too low"));
        }
        for (field, level) in [
            (4, levels[0]),
            (12, levels[1]),
            (32, levels[2]),
            (14, levels.iter().copied().fold(0, u32::saturating_add)),
        ] {
            if self.number(field) > level as f32 {
                return Err(invalid("item level requirement is not met"));
            }
        }
        let lore = self.number(17) as u32;
        if (profile.race == 1 && lore == 2) || (profile.race == 2 && lore == 1) {
            return Err(invalid("this character's lore cannot equip the item"));
        }
        Ok(())
    }

    fn validate(&self) -> io::Result<()> {
        let mut seen = std::collections::HashSet::new();
        if self.id == 0
            || self.name.is_empty()
            || self.name.len() > 63
            || !self.name.is_ascii()
            || self.max_stack == 0
            || self.max_stack > 1_000_000
            || self.level > 9
            || self.fields.len() > 38
            || self.activated.len() > 6
            || self.equipped.len() > 6
            || self.description.len() > 16_000
            || self.components.len() > 6
        {
            return Err(invalid(&format!("invalid item template {}", self.id)));
        }
        for field in &self.fields {
            if field.index >= 38 || !seen.insert(field.index) {
                return Err(invalid("invalid item field index"));
            }
            match &field.value {
                FieldValue::Float(value) if !value.is_finite() => {
                    return Err(invalid("nonfinite item field"))
                }
                FieldValue::Text(value) if value.len() > 1024 => {
                    return Err(invalid("oversized item field"))
                }
                _ => {}
            }
        }
        if self.activated.iter().chain(&self.equipped).any(|effect| {
            effect.values.len() > 3
                || effect.values.iter().any(|value| !value.is_finite())
                || effect
                    .modifiers
                    .iter()
                    .any(|modifier| !modifier.value.is_finite())
        }) {
            return Err(invalid("invalid item effect data"));
        }
        if self.packet().body.len() > 32_000 {
            return Err(invalid("item template exceeds packet capacity"));
        }
        Ok(())
    }

    pub fn number(&self, index: u32) -> f32 {
        match self
            .fields
            .iter()
            .find(|field| field.index == index)
            .map(|field| &field.value)
        {
            Some(FieldValue::Integer(value)) => *value as f32,
            Some(FieldValue::Float(value)) => *value,
            _ => 0.,
        }
    }

    pub fn integer(&self, index: u32) -> i32 {
        match self
            .fields
            .iter()
            .find(|field| field.index == index)
            .map(|field| &field.value)
        {
            Some(FieldValue::Integer(value)) => *value,
            _ => 0,
        }
    }

    pub fn packet(&self) -> Frame {
        let mut data = Vec::from(self.id.to_be_bytes());
        for value in [self.category, self.subcategory, self.item_type as i32] {
            data.extend_from_slice(&value.to_le_bytes());
        }
        data.push(self.fields.len() as u8);
        for field in &self.fields {
            data.extend_from_slice(&field.index.to_le_bytes());
            match &field.value {
                FieldValue::Integer(value) => data.extend_from_slice(&value.to_le_bytes()),
                FieldValue::Float(value) => data.extend_from_slice(&value.to_le_bytes()),
                FieldValue::Text(value) => {
                    let mut value = value.clone();
                    value.push('\0');
                    string(&mut data, &value);
                }
            }
        }
        data.extend_from_slice(&(self.activated.len() as u32).to_be_bytes());
        for effect in &self.activated {
            effect.encode(&mut data);
        }
        if !self.activated.is_empty() {
            // C++ EffectData holds integral recharge/range, not IEEE floats.
            for value in [
                self.activation.recharge as i32,
                0,
                self.activation.range as i32,
                0,
            ] {
                data.extend_from_slice(&value.to_le_bytes());
            }
        }
        data.extend_from_slice(&(self.equipped.len() as u32).to_be_bytes());
        for effect in &self.equipped {
            effect.encode(&mut data);
        }
        if !self.equipped.is_empty() {
            data.extend_from_slice(&[0; 16]);
        }
        for value in [self.asset, self.icon, self.level] {
            data.extend_from_slice(&value.to_be_bytes());
        }
        for value in [self.price, self.max_stack, self.use_effect] {
            data.extend_from_slice(&value.to_be_bytes());
        }
        data.extend_from_slice(&self.flags.to_le_bytes());
        for value in [&self.name, &self.description, &self.manufacturer] {
            string(&mut data, value);
        }
        Frame::new(0x25, data)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn final_class_hulls_and_equipped_modifiers_flow_through_one_stat_derivation() {
        // No imported item currently supplies STAT_HULL. Copy the immutable
        // public catalog locally to exercise the real equipped-effect path;
        // never mutate the global catalog or introduce a production test API.
        let original = ItemCatalog::load().unwrap();
        let mut modified = original.clone();
        let mut effect = modified.get(2335).unwrap().equipped[0].clone();
        effect.modifiers = [(1, 500.), (2, 25.), (3, 100.), (4, 5.)]
            .into_iter()
            .map(|(operation, value)| Modifier {
                stat: "STAT_HULL".into(),
                operation,
                value,
            })
            .collect();
        Arc::make_mut(&mut modified.0)
            .get_mut(&2335)
            .unwrap()
            .equipped = vec![effect];
        for (index, expected) in [
            72000., 56000., 55500., 66300., 55000., 42000., 82000., 66300., 55000.,
        ]
        .into_iter()
        .enumerate()
        {
            let profile = CharacterProfile {
                game_id: crate::account::FIRST_PLAYER_ID,
                name: "HullTest".into(),
                race: index as u32 / 3,
                profession: index as u32 % 3,
            };
            let skills = SkillCatalog::for_profile(&profile).unwrap();
            let mut inventory = crate::inventory::Inventory::starter(&original).unwrap();
            let base =
                crate::equipment_stats::EquipmentStats::derive(&inventory, &original, &skills)
                    .unwrap();
            assert_eq!(base.ship.hull_capacity, expected);
            // Duplicate equipped effects select the strongest per operation;
            // neither duplicate items nor repeated refreshes multiply the base.
            for slot in [9, 10] {
                inventory.equipped[slot] = Some(crate::inventory::ItemInstance::stock(2335, 1));
            }
            for _ in 0..2 {
                let stats =
                    crate::equipment_stats::EquipmentStats::derive(&inventory, &modified, &skills)
                        .unwrap();
                assert_eq!(stats.ship.hull_capacity, expected * 1.2 + 400.);
            }
            for slot in [9, 10] {
                inventory.equipped[slot].as_mut().unwrap().structure = 0.;
            }
            let broken =
                crate::equipment_stats::EquipmentStats::derive(&inventory, &modified, &skills)
                    .unwrap();
            assert_eq!(broken.ship.hull_capacity, expected);
        }
        assert_eq!(
            original.get(2335).unwrap().equipped[0]
                .modifiers
                .iter()
                .filter(|modifier| modifier.stat == "STAT_HULL")
                .count(),
            0
        );
    }

    #[test]
    fn effect_wire_uses_network_float_bits_and_legacy_integral_flags() {
        let effect = ItemEffect {
            id: 1,
            name: "Boost".into(),
            description: "x".into(),
            tooltip: String::new(),
            buff_name: String::new(),
            flag1: 2,
            flag2: 1,
            visual: -1,
            values: vec![1.5, -2., 0.],
            modifiers: vec![],
        };
        let mut wire = Vec::new();
        effect.encode(&mut wire);
        let mut expected =
            b"\x05\x00Boost\x01\x00x\x00\x00\x00\x00\x00\x00\x00\x00\x00\x03".to_vec();
        for bits in [0x3fc00000u32, 0xc0000000, 0] {
            expected.extend_from_slice(&bits.to_be_bytes());
        }
        expected.extend_from_slice(&[2, 0, 0, 0, 1, 0, 0, 0]);
        assert_eq!(wire, expected);
    }

    #[test]
    fn equipment_restrictions_use_class_skills_and_character_levels() {
        let catalog = ItemCatalog::load().unwrap();
        assert_eq!(catalog.0.len(), 4568);
        let skills = SkillCatalog::load().unwrap();
        let profile = crate::account::Account::create("test".into(), "example", "Test".into())
            .unwrap()
            .character;
        let mut engine = catalog.get(2505).unwrap().clone();
        engine
            .check_requirements(&profile, &skills, [50, 50, 50])
            .unwrap();
        engine.fields.push(ItemField {
            index: 14,
            value: FieldValue::Integer(151),
        });
        assert!(engine
            .check_requirements(&profile, &skills, [50, 50, 50])
            .is_err());
        engine
            .fields
            .retain(|field| field.index != 14 && field.index != 3);
        engine.fields.push(ItemField {
            index: 3,
            value: FieldValue::Integer(1 << 2),
        });
        assert!(engine
            .check_requirements(&profile, &skills, [50, 50, 50])
            .is_err());
        engine.fields.retain(|field| field.index != 3);
        engine.subcategory = 102; // JE has no missile weapon skill.
        assert!(engine
            .check_requirements(&profile, &skills, [50, 50, 50])
            .is_err());
    }
    #[test]
    fn engine_template_and_equipment_slots_match_cpp_defaults() {
        let items = ItemCatalog::load().unwrap();
        let engine = items.get(2505).unwrap();
        assert_eq!(engine.id, 2505);
        assert_eq!(engine.name, "Compression Thrusters");
        let frame = engine.packet();
        assert_eq!(&frame.body[..4], &2505u32.to_be_bytes());
        assert_eq!(&frame.body[4..8], &12u32.to_le_bytes());
        assert!(frame
            .body
            .windows(engine.name.len())
            .any(|part| part == engine.name.as_bytes()));
    }
}
