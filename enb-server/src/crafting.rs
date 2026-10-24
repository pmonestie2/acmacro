//! Manufacture/analysis transactions. The archived implementation has no build
//! algorithm: normal-quality builds and the analysis chance below are explicit
//! prototype policy, while recipes, costs and skill ceilings come from SQL.
use crate::inventory::{Inventory, ItemInstance};
use crate::items::ItemCatalog;
use crate::production::ProductionRequest;
use crate::protocol::invalid;
use crate::skills::SkillCatalog;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io;

pub const CRAFT_DELAY_MS: u64 = 3000;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CraftOperation {
    Manufacture,
    Analyze,
    Dismantle,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CraftRecipe {
    pub product: u32,
    pub components: Vec<u32>,
    pub cost: u64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CraftJob {
    pub station: u32,
    pub operation: CraftOperation,
    pub recipe: CraftRecipe,
    pub due_ms: u64,
    pub request: ProductionRequest,
    /// An analyzer sample is compared by full instance identity on completion.
    pub sample: Option<ItemInstance>,
    /// Chosen once before persistence; restart cannot reroll an analysis.
    pub analysis_succeeded: bool,
    #[serde(default)]
    pub builder_name: String,
    /// Rolled once per component before saving; repeated IDs preserve quantity.
    #[serde(default)]
    pub recovered_components: Vec<u32>,
}

/// Public skill_levels descriptions explicitly map ranks 6 -> item 7 and
/// 7 -> item 9; ranks 1 through 5 match the item level.
pub fn build_ceiling(rank: u32) -> u32 {
    match rank {
        0..=5 => rank,
        6 => 7,
        _ => 9,
    }
}

impl CraftRecipe {
    pub fn resolve(product: u32, items: &ItemCatalog) -> io::Result<Self> {
        let item = items.get(product)?;
        if item.flags & 128 != 0
            || !(1..=9).contains(&item.level)
            || item.components.is_empty()
            || item.components.len() > 6
        {
            return Err(invalid("This item has no manufacturable recipe."));
        }
        if item.crafting.cost == 0 || item.crafting.cost_base >= 9_999_999 {
            return Err(invalid(
                "This recipe has no usable public manufacturing cost.",
            ));
        }
        for component in &item.components {
            items.get(*component)?;
            if *component == product {
                return Err(invalid("A recipe cannot consume its own product."));
            }
        }
        Ok(Self {
            product,
            components: item.components.clone(),
            cost: item.crafting.cost,
        })
    }

    pub fn check_skill(&self, skills: &SkillCatalog, items: &ItemCatalog) -> io::Result<u32> {
        let item = items.get(self.product)?;
        let skill = match item.item_type {
            13 => 4,            // Build Components
            11 => 5,            // Build Devices
            6 => 6,             // Build Engines
            7 => 8,             // Build Reactors
            2 => 9,             // Build Shields
            10 | 14..=16 => 10, // Build Weapons (including ammunition)
            _ => return Err(invalid("No build skill supports this item type.")),
        };
        let ceiling = build_ceiling(skills.level(skill));
        if ceiling < u32::from(item.level) {
            return Err(invalid("Build skill is too low for this item."));
        }
        Ok(ceiling)
    }

    pub fn chance(&self, skills: &SkillCatalog, items: &ItemCatalog) -> io::Result<u32> {
        let ceiling = self.check_skill(skills, items)?;
        let item = items.get(self.product)?;
        // SQL supplies a difficulty, but the archived analyzer is a stub.
        // Publish the prototype policy rather than hiding an invented rule.
        Ok((100 - item.crafting.analyze_difficulty.clamp(0, 100)
            + 10 * (ceiling - u32::from(item.level)) as i32)
            .clamp(5, 100) as u32)
    }

    pub fn requirements(&self) -> BTreeMap<u32, u32> {
        let mut result = BTreeMap::new();
        for component in &self.components {
            *result.entry(*component).or_insert(0) += 1;
        }
        result
    }

    pub fn manufacture(
        &self,
        inventory: &Inventory,
        skills: &SkillCatalog,
        items: &ItemCatalog,
        builder_name: &str,
    ) -> io::Result<Inventory> {
        inventory.validate(items)?;
        self.check_skill(skills, items)?;
        if Self::resolve(self.product, items)? != *self {
            return Err(invalid("Manufacturing recipe changed; select it again."));
        }
        if !inventory.known_formulas.contains(&self.product) {
            return Err(invalid("Analyze this item to learn its recipe first."));
        }
        let product = items.get(self.product)?;
        if product.flags & 4 != 0
            && inventory
                .cargo
                .iter()
                .chain(&inventory.equipped)
                .chain(&inventory.vault)
                .chain(&inventory.production_target)
                .flatten()
                .any(|instance| instance.template == self.product)
        {
            return Err(invalid("This unique item is already owned."));
        }
        let mut candidate = inventory.clone();
        candidate.credits = candidate
            .credits
            .checked_sub(self.cost)
            .ok_or_else(|| invalid("Insufficient manufacturing credits."))?;
        for (template, mut needed) in self.requirements() {
            for slot in &mut candidate.cargo {
                if let Some(item) = slot {
                    if item.template == template {
                        let taken = needed.min(item.quantity);
                        item.quantity -= taken;
                        needed -= taken;
                        if item.quantity == 0 {
                            *slot = None;
                        }
                    }
                }
            }
            if needed > 0 {
                return Err(invalid("Missing manufacturing components."));
            }
        }
        // Full cargo is permitted when consuming components frees a slot.
        if builder_name.is_empty() {
            return Err(invalid(
                "A manufactured item requires its authenticated builder.",
            ));
        }
        let mut built = ItemInstance::stock(self.product, 1);
        built.builder_name = builder_name.to_owned();
        candidate = candidate.received_instance(&built, u32::MAX as usize, items)?;
        candidate.trade_xp = candidate
            .trade_xp
            .checked_add(u64::from(product.level) * 50)
            .ok_or_else(|| invalid("Trade XP overflow."))?;
        Ok(candidate)
    }

    pub fn analyze(
        &self,
        inventory: &Inventory,
        skills: &SkillCatalog,
        items: &ItemCatalog,
        succeeded: bool,
    ) -> io::Result<Inventory> {
        inventory.validate(items)?;
        self.check_skill(skills, items)?;
        if Self::resolve(self.product, items)? != *self {
            return Err(invalid("Analysis recipe changed."));
        }
        if inventory.known_formulas.contains(&self.product) {
            return Err(invalid("This recipe is already known."));
        }
        let sample = inventory.production_target[0]
            .as_ref()
            .ok_or_else(|| invalid("Place an owned item in the analyzer."))?;
        if !sample.builder_name.is_empty() {
            return Err(invalid(
                "Player-made items cannot teach an analysis recipe.",
            ));
        }
        if sample.template != self.product
            || sample.quantity != 1
            || sample.quality < 1.
            || sample.structure <= 0.
        {
            return Err(invalid(
                "Analysis requires one intact item at normal quality or better.",
            ));
        }
        let mut candidate = inventory.clone();
        // No separate analysis fee survives in this dump: use the build quote
        // as explicit prototype policy. Both success and failure consume sample.
        candidate.credits = candidate
            .credits
            .checked_sub(self.cost)
            .ok_or_else(|| invalid("Insufficient analysis credits."))?;
        candidate.production_target[0] = None;
        if succeeded {
            candidate.known_formulas.insert(self.product);
        }
        candidate.validate(items)?;
        Ok(candidate)
    }

    pub fn dismantle_chance(&self, skills: &SkillCatalog, items: &ItemCatalog) -> io::Result<u32> {
        let ceiling = self.check_skill(skills, items)?;
        let item = items.get(self.product)?;
        Ok((100 - item.crafting.dismantle_difficulty.clamp(0, 100)
            + 10 * (ceiling - u32::from(item.level)) as i32)
            .clamp(5, 100) as u32)
    }

    pub fn check_recovery(&self, recovered: &[u32]) -> io::Result<()> {
        let mut available = self.requirements();
        for component in recovered {
            let count = available
                .get_mut(component)
                .filter(|count| **count > 0)
                .ok_or_else(|| invalid("Recovery plan exceeds the item's component recipe."))?;
            *count -= 1;
        }
        Ok(())
    }

    pub fn dismantle(
        &self,
        inventory: &Inventory,
        skills: &SkillCatalog,
        items: &ItemCatalog,
        recovered: &[u32],
    ) -> io::Result<Inventory> {
        inventory.validate(items)?;
        self.check_skill(skills, items)?;
        if Self::resolve(self.product, items)? != *self {
            return Err(invalid("Dismantling recipe changed."));
        }
        self.check_recovery(recovered)?;
        let sample = inventory.production_target[0]
            .as_ref()
            .ok_or_else(|| invalid("Place an owned item in the dismantler."))?;
        if sample.template != self.product || sample.quantity != 1 || sample.structure <= 0. {
            return Err(invalid("Dismantling requires one non-destroyed item."));
        }
        let mut candidate = inventory.clone();
        // Explicit prototype fee: the public dump has no separate rip cost.
        candidate.credits = candidate
            .credits
            .checked_sub(self.cost)
            .ok_or_else(|| invalid("Insufficient dismantling credits."))?;
        candidate.production_target[0] = None;
        for (template, quantity) in
            recovered
                .iter()
                .fold(BTreeMap::<u32, u32>::new(), |mut counts, id| {
                    *counts.entry(*id).or_default() += 1;
                    counts
                })
        {
            candidate = candidate.received(template, quantity, u32::MAX as usize, items)?;
        }
        candidate.validate(items)?;
        Ok(candidate)
    }
}

impl CraftJob {
    pub fn validate(&self, inventory: &Inventory, items: &ItemCatalog) -> io::Result<()> {
        items.get(self.recipe.product)?;
        if !(10000..20000).contains(&self.station)
            || self.due_ms == 0
            || self.request.epoch == 0
            || inventory.last_production_request != Some(self.request)
            || self.recipe.components.is_empty()
            || self.recipe.components.len() > 6
            || self.recipe.cost == 0
            || (self.operation != CraftOperation::Manufacture) != self.sample.is_some()
            || self.builder_name.len() > 63
            || !self.builder_name.is_ascii()
            || self
                .builder_name
                .bytes()
                .any(|byte| byte < 32 || byte == 127)
            || self.operation != CraftOperation::Manufacture && !self.builder_name.is_empty()
            || self.operation != CraftOperation::Dismantle && !self.recovered_components.is_empty()
        {
            return Err(invalid("Invalid saved crafting job."));
        }
        for component in &self.recipe.components {
            items.get(*component)?;
        }
        self.recipe.check_recovery(&self.recovered_components)?;
        Ok(())
    }

    pub fn complete(
        &self,
        inventory: &Inventory,
        skills: &SkillCatalog,
        items: &ItemCatalog,
    ) -> io::Result<Inventory> {
        let mut candidate = inventory.clone();
        candidate.craft_job = None;
        match self.operation {
            CraftOperation::Manufacture => {
                self.recipe
                    .manufacture(&candidate, skills, items, &self.builder_name)
            }
            CraftOperation::Analyze | CraftOperation::Dismantle => {
                if candidate.production_target[0] != self.sample {
                    return Err(invalid("Analyzer sample changed."));
                }
                if self.operation == CraftOperation::Analyze {
                    self.recipe
                        .analyze(&candidate, skills, items, self.analysis_succeeded)
                } else {
                    self.recipe
                        .dismantle(&candidate, skills, items, &self.recovered_components)
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn sample(template: u32) -> Option<ItemInstance> {
        Some(ItemInstance {
            template,
            quantity: 1,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        })
    }

    #[test]
    fn crafted_provenance_blocks_learning_but_dismantle_consumes_sample_and_recovers_only_its_recipe(
    ) {
        let items = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let recipe = CraftRecipe::resolve(1758, &items).unwrap();
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.credits = 1_000_000;
        let mut crafted = ItemInstance::stock(1758, 1);
        crafted.builder_name = "OriginalBuilder".into();
        inventory.production_target[0] = Some(crafted);
        assert!(recipe.analyze(&inventory, &skills, &items, true).is_err());
        assert!(recipe
            .dismantle(&inventory, &skills, &items, &[769])
            .is_err());
        let result = recipe
            .dismantle(&inventory, &skills, &items, &recipe.components)
            .unwrap();
        assert!(result.production_target[0].is_none());
        assert!(result.known_formulas.is_empty());
        assert_eq!(result.credits, inventory.credits - recipe.cost);
        assert_eq!(result.trade_xp, inventory.trade_xp);
        assert_eq!(
            result
                .cargo
                .iter()
                .flatten()
                .map(|i| i.template)
                .collect::<Vec<_>>(),
            recipe.components
        );
        assert!(result
            .cargo
            .iter()
            .flatten()
            .all(|i| i.builder_name.is_empty()));
        let failed = recipe.dismantle(&inventory, &skills, &items, &[]).unwrap();
        assert!(failed.production_target[0].is_none());
        assert_eq!(failed.credits, result.credits);
        assert!(failed.cargo.iter().all(Option::is_none));
        inventory.cargo.fill(sample(769));
        let before = inventory.clone();
        assert!(recipe
            .dismantle(&inventory, &skills, &items, &recipe.components)
            .is_err());
        assert_eq!(inventory, before);
    }

    #[test]
    fn dismantle_plan_survives_reload_and_rejects_extra_or_changed_components_without_destroying_sample(
    ) {
        let items = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let recipe = CraftRecipe::resolve(2372, &items).unwrap();
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.credits = 1_000_000;
        inventory.production_target[0] = sample(2372);
        let request = ProductionRequest {
            epoch: 6,
            sequence: 22,
        };
        inventory.last_production_request = Some(request);
        let job = CraftJob {
            station: 10761,
            operation: CraftOperation::Dismantle,
            recipe: recipe.clone(),
            due_ms: 1,
            request,
            sample: sample(2372),
            analysis_succeeded: false,
            builder_name: String::new(),
            recovered_components: vec![3989, 3989],
        };
        inventory.craft_job = Some(job.clone());
        inventory.validate(&items).unwrap();
        let restored: Inventory =
            serde_json::from_slice(&serde_json::to_vec(&inventory).unwrap()).unwrap();
        let result = restored
            .craft_job
            .as_ref()
            .unwrap()
            .complete(&restored, &skills, &items)
            .unwrap();
        assert_eq!(
            result
                .cargo
                .iter()
                .flatten()
                .map(|i| i.quantity)
                .sum::<u32>(),
            2
        );
        let mut invalid = job.clone();
        invalid.recovered_components = vec![3989; 4];
        assert!(invalid.validate(&inventory, &items).is_err());
        assert!(invalid.complete(&inventory, &skills, &items).is_err());
        let mut changed = inventory.clone();
        changed.production_target[0].as_mut().unwrap().structure = 0.5;
        assert!(job.complete(&changed, &skills, &items).is_err());
        assert!(inventory.production_target[0].is_some());
    }

    #[test]
    fn repeated_components_free_full_cargo_but_missing_one_or_credits_cannot_partially_consume() {
        let items = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let recipe = CraftRecipe::resolve(2372, &items).unwrap();
        assert_eq!(recipe.requirements()[&3989], 3);
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.credits = 1_000_000;
        inventory.known_formulas.insert(2372);
        inventory.cargo.fill(sample(769));
        for (index, component) in recipe.components.iter().enumerate() {
            inventory.cargo[index] = sample(*component);
        }
        let result = recipe
            .manufacture(&inventory, &skills, &items, "TestBuilder")
            .unwrap();
        assert_eq!(
            result
                .cargo
                .iter()
                .flatten()
                .filter(|item| item.template == 2372)
                .count(),
            1
        );
        assert_eq!(
            result
                .cargo
                .iter()
                .flatten()
                .filter(|item| item.template == 3989)
                .count(),
            0
        );
        inventory.cargo[4] = sample(769);
        let before = inventory.clone();
        assert!(recipe
            .manufacture(&inventory, &skills, &items, "TestBuilder")
            .is_err());
        assert_eq!(inventory, before);
        inventory.cargo[4] = sample(3989);
        inventory.credits = recipe.cost - 1;
        let before = inventory.clone();
        assert!(recipe
            .manufacture(&inventory, &skills, &items, "TestBuilder")
            .is_err());
        assert_eq!(inventory, before);
    }

    #[test]
    fn pending_analysis_freezes_recipe_and_sample_and_failed_outcome_cannot_learn() {
        let items = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let recipe = CraftRecipe::resolve(1758, &items).unwrap();
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.credits = 1_000_000;
        inventory.production_target[0] = sample(1758);
        let request = ProductionRequest {
            epoch: 7,
            sequence: 100,
        };
        inventory.last_production_request = Some(request);
        let job = CraftJob {
            station: 10761,
            operation: CraftOperation::Analyze,
            recipe: recipe.clone(),
            due_ms: 1,
            request,
            sample: sample(1758),
            analysis_succeeded: false,
            builder_name: String::new(),
            recovered_components: Vec::new(),
        };
        inventory.craft_job = Some(job.clone());
        inventory.validate(&items).unwrap();
        let failed = job.complete(&inventory, &skills, &items).unwrap();
        assert!(failed.craft_job.is_none());
        assert!(failed.known_formulas.is_empty());
        assert!(failed.production_target[0].is_none());
        assert_eq!(failed.credits, inventory.credits - recipe.cost);
        let mut changed = job.clone();
        changed.recipe.components.reverse();
        assert!(changed.complete(&inventory, &skills, &items).is_err());
        let mut different = inventory.clone();
        different.production_target[0].as_mut().unwrap().quality = 1.1;
        assert!(job.complete(&different, &skills, &items).is_err());
        assert!(inventory.production_target[0].is_some());
        assert_eq!(inventory.credits, 1_000_000);
    }

    #[test]
    fn legacy_inventory_request_field_loads_and_new_analysis_state_is_optional() {
        let items = ItemCatalog::load().unwrap();
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.last_production_request = Some(ProductionRequest {
            epoch: 9,
            sequence: 20,
        });
        let mut json = serde_json::to_value(&inventory).unwrap();
        let object = json.as_object_mut().unwrap();
        let request = object.remove("last_production_request").unwrap();
        object.insert("last_refine_request".into(), request);
        for key in ["known_formulas", "production_target", "craft_job"] {
            object.remove(key);
        }
        for container in ["cargo", "equipped", "vault"] {
            for item in object
                .get_mut(container)
                .unwrap()
                .as_array_mut()
                .unwrap()
                .iter_mut()
            {
                if let Some(instance) = item.as_object_mut() {
                    instance.remove("builder_name");
                }
            }
        }
        let restored: Inventory = serde_json::from_value(json).unwrap();
        restored.validate(&items).unwrap();
        assert_eq!(restored, inventory);
    }

    #[test]
    fn learning_consumes_owned_sample_and_fee_and_survives_serialization() {
        let items = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let recipe = CraftRecipe::resolve(1758, &items).unwrap();
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.credits = 1_000_000;
        assert!(recipe.analyze(&inventory, &skills, &items, true).is_err());
        inventory.production_target[0] = sample(1758);
        let failed = recipe.analyze(&inventory, &skills, &items, false).unwrap();
        assert!(!failed.known_formulas.contains(&1758));
        assert!(failed.production_target[0].is_none());
        let learned = recipe.analyze(&inventory, &skills, &items, true).unwrap();
        assert!(learned.known_formulas.contains(&1758));
        assert!(learned.production_target[0].is_none());
        assert_eq!(learned.credits, inventory.credits - recipe.cost);
        let restored: Inventory =
            serde_json::from_slice(&serde_json::to_vec(&learned).unwrap()).unwrap();
        assert_eq!(restored, learned);
        assert!(inventory.production_target[0].is_some());
    }

    #[test]
    fn manufacture_requires_knowledge_materials_credits_and_skill_without_partial_changes() {
        let items = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let recipe = CraftRecipe::resolve(1758, &items).unwrap();
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.credits = 1_000_000;
        for (index, component) in recipe.components.iter().enumerate() {
            inventory.cargo[index] = sample(*component);
        }
        assert!(recipe
            .manufacture(&inventory, &skills, &items, "TestBuilder")
            .is_err());
        inventory.known_formulas.insert(1758);
        let built = recipe
            .manufacture(&inventory, &skills, &items, "TestBuilder")
            .unwrap();
        assert_eq!(built.credits, inventory.credits - recipe.cost);
        assert_eq!(built.trade_xp, 50);
        assert_eq!(
            built
                .cargo
                .iter()
                .flatten()
                .map(|i| i.template)
                .collect::<Vec<_>>(),
            vec![1758]
        );
        assert_eq!(
            inventory.cargo.iter().flatten().count(),
            recipe.components.len()
        );
        inventory.cargo[0] = None;
        let original = inventory.clone();
        assert!(recipe
            .manufacture(&inventory, &skills, &items, "TestBuilder")
            .is_err());
        assert_eq!(inventory, original);
        assert!(CraftRecipe::resolve(1180, &items)
            .unwrap()
            .check_skill(&skills, &items)
            .is_err());
        assert_eq!(
            (build_ceiling(5), build_ceiling(6), build_ceiling(7)),
            (5, 7, 9)
        );
    }
}
