//! Refinery recipes and all-or-nothing material/credit/product transactions.
//! Repeated component IDs are quantities, not duplicate rows to discard.
use crate::inventory::Inventory;
use crate::items::ItemCatalog;
use crate::production::ProductionRequest;
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io;
use std::sync::{Arc, OnceLock};

pub const REFINE_PRICE: u64 = 150;
pub const REFINE_BASE_PRICE: u64 = 200;
pub const REFINE_DELAY_MS: u64 = 3000;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RefineRecipe {
    pub input: u32,
    pub input_quantity: u32,
    pub output: u32,
}

#[derive(Clone)]
pub struct RefineryCatalog(Arc<BTreeMap<u32, RefineRecipe>>);

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RefineJob {
    pub station: u32,
    pub product: u32,
    pub iterations: u32,
    pub unit_cost: u64,
    pub due_ms: u64,
    pub request: ProductionRequest,
    /// Older development jobs lacked a recipe snapshot. They can load but
    /// are cancelled without charge rather than guessing their input contract.
    #[serde(default)]
    pub recipe: Option<RefineRecipe>,
}

impl RefineryCatalog {
    pub fn load() -> io::Result<Self> {
        static CATALOG: OnceLock<Arc<BTreeMap<u32, RefineRecipe>>> = OnceLock::new();
        if let Some(catalog) = CATALOG.get() {
            return Ok(Self(Arc::clone(catalog)));
        }
        let items = ItemCatalog::load()?;
        let mut recipes = BTreeMap::new();
        for input in items.iter().filter(|item| item.refines_into != 0) {
            let output = items.get(input.refines_into)?;
            if !(1..=9).contains(&output.level)
                || output.components.is_empty()
                || output.components.iter().any(|id| *id != input.id)
                || output.id == input.id
            {
                return Err(invalid("Invalid homogeneous refinery recipe."));
            }
            if recipes
                .insert(
                    output.id,
                    RefineRecipe {
                        input: input.id,
                        input_quantity: output.components.len() as u32,
                        output: output.id,
                    },
                )
                .is_some()
            {
                return Err(invalid("Ambiguous refinery output recipe."));
            }
        }
        let _ = CATALOG.set(Arc::new(recipes));
        Ok(Self(Arc::clone(CATALOG.get().unwrap())))
    }
    pub fn get(&self, output: u32) -> io::Result<&RefineRecipe> {
        self.0
            .get(&output)
            .ok_or_else(|| invalid("Item is not a known refinery product."))
    }
    pub fn available(&self, inventory: &Inventory) -> Vec<&RefineRecipe> {
        self.0
            .values()
            .filter(|recipe| {
                inventory
                    .cargo
                    .iter()
                    .flatten()
                    .any(|item| item.template == recipe.input)
            })
            .collect()
    }
}

