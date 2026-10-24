//! Public MOB drops and reproducible corpse contents, separate from player cargo.
//!
//! SQL chances are percentages. The old C++ loader's unsigned-char conversion
//! and comparison with a 0..1 roll discarded fractions and made most drops
//! unconditional; this implementation retains percentages, including fractions.
use crate::inventory::ItemInstance;
use crate::items::ItemCatalog;
use crate::protocol::invalid;
use crate::world_random::WorldRandom;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::io;
use std::sync::{Arc, OnceLock};

pub const LOOT_SLOTS: usize = 6;
pub const CORPSE_LIFETIME_MS: u64 = 300_000;
pub const PRIVATE_LOOT_MS: u64 = 150_000;

#[derive(Clone, Debug, Deserialize)]
pub struct DropEntry {
    pub source_id: u32,
    pub item: u32,
    pub chance_percent: f64,
    pub quantity: u32,
    pub kind: u8,
    /// Raw equipment-use metadata; the dump contains values above 100. It is
    /// independent of the validated loot probability and must not disable it.
    pub usage_chance: u32,
}

#[derive(Clone, Debug, Deserialize)]
pub struct MobDrops {
    pub id: u32,
    pub kind: Option<u8>,
    pub drops: Vec<DropEntry>,
}

#[derive(Deserialize)]
struct CatalogFile {
    mobs: Vec<MobDrops>,
}

#[derive(Clone)]
pub struct LootCatalog(Arc<HashMap<u32, MobDrops>>);

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CorpseContents {
    pub asset: u16,
    pub items: [Option<ItemInstance>; LOOT_SLOTS],
    pub credits: u64,
}

impl LootCatalog {
    pub fn load() -> io::Result<Self> {
        static CATALOG: OnceLock<Arc<HashMap<u32, MobDrops>>> = OnceLock::new();
        if let Some(catalog) = CATALOG.get() {
            return Ok(Self(Arc::clone(catalog)));
        }
        let source: CatalogFile = serde_json::from_str(include_str!("../data/loot.json"))?;
        let catalog = Self::from_mobs(source.mobs, &ItemCatalog::load()?)?;
        let _ = CATALOG.set(catalog.0);
        Ok(Self(Arc::clone(CATALOG.get().unwrap())))
    }

    pub(crate) fn from_mobs(mobs: Vec<MobDrops>, items: &ItemCatalog) -> io::Result<Self> {
        let mut result = HashMap::new();
        let mut rows = HashSet::new();
        for mob in mobs {
            if mob.id == 0 || mob.kind.is_some_and(|kind| kind > 6) || mob.drops.len() > 1024 {
                return Err(invalid("Invalid MOB drop definition."));
            }
            for drop in &mob.drops {
                items.get(drop.item)?;
                if !rows.insert(drop.source_id)
                    || drop.source_id == 0
                    || !drop.chance_percent.is_finite()
                    || !(0.0..=100.).contains(&drop.chance_percent)
                    || !(1..=255).contains(&drop.quantity)
                    || drop.kind > 1
                {
                    return Err(invalid("Invalid or duplicate MOB item row."));
                }
            }
            if result.insert(mob.id, mob).is_some() {
                return Err(invalid("Duplicate MOB drop template."));
            }
        }
        Ok(Self(Arc::new(result)))
    }

    pub fn get(&self, template: u32) -> Option<&MobDrops> {
        self.0.get(&template)
    }

