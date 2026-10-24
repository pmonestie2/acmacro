//! Owned item instances, independent of their shared public templates.
//! Moves are validated on a copy, so rejected requests cannot lose/duplicate
//! cargo. The session persists that copy before publishing the accepted change.
use crate::items::{ItemCatalog, ItemTemplate};
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io;

pub const CARGO_SLOTS: usize = 22;
pub const EQUIPMENT_SLOTS: usize = 20;
pub const VAULT_SLOTS: usize = 96;

fn empty_ammunition() -> Vec<Option<ItemInstance>> {
    vec![None; EQUIPMENT_SLOTS]
}

fn empty_vault() -> Vec<Option<ItemInstance>> {
    vec![None; VAULT_SLOTS]
}
fn starting_credits() -> u64 {
    10_000
}

fn empty_production_target() -> Vec<Option<ItemInstance>> {
    vec![None]
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ItemInstance {
    pub template: u32,
    pub quantity: u32,
    pub quality: f32,
    pub structure: f32,
    /// Original native AuxItem field 9. Empty means stock/loot, not player-made.
    #[serde(default)]
    pub builder_name: String,
}

impl ItemInstance {
    pub fn stock(template: u32, quantity: u32) -> Self {
        Self {
            template,
            quantity,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        }
    }

    pub fn stacks_with(&self, other: &Self) -> bool {
        self.template == other.template
            && self.quality == other.quality
            && self.structure == other.structure
            && self.builder_name == other.builder_name
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Inventory {
    pub cargo: Vec<Option<ItemInstance>>,
    pub equipped: Vec<Option<ItemInstance>>,
    #[serde(default = "empty_ammunition")]
    pub ammunition: Vec<Option<ItemInstance>>,
    #[serde(default = "empty_vault")]
    pub vault: Vec<Option<ItemInstance>>,
    #[serde(default = "starting_credits")]
    pub credits: u64,
    #[serde(default)]
    pub sandbox_grant: u32,
    #[serde(default)]
    pub shot_sequence: u64,
    #[serde(default)]
    pub pending_launch: Option<crate::projectiles::LaunchJournal>,
    #[serde(default)]
    pub harvests: BTreeMap<u32, crate::resources::HarvestReceipt>,
    #[serde(default)]
    pub explore_xp: u64,
    #[serde(default)]
    pub trade_xp: u64,
    #[serde(default)]
    pub refine_job: Option<crate::refining::RefineJob>,
    #[serde(default)]
    #[serde(alias = "last_refine_request")]
    pub last_production_request: Option<crate::production::ProductionRequest>,
    #[serde(default)]
    pub known_formulas: std::collections::BTreeSet<u32>,
    #[serde(default = "empty_production_target")]
    pub production_target: Vec<Option<ItemInstance>>,
    #[serde(default)]
    pub craft_job: Option<crate::crafting::CraftJob>,
    #[serde(default)]
    pub loot_receipts: BTreeMap<u64, crate::loot_world::LootReceipt>,
    #[serde(default)]
    pub faction_standings: BTreeMap<u32, f32>,
}

#[derive(Clone, Copy, Debug)]
pub struct ItemTransfer {
    pub source: u32,
    pub source_slot: usize,
    pub destination: u32,
    pub destination_slot: usize,
    pub quantity: u32,
}

impl Inventory {
    /// Prepare a receipt and its loot award in one inventory candidate. The
    /// session's ordinary commit path persists this before shared depletion.
    pub fn collected_loot(
        &self,
        collection: &crate::loot_world::LootCollection<'_>,
        templates: &ItemCatalog,
    ) -> io::Result<Self> {
        let receipt = &collection.receipt;
        if !receipt.valid() {
            return Err(invalid("Invalid loot collection receipt."));
        }
        let previous = self.loot_receipts.get(&receipt.key());
        if previous == Some(receipt) {
            return Err(invalid("Loot collection was already committed."));
        }
        let mut added_slots = 0;
        for (index, &taken) in receipt.slots.iter().enumerate() {
            let was_taken = previous.is_some_and(|old| old.slots[index]);
            if was_taken && !taken {
                return Err(invalid("Loot receipt moved backwards."));
            }
            if taken && !was_taken {
                added_slots += 1;
            }
        }
        let credits_were_taken = previous.is_some_and(|old| old.credits);
        if credits_were_taken && (!receipt.credits || collection.credits > 0)
            || added_slots != usize::from(collection.item.is_some())
            || collection.credits > 0 && !receipt.credits
        {
            return Err(invalid("Loot award does not match its receipt changes."));
        }
        let mut candidate = if let Some(item) = &collection.item {
            self.received_instance(item, u32::MAX as usize, templates)?
        } else {
            self.clone()
        };
        candidate.credits = candidate
            .credits
            .checked_add(collection.credits)
            .ok_or_else(|| invalid("Credit balance overflow."))?;
        candidate
            .loot_receipts
            .insert(receipt.key(), receipt.clone());
        candidate.validate(templates)?;
        Ok(candidate)
    }

    /// StaticData.h racial starter templates. The ordinary starter remains the
    /// JE fixture used by protocol tests; real accounts choose their own race.
    pub fn starter_for(
        profile: &crate::account::CharacterProfile,
        templates: &ItemCatalog,
    ) -> io::Result<Self> {
        let mut inventory = Self::starter(templates)?;
        let race = profile.class().race() as usize;
        for (slot, choices) in [
            [3628, 3629, 3627],
            [3184, 3182, 3183],
            [2507, 2505, 2506],
            [770, 769, 771],
        ]
        .iter()
        .enumerate()
        {
            inventory.equipped[slot] = Some(ItemInstance::stock(choices[race], 1));
        }
        inventory.validate(templates)?;
        Ok(inventory)
    }

    pub fn validate_for_profile(
        &self,
        profile: &crate::account::CharacterProfile,
        templates: &ItemCatalog,
        skills: &crate::skills::SkillCatalog,
    ) -> io::Result<()> {
        self.validate(templates)?;
        if self
            .pending_launch
            .as_ref()
            .is_some_and(|journal| journal.owner != profile.game_id)
        {
            return Err(invalid("Foreign launch journal."));
        }
        for (slot, item) in self
            .equipped
            .iter()
            .enumerate()
            .filter_map(|(slot, item)| item.as_ref().map(|item| (slot, item)))
        {
            let template = templates.get(item.template)?;
            profile.class().check_mount(slot, template.item_type)?;
            template.check_requirements(profile, skills, [50, 50, 50])?;
        }
        Ok(())
    }

    pub fn starter(templates: &ItemCatalog) -> io::Result<Self> {
        let mut inventory = Self {
            cargo: vec![None; CARGO_SLOTS],
            equipped: vec![None; EQUIPMENT_SLOTS],
            ammunition: empty_ammunition(),
            vault: empty_vault(),
            credits: starting_credits(),
            sandbox_grant: 0,
            shot_sequence: 0,
            pending_launch: None,
            harvests: BTreeMap::new(),
            explore_xp: 0,
            trade_xp: 0,
            refine_job: None,
            last_production_request: None,
            known_formulas: std::collections::BTreeSet::new(),
            production_target: empty_production_target(),
            craft_job: None,
            loot_receipts: BTreeMap::new(),
            faction_standings: BTreeMap::new(),
        };
        for starting in templates.starting_items()? {
            if starting.slot >= EQUIPMENT_SLOTS || inventory.equipped[starting.slot].is_some() {
                return Err(invalid("invalid starting equipment slot"));
            }
            inventory.equipped[starting.slot] = Some(ItemInstance {
                template: starting.template,
                quantity: 1,
                quality: 1.,
                structure: 1.,
                builder_name: String::new(),
            });
        }
        inventory.validate(templates)?;
        Ok(inventory)
    }

    pub fn templates<'a>(&self, catalog: &'a ItemCatalog) -> io::Result<Vec<&'a ItemTemplate>> {
        let identities: std::collections::BTreeSet<u32> = self
            .cargo
            .iter()
            .chain(&self.equipped)
            .chain(&self.ammunition)
            .chain(&self.vault)
            .chain(&self.production_target)
            .flatten()
            .map(|item| item.template)
            .collect();
        identities.into_iter().map(|id| catalog.get(id)).collect()
    }

    pub fn validate(&self, templates: &ItemCatalog) -> io::Result<()> {
        if !self.faction_standings.is_empty() {
            crate::factions::FactionCatalog::load()?.validate_overrides(&self.faction_standings)?;
        }
        if self
            .pending_launch
            .as_ref()
            .is_some_and(|journal| !journal.valid() || journal.sequence != self.shot_sequence)
        {
            return Err(invalid("Invalid weapon launch journal."));
        }
        if self.known_formulas.len() > 500
            || self.production_target.len() != 1
            || self
                .production_target
                .iter()
                .flatten()
                .any(|item| item.quantity != 1)
            || self.refine_job.is_some() && self.craft_job.is_some()
        {
            return Err(invalid("Invalid production state."));
        }
        for product in &self.known_formulas {
            templates.get(*product)?;
        }
        if let Some(job) = &self.craft_job {
            job.validate(self, templates)?;
        }
        if self
            .last_production_request
            .is_some_and(|request| request.epoch == 0)
        {
            return Err(invalid("Invalid refinery request epoch."));
        }
        if let Some(job) = &self.refine_job {
            templates.get(job.product)?;
            if let Some(recipe) = &job.recipe {
                templates.get(recipe.input)?;
                if recipe.output != job.product
                    || recipe.input == recipe.output
                    || !(1..=6).contains(&recipe.input_quantity)
                {
                    return Err(invalid("Invalid pending refinery recipe."));
                }
            }
            if !(10000..20000).contains(&job.station)
                || job.iterations == 0
                || job.iterations > 22_000_000
                || job.unit_cost != crate::refining::REFINE_PRICE
                || job.due_ms == 0
                || job.request.epoch == 0
                || self.last_production_request != Some(job.request)
            {
                return Err(invalid("Invalid refinery job."));
            }
        }
        if self.loot_receipts.len() > 50_000
            || self
                .loot_receipts
                .iter()
                .any(|(key, receipt)| !receipt.valid() || *key != receipt.key())
        {
            return Err(invalid("Invalid or excessive loot receipts."));
        }
        if self.cargo.len() != CARGO_SLOTS
            || self.equipped.len() != EQUIPMENT_SLOTS
            || self.ammunition.len() != EQUIPMENT_SLOTS
            || self.vault.len() != VAULT_SLOTS
        {
            return Err(invalid("invalid inventory dimensions"));
        }
        if self.harvests.len() > 50_000
            || self.harvests.iter().any(|(id, receipt)| {
                !(0x20000000..0x40000000).contains(id)
                    || !(1..10000).contains(&receipt.sector)
                    || receipt.completed_at_ms == 0
                    || receipt
                        .quantities
                        .iter()
                        .any(|quantity| *quantity > 1_000_000)
            })
        {
            return Err(invalid("invalid resource harvest receipts"));
        }
        for item in self
            .cargo
            .iter()
            .chain(&self.equipped)
            .chain(&self.ammunition)
            .chain(&self.vault)
            .chain(&self.production_target)
            .flatten()
        {
            let template = templates.get(item.template)?;
            if item.quantity == 0
                || item.quantity > template.max_stack
                || !item.quality.is_finite()
                || !(0.01..=2.).contains(&item.quality)
                || !item.structure.is_finite()
                || !(0.0..=1.).contains(&item.structure)
                || item.builder_name.len() > 63
                || !item.builder_name.is_ascii()
                || item
                    .builder_name
                    .bytes()
                    .any(|byte| byte < 32 || byte == 127)
            {
                return Err(invalid("invalid owned item instance"));
            }
        }
        for (slot, item) in self.equipped.iter().enumerate() {
            if let Some(item) = item {
                Self::can_equip(item, slot, templates)?;
            }
        }
        for (slot, item) in self.ammunition.iter().enumerate() {
            if let Some(item) = item {
                self.check_ammunition(slot, item, templates)?;
            }
        }
        for item in self.vault.iter().flatten() {
            Self::can_store(item, templates)?;
        }
        Ok(())
    }

    fn can_equip(item: &ItemInstance, slot: usize, templates: &ItemCatalog) -> io::Result<()> {
        let template = templates.get(item.template)?;
        let allowed = match template.item_type {
            2 => slot == 0,
            7 => slot == 1,
            6 => slot == 2,
            14..=16 => (3..=8).contains(&slot),
            11 => (9..=14).contains(&slot),
            _ => false,
        };
        if !allowed || item.quantity != 1 {
            return Err(invalid("item does not fit that equipment mount"));
        }
        Ok(())
    }

    fn slots(&self, container: u32) -> io::Result<&[Option<ItemInstance>]> {
        match container {
            1 => Ok(&self.cargo),
            2 => Ok(&self.equipped),
            3 => Ok(&self.vault),
            12 => Ok(&self.production_target),
            _ => Err(invalid("this inventory container is not implemented yet")),
        }
    }

    fn set(&mut self, container: u32, slot: usize, value: Option<ItemInstance>) {
        match container {
            1 => self.cargo[slot] = value,
            2 => self.equipped[slot] = value,
            3 => self.vault[slot] = value,
            12 => self.production_target[slot] = value,
            _ => unreachable!("container checked before mutation"),
        }
    }

    pub fn moved(&self, transfer: ItemTransfer, templates: &ItemCatalog) -> io::Result<Self> {
        let ItemTransfer {
            source,
            source_slot,
            destination,
            destination_slot,
            quantity,
        } = transfer;
        self.validate(templates)?;
        if !matches!(
            (source, destination),
            (1, 1) | (1, 2) | (1, 3) | (2, 1) | (2, 2) | (3, 1) | (3, 3) | (1, 12) | (12, 1)
        ) {
            return Err(invalid("unsupported inventory transfer path"));
        }
        let source_item = self
            .slots(source)?
            .get(source_slot)
            .ok_or_else(|| invalid("source inventory slot out of range"))?
            .clone()
            .ok_or_else(|| invalid("source inventory slot is empty"))?;
        let destination_item = self
            .slots(destination)?
            .get(destination_slot)
            .ok_or_else(|| invalid("destination inventory slot out of range"))?
            .clone();
        if quantity == 0 || quantity > source_item.quantity {
            return Err(invalid("invalid transfer quantity"));
        }
        if source == 1 && destination == 2 && templates.get(source_item.template)?.item_type == 10 {
            return self.loaded_ammunition(source_slot, destination_slot, quantity, templates);
        }
        if destination == 2 {
            Self::can_equip(&source_item, destination_slot, templates)?;
        }
        if source == 2 {
            if let Some(item) = &destination_item {
                Self::can_equip(item, source_slot, templates)?;
            }
        }
        if destination == 3 {
            Self::can_store(&source_item, templates)?;
        }
        if source == 3 {
            if let Some(item) = &destination_item {
                Self::can_store(item, templates)?;
            }
        }
        let mut result = self.clone();
        if source == destination && source_slot == destination_slot {
            return Ok(result);
        }
        let mut remaining = source_item.clone();
        let mut arriving = source_item.clone();
        match destination_item {
            None => {
                arriving.quantity = quantity;
                remaining.quantity -= quantity;
                result.set(destination, destination_slot, Some(arriving));
                result.set(
                    source,
                    source_slot,
                    (remaining.quantity > 0).then_some(remaining),
                );
            }
            Some(mut target)
                if source != 2 && destination != 2 && target.stacks_with(&source_item) =>
            {
                let capacity = templates.get(target.template)?.max_stack - target.quantity;
                let moved = quantity.min(capacity);
                target.quantity += moved;
                remaining.quantity -= moved;
                result.set(destination, destination_slot, Some(target));
                result.set(
                    source,
                    source_slot,
                    (remaining.quantity > 0).then_some(remaining),
                );
            }
            Some(target) => {
                if quantity != source_item.quantity {
                    return Err(invalid("cannot swap only part of an item stack"));
                }
                result.set(source, source_slot, Some(target));
                result.set(destination, destination_slot, Some(source_item));
            }
        }
        result.reconcile_ammunition(self, transfer, templates)?;
        result.validate(templates)?;
        Ok(result)
    }

    fn can_store(item: &ItemInstance, catalog: &ItemCatalog) -> io::Result<()> {
        let template = catalog.get(item.template)?;
        if template.category == 90 || template.flags & 8 != 0 {
            return Err(invalid("this item cannot be stored in the vault"));
        }
        Ok(())
    }

    /// Apply the whole purchase to a copy. Stack capacity and credits are
    /// checked before publication, including purchases spanning several slots.
    pub fn purchased(
        &self,
        template: u32,
        quantity: u32,
        price: u64,
        preferred_slot: usize,
        catalog: &ItemCatalog,
    ) -> io::Result<Self> {
        if price == 0 {
            return Err(invalid("Invalid purchase price."));
        }
        let cost = price
            .checked_mul(u64::from(quantity))
            .ok_or_else(|| invalid("Purchase cost overflow."))?;
        let credits = self
            .credits
            .checked_sub(cost)
            .ok_or_else(|| invalid("Not enough credits."))?;
        let mut result = self.received(template, quantity, preferred_slot, catalog)?;
        result.credits = credits;
        Ok(result)
    }

    /// All-or-nothing admission for newly obtained normal-quality items. Used
    /// by purchases and mining; never overwrites or silently drops old cargo.
    pub fn received(
        &self,
        template: u32,
        quantity: u32,
        preferred_slot: usize,
        catalog: &ItemCatalog,
    ) -> io::Result<Self> {
        self.received_instance(
            &ItemInstance::stock(template, quantity),
            preferred_slot,
            catalog,
        )
    }

    /// Preserve quality, damage and maker through admission and stack merging.
    pub fn received_instance(
        &self,
        instance: &ItemInstance,
        preferred_slot: usize,
        catalog: &ItemCatalog,
    ) -> io::Result<Self> {
        self.validate(catalog)?;
        let item = catalog.get(instance.template)?;
        let quantity = instance.quantity;
        if quantity == 0 || (preferred_slot != u32::MAX as usize && preferred_slot >= CARGO_SLOTS) {
            return Err(invalid("Invalid cargo quantity or destination slot."));
        }
        let mut result = self.clone();
        let mut remaining = quantity;
        let mut order: Vec<usize> = (0..CARGO_SLOTS).collect();
        if preferred_slot < CARGO_SLOTS {
            order.swap(0, preferred_slot);
        }
        // Merge existing identical stock before filling empty slots. Buying
        // never overwrites unrelated cargo or merges damaged/modified items.
        for &slot in &order {
            if let Some(owned) = &mut result.cargo[slot] {
                if owned.stacks_with(instance) {
                    let moved = remaining.min(item.max_stack - owned.quantity);
                    owned.quantity += moved;
                    remaining -= moved;
                }
            }
        }
        for slot in order {
            if remaining == 0 {
                break;
            }
            if result.cargo[slot].is_none() {
                let moved = remaining.min(item.max_stack);
                let mut arriving = instance.clone();
                arriving.quantity = moved;
                result.cargo[slot] = Some(arriving);
                remaining -= moved;
            }
        }
        if remaining != 0 {
            return Err(invalid(
                "Not enough cargo space for the entire item quantity.",
            ));
        }
        result.validate(catalog)?;
        Ok(result)
    }

    /// Existing matching cargo is sellable regardless of how it was acquired.
    /// The service supplies the current NPC quote, never a client-supplied price.
    pub fn sold(
        &self,
        slot: usize,
        quantity: u32,
        price: u64,
        catalog: &ItemCatalog,
    ) -> io::Result<Self> {
        self.validate(catalog)?;
        let item = self
            .cargo
            .get(slot)
            .and_then(Option::as_ref)
            .ok_or_else(|| invalid("Sale cargo slot is empty or invalid."))?;
        if quantity == 0
            || quantity > item.quantity
            || price == 0
            || catalog.get(item.template)?.level > 9
        {
            return Err(invalid(
                "Invalid sale quantity or this NPC does not buy the item.",
            ));
        }
        let payment = price
            .checked_mul(u64::from(quantity))
            .ok_or_else(|| invalid("Sale payment overflow."))?;
        let credits = self
            .credits
            .checked_add(payment)
            .ok_or_else(|| invalid("Credit balance overflow."))?;
        let mut result = self.clone();
        let item = result.cargo[slot].as_mut().unwrap();
        item.quantity -= quantity;
        if item.quantity == 0 {
            result.cargo[slot] = None;
        }
        result.credits = credits;
        result.validate(catalog)?;
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::items::ItemCatalog;

    #[test]
    fn purchases_and_sales_are_atomic_and_stack_without_overwriting_cargo() {
        let catalog = ItemCatalog::load().unwrap();
        let inventory = Inventory::starter(&catalog).unwrap();
        let bought = inventory
            .purchased(507, 75, 3, u32::MAX as usize, &catalog)
            .unwrap();
        assert_eq!(bought.cargo[0].as_ref().unwrap().quantity, 50);
        assert_eq!(bought.cargo[1].as_ref().unwrap().quantity, 25);
        assert_eq!(bought.credits, inventory.credits - 225);
        let sold = bought.sold(0, 17, 2, &catalog).unwrap();
        assert_eq!(sold.cargo[0].as_ref().unwrap().quantity, 33);
        assert_eq!(sold.credits, bought.credits + 34);
        assert_eq!(inventory.cargo, vec![None; CARGO_SLOTS]);
        assert!(bought.sold(0, 51, 2, &catalog).is_err());
        assert!(bought.sold(0, 1, 0, &catalog).is_err());
        assert!(inventory
            .purchased(507, u32::MAX, u64::MAX, 0, &catalog)
            .is_err());
        assert!(inventory
            .purchased(507, 1, 1, CARGO_SLOTS, &catalog)
            .is_err());
        let mut full = inventory.clone();
        full.credits = u64::MAX;
        full.cargo.fill(Some(ItemInstance {
            template: 2505,
            quantity: 1,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        }));
        let before = full.clone();
        assert!(full.purchased(507, 1, 1, 0, &catalog).is_err());
        assert_eq!(full, before);
        assert!(full.sold(0, 1, 1, &catalog).is_err()); // credit overflow
        assert_eq!(full, before);
    }

    #[test]
    fn stack_split_merge_and_vault_moves_conserve_items_and_preserve_restrictions() {
        let catalog = ItemCatalog::load().unwrap();
        let mut inventory = Inventory::starter(&catalog).unwrap();
        let ammo = ItemInstance {
            template: 507,
            quantity: 30,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        };
        assert_eq!(catalog.get(507).unwrap().max_stack, 50);
        inventory.cargo[0] = Some(ammo.clone());
        inventory.vault[0] = Some(ItemInstance {
            quantity: 40,
            ..ammo
        });
        inventory = moved(&inventory, (1, 0, 1, 1, 10), &catalog).unwrap();
        assert_eq!(inventory.cargo[0].as_ref().unwrap().quantity, 20);
        assert_eq!(inventory.cargo[1].as_ref().unwrap().quantity, 10);
        inventory = moved(&inventory, (1, 0, 3, 0, 20), &catalog).unwrap();
        assert_eq!(inventory.cargo[0].as_ref().unwrap().quantity, 10);
        assert_eq!(inventory.vault[0].as_ref().unwrap().quantity, 50);
        inventory = moved(&inventory, (3, 0, 1, 2, 50), &catalog).unwrap();
        assert!(inventory.vault[0].is_none());
        assert_eq!(
            inventory
                .cargo
                .iter()
                .flatten()
                .map(|item| item.quantity)
                .sum::<u32>(),
            70
        );
        let before = inventory.clone();
        for request in [
            (1, 2, 3, 96, 1),
            (1, 0, 1, 1, u32::MAX),
            (1, 0, 1, 1, 0),
            (1, 0, 2, 3, 1),
        ] {
            assert!(moved(&inventory, request, &catalog).is_err());
            assert_eq!(inventory, before);
        }
        for template in [3985, 848] {
            // trade cargo and no-store equipment
            inventory.cargo[3] = Some(ItemInstance {
                template,
                quantity: 1,
                quality: 1.,
                structure: 1.,
                builder_name: String::new(),
            });
            assert!(moved(&inventory, (1, 3, 3, 0, 1), &catalog).is_err());
        }
        let legacy = serde_json::to_value(&before).unwrap();
        let mut legacy = legacy.as_object().unwrap().clone();
        legacy.remove("vault");
        legacy.remove("credits");
        let migrated: Inventory = serde_json::from_value(legacy.into()).unwrap();
        assert_eq!(migrated.vault.len(), 96);
        assert_eq!(migrated.credits, 10_000);
        migrated.validate(&catalog).unwrap();
    }

    #[test]
    fn makers_survive_moves_and_stock_admission_cannot_erase_a_crafted_stack() {
        let catalog = ItemCatalog::load().unwrap();
        let inventory = Inventory::starter(&catalog).unwrap();
        let mut built = ItemInstance::stock(507, 20);
        built.builder_name = "OriginalMaker".into();
        let crafted = inventory
            .received_instance(&built, u32::MAX as usize, &catalog)
            .unwrap();
        let mut mixed = crafted
            .received(507, 5, u32::MAX as usize, &catalog)
            .unwrap();
        assert_eq!(mixed.cargo[0].as_ref().unwrap().quantity, 20);
        assert_eq!(mixed.cargo[1].as_ref().unwrap().quantity, 5);
        mixed = moved(&mixed, (1, 0, 3, 0, 10), &catalog).unwrap();
        assert_eq!(
            mixed.vault[0].as_ref().unwrap().builder_name,
            "OriginalMaker"
        );
        mixed = moved(&mixed, (3, 0, 1, 0, 10), &catalog).unwrap();
        assert_eq!(mixed.cargo[0].as_ref().unwrap().quantity, 20);
        assert!(moved(&mixed, (1, 0, 1, 1, 1), &catalog).is_err());
        let mut other = built.clone();
        other.builder_name = "OtherMaker".into();
        mixed = mixed
            .received_instance(&other, u32::MAX as usize, &catalog)
            .unwrap();
        assert_eq!(mixed.cargo[2].as_ref().unwrap().builder_name, "OtherMaker");
        let restored: Inventory =
            serde_json::from_slice(&serde_json::to_vec(&mixed).unwrap()).unwrap();
        assert_eq!(restored, mixed);
        let mut invalid = built;
        invalid.builder_name = "Bad\0Name".into();
        assert!(inventory
            .received_instance(&invalid, u32::MAX as usize, &catalog)
            .is_err());
    }

    fn moved(
        inventory: &Inventory,
        request: (u32, usize, u32, usize, u32),
        templates: &ItemCatalog,
    ) -> io::Result<Inventory> {
        inventory.moved(
            ItemTransfer {
                source: request.0,
                source_slot: request.1,
                destination: request.2,
                destination_slot: request.3,
                quantity: request.4,
            },
            templates,
        )
    }

    #[test]
    fn unequip_swap_and_re_equip_preserve_instances_and_reject_wrong_mounts() {
        let templates = ItemCatalog::load().unwrap();
        let original = Inventory::starter(&templates).unwrap();
        let mut cargo = moved(&original, (2, 2, 1, 0, 1), &templates).unwrap();
        assert!(cargo.equipped[2].is_none());
        assert_eq!(cargo.cargo[0].as_ref().unwrap().template, 2505);
        let before = cargo.clone();
        for request in [
            (1, 0, 2, 0, 1),
            (1, 0, 2, 2, 2),
            (1, 0, 1, 22, 1),
            (3, 0, 1, 0, 1),
        ] {
            assert!(moved(&cargo, request, &templates).is_err());
            assert_eq!(cargo, before);
        }
        cargo = moved(&cargo, (1, 0, 2, 2, 1), &templates).unwrap();
        assert_eq!(cargo, original);
        let swapped = moved(&cargo, (2, 3, 2, 4, 1), &templates).unwrap();
        assert!(swapped.equipped[3].is_none());
        assert_eq!(swapped.equipped[4], original.equipped[3]);
        assert!(moved(&swapped, (2, 4, 2, 1, 1), &templates).is_err());
    }
}