impl RefineRecipe {
    pub fn count_input(&self, inventory: &Inventory) -> u32 {
        inventory
            .cargo
            .iter()
            .flatten()
            .filter(|item| item.template == self.input)
            .map(|item| item.quantity)
            .sum()
    }
    pub fn apply(
        &self,
        inventory: &Inventory,
        iterations: u32,
        unit_cost: u64,
        items: &ItemCatalog,
    ) -> io::Result<Inventory> {
        inventory.validate(items)?;
        if iterations == 0 || self.input_quantity == 0 || self.input == self.output {
            return Err(invalid("Invalid refinery quantity or recipe."));
        }
        let needed = self
            .input_quantity
            .checked_mul(iterations)
            .ok_or_else(|| invalid("Refine quantity overflow."))?;
        if self.count_input(inventory) < needed {
            return Err(invalid("Missing refinery components."));
        }
        let cost = unit_cost
            .checked_mul(u64::from(iterations))
            .ok_or_else(|| invalid("Refine price overflow."))?;
        let mut candidate = inventory.clone();
        candidate.credits = candidate
            .credits
            .checked_sub(cost)
            .ok_or_else(|| invalid("Insufficient refinery credits."))?;
        let mut remaining = needed;
        for slot in &mut candidate.cargo {
            if let Some(item) = slot {
                if item.template == self.input {
                    let consumed = remaining.min(item.quantity);
                    item.quantity -= consumed;
                    remaining -= consumed;
                    if item.quantity == 0 {
                        *slot = None;
                    }
                }
            }
        }
        // Admission runs AFTER consumption: a full hull can refine if consuming
        // ore frees enough space. On any failure the original stays unchanged.
        let mut candidate =
            candidate.received(self.output, iterations, u32::MAX as usize, items)?;
        candidate.trade_xp = candidate
            .trade_xp
            .checked_add(u64::from(items.get(self.output)?.level) * 50 * u64::from(iterations))
            .ok_or_else(|| invalid("Trade XP overflow."))?;
        Ok(candidate)
    }
    pub fn max_iterations(&self, inventory: &Inventory, items: &ItemCatalog) -> u32 {
        if self.input_quantity == 0 {
            return 0;
        }
        let inputs = self.count_input(inventory) / self.input_quantity;
        let credits = (inventory.credits / REFINE_PRICE).min(u64::from(u32::MAX)) as u32;
        let upper = inputs.min(credits);
        // Capacity is not monotonic while consumption gradually frees slots.
        // For each candidate final cargo slot boundary, test the real atomic
        // transaction; the largest material/credit bound is the first candidate.
        if upper > 0 && self.apply(inventory, upper, REFINE_PRICE, items).is_ok() {
            return upper;
        }
        let output = match items.get(self.output) {
            Ok(item) => item,
            Err(_) => return 0,
        };
        let existing_capacity: u32 = inventory
            .cargo
            .iter()
            .map(|slot| match slot {
                None => output.max_stack,
                Some(item)
                    if item.template == self.output
                        && item.quality == 1.
                        && item.structure == 1.
                        && item.builder_name.is_empty() =>
                {
                    output.max_stack - item.quantity
                }
                _ => 0,
            })
            .sum();
        let mut candidates = vec![upper.min(existing_capacity)];
        for freed in 1..=crate::inventory::CARGO_SLOTS as u32 {
            candidates.push(
                upper.min(existing_capacity.saturating_add(freed.saturating_mul(output.max_stack))),
            );
        }
        candidates
            .into_iter()
            .filter(|count| {
                *count > 0 && self.apply(inventory, *count, REFINE_PRICE, items).is_ok()
            })
            .max()
            .unwrap_or(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inventory::ItemInstance;

    fn item(template: u32, quantity: u32) -> Option<ItemInstance> {
        Some(ItemInstance {
            template,
            quantity,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        })
    }

    #[test]
    fn public_links_preserve_five_ore_recipes_and_atomic_cost_products_xp() {
        let catalog = RefineryCatalog::load().unwrap();
        let items = ItemCatalog::load().unwrap();
        assert_eq!(catalog.0.len(), 207);
        let recipe = catalog.get(3375).unwrap();
        assert_eq!((recipe.input, recipe.input_quantity), (2842, 5));
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.cargo[0] = item(2842, 3);
        inventory.cargo[1] = item(2842, 7);
        let result = recipe.apply(&inventory, 2, REFINE_PRICE, &items).unwrap();
        assert_eq!(result.credits, inventory.credits - 300);
        assert_eq!(result.trade_xp, 100);
        assert_eq!(recipe.count_input(&result), 0);
        assert_eq!(result.cargo[0].as_ref().unwrap().template, 3375);
        assert_eq!(result.cargo[0].as_ref().unwrap().quantity, 2);
        assert_eq!(recipe.count_input(&inventory), 10);
        assert!(recipe.apply(&inventory, 3, REFINE_PRICE, &items).is_err());
        inventory.credits = 149;
        assert!(recipe.apply(&inventory, 1, REFINE_PRICE, &items).is_err());
        assert_eq!(recipe.max_iterations(&inventory, &items), 0);
    }

    #[test]
    fn stack_limit_matches_brute_force_when_consumption_frees_slots_discontinuously() {
        let catalog = RefineryCatalog::load().unwrap();
        let recipe = catalog.get(3375).unwrap();
        let items = ItemCatalog::load().unwrap();
        let mut inventory = Inventory::starter(&items).unwrap();
        inventory.credits = 1_000_000;
        inventory.cargo.fill(item(769, 1));
        for quantity in (1..=300).step_by(7) {
            for existing in [1, 59, 60] {
                inventory.cargo[0] = item(2842, quantity);
                inventory.cargo[1] = item(3375, existing);
                let expected = (1..=quantity / 5)
                    .filter(|count| {
                        recipe
                            .apply(&inventory, *count, REFINE_PRICE, &items)
                            .is_ok()
                    })
                    .max()
                    .unwrap_or(0);
                assert_eq!(
                    recipe.max_iterations(&inventory, &items),
                    expected,
                    "input={quantity}, product={existing}"
                );
            }
        }
    }

    #[test]
    fn request_order_handles_duplicate_stale_wrap_and_new_transport_epochs() {
        let old = ProductionRequest {
            epoch: 1,
            sequence: u32::MAX - 1,
        };
        assert!(!old.follows(Some(old)));
        assert!(!ProductionRequest {
            sequence: u32::MAX - 2,
            ..old
        }
        .follows(Some(old)));
        assert!(ProductionRequest { sequence: 0, ..old }.follows(Some(old)));
        assert!(ProductionRequest {
            epoch: 2,
            sequence: 0
        }
        .follows(Some(old)));
    }
}