    pub fn generate(
        &self,
        template: u32,
        level: u32,
        seed: u64,
        items: &ItemCatalog,
    ) -> io::Result<CorpseContents> {
        let definition = self
            .get(template)
            .ok_or_else(|| invalid("Unknown MOB drop template."))?;
        let mut rng = WorldRandom(seed);
        let mut result = CorpseContents {
            // One archived row has type -1. Use a generic wreck appearance for
            // that unspecified type, without inventing credits or item drops.
            asset: definition.kind.map_or(1960, |kind| {
                [1963, 1960, 1961, 1966, 1962, 1964, 1965][kind as usize]
            }),
            items: std::array::from_fn(|_| None),
            credits: match definition.kind {
                Some(kind @ (0 | 1)) => {
                    u64::from(level) * if kind == 1 { 20 } else { 10 }
                        + u64::from(rng.below(level.saturating_add(1).max(1)))
                }
                _ => 0,
            },
        };
        let random = (0..4).map(|_| rng.below(25)).sum::<u32>() + 4;
        let attempts = 1 + [55, 75, 90, 96, 99]
            .iter()
            .filter(|threshold| random > **threshold)
            .count();
        if definition.drops.is_empty() {
            return Ok(result);
        }
        for _ in 0..attempts {
            let drop = &definition.drops[rng.below(definition.drops.len() as u32) as usize];
            // Interpret SQL percentages literally for each attempt. Retain the
            // archived attempt-count distribution, not its broken chance scale.
            let roll = rng.fraction() * 100.;
            if drop.chance_percent <= 0. || roll >= drop.chance_percent {
                continue;
            }
            let item = items.get(drop.item)?;
            let quantity = (1 + rng.below(drop.quantity)).min(item.max_stack);
            if let Some(existing) = result
                .items
                .iter_mut()
                .flatten()
                .find(|slot| slot.template == item.id)
            {
                existing.quantity = existing
                    .quantity
                    .saturating_add(quantity)
                    .min(item.max_stack);
            } else if let Some(slot) = result.items.iter_mut().find(|slot| slot.is_none()) {
                *slot = Some(ItemInstance {
                    template: item.id,
                    quantity,
                    quality: 1.,
                    structure: 1.,
                    builder_name: String::new(),
                });
            }
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn catalog(chance: f64, kind: Option<u8>) -> LootCatalog {
        LootCatalog::from_mobs(
            vec![MobDrops {
                id: 1,
                kind,
                drops: vec![DropEntry {
                    source_id: 1,
                    item: 2837,
                    chance_percent: chance,
                    quantity: 255,
                    kind: 0,
                    usage_chance: 0,
                }],
            }],
            &ItemCatalog::load().unwrap(),
        )
        .unwrap()
    }

    #[test]
    fn source_catalog_resolves_items_and_keeps_fractional_percentages() {
        let source = LootCatalog::load().unwrap();
        assert_eq!(source.0.len(), 2042);
        assert_eq!(
            source.0.values().map(|mob| mob.drops.len()).sum::<usize>(),
            8506
        );
        assert!(source
            .0
            .values()
            .flat_map(|mob| &mob.drops)
            .any(|drop| drop.chance_percent.fract() != 0.));
    }

    #[test]
    fn generated_drops_are_reproducible_bounded_and_obey_zero_and_full_chance() {
        let items = ItemCatalog::load().unwrap();
        let guaranteed = catalog(100., Some(1));
        let disabled = catalog(0., Some(2));
        for seed in 0..200 {
            let result = guaranteed.generate(1, 10, seed, &items).unwrap();
            assert_eq!(result, guaranteed.generate(1, 10, seed, &items).unwrap());
            assert_eq!(result.asset, 1960);
            assert!((200..=210).contains(&result.credits));
            assert!(result.items.iter().flatten().count() == 1);
            for item in result.items.iter().flatten() {
                assert!((1..=items.get(item.template).unwrap().max_stack).contains(&item.quantity));
            }
            let empty = disabled.generate(1, 10, seed, &items).unwrap();
            assert!(empty.items.iter().all(Option::is_none));
            assert_eq!(empty.credits, 0);
        }
        assert!(guaranteed.generate(9999, 1, 1, &items).is_err());
        let mut invalid = guaranteed.get(1).unwrap().clone();
        invalid.drops[0].chance_percent = f64::NAN;
        assert!(LootCatalog::from_mobs(vec![invalid], &items).is_err());
    }

    #[test]
    fn fractional_drop_chances_are_not_truncated_or_treated_as_unit_probabilities() {
        let items = ItemCatalog::load().unwrap();
        let rare = catalog(0.5, Some(2));
        let common = catalog(50., Some(2));
        let mut counts = [0; 2];
        for seed in 0..6000 {
            for (index, catalog) in [&rare, &common].iter().enumerate() {
                if catalog
                    .generate(1, 1, seed, &items)
                    .unwrap()
                    .items
                    .iter()
                    .any(Option::is_some)
                {
                    counts[index] += 1;
                }
            }
        }
        assert!((1..100).contains(&counts[0]), "rare drops={}", counts[0]);
        assert!(counts[1] > 2000, "common drops={}", counts[1]);
    }
}
