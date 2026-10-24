//! Shared, deterministic resource contents and exclusive mining claims.
//!
//! Successful harvest counters live in the SAME atomic file as the miner's
//! cargo. A field rebuilds depletion from every account's counters on startup;
//! no second write can lose depletion after awarding ore. Only respawn generation
//! changes need a separate world file, persisted before exposing new stock.
use crate::content::{ResourceNode, Sector};
use crate::inventory::ItemInstance;
use crate::items::ItemCatalog;
use crate::protocol::invalid;
use crate::world_random::WorldRandom;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const RESOURCE_SLOTS: usize = 6;
pub const RESPAWN_MS: u64 = 15 * 60 * 1000;

pub fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HarvestReceipt {
    pub sector: u32,
    pub generation: u64,
    /// Cumulative counts by ORIGINAL resource slot for this player/generation.
    pub quantities: [u32; RESOURCE_SLOTS],
    pub completed_at_ms: u64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MiningClaim {
    pub owner: u32,
    pub slot: usize,
    pub quantity: u32,
}

#[derive(Clone)]
pub struct ResourceState {
    pub definition: ResourceNode,
    pub generation: u64,
    pub contents: [Option<ItemInstance>; RESOURCE_SLOTS],
    initial: [Option<ItemInstance>; RESOURCE_SLOTS],
    harvested: BTreeMap<u32, HarvestReceipt>,
    claim: Option<MiningClaim>,
    depleted_at_ms: Option<u64>,
}

impl ResourceState {
    fn generate(
        definition: &ResourceNode,
        generation: u64,
        items: &ItemCatalog,
    ) -> io::Result<Self> {
        let mut rng = WorldRandom(
            u64::from(definition.object.id) ^ generation.wrapping_mul(0xd1342543de82ef95),
        );
        let mut random = 4;
        for _ in 0..4 {
            random += rng.below(25);
        }
        let slots = 1 + [45, 70, 85, 95, 99]
            .iter()
            .filter(|threshold| random > **threshold)
            .count();
        let mut contents: [Option<ItemInstance>; RESOURCE_SLOTS] = std::array::from_fn(|_| None);
        for slot in contents.iter_mut().take(slots) {
            let level = definition.level.saturating_sub(rng.below(2) as u16).max(1);
            let mut candidates = Vec::new();
            for ore in &definition.ore_items {
                let template = items.get(ore.item_id)?;
                if template.level == level {
                    candidates.push((template, ore.frequency));
                }
            }
            if candidates.is_empty() {
                for ore in &definition.ore_items {
                    candidates.push((items.get(ore.item_id)?, ore.frequency));
                }
            }
            if candidates.is_empty() {
                break;
            }
            let total: f64 = candidates.iter().map(|(_, weight)| weight).sum();
            let mut pick = rng.fraction() * total;
            let mut template = candidates.last().unwrap().0;
            for (candidate, weight) in candidates {
                template = candidate;
                if pick < weight {
                    break;
                }
                pick -= weight;
            }
            let mut quantity = 1 + rng.below(5);
            if definition.resource_type == 7 && definition.level < 7 {
                quantity = if rng.below(30) == 29 {
                    1 + rng.below(80)
                } else if rng.below(20) > 18 {
                    1 + rng.below(40)
                } else {
                    1 + rng.below(6)
                };
            }
            *slot = Some(ItemInstance {
                template: template.id,
                quantity: quantity.min(template.max_stack),
                quality: 1.,
                structure: 1.,
                builder_name: String::new(),
            });
        }
        Ok(Self {
            definition: definition.clone(),
            generation,
            initial: contents.clone(),
            contents,
            harvested: BTreeMap::new(),
            claim: None,
            depleted_at_ms: None,
        })
    }

    pub fn available(&self) -> bool {
        self.contents.iter().any(Option::is_some)
    }

    pub fn fraction(&self) -> f32 {
        let original: u32 = self
            .initial
            .iter()
            .flatten()
            .map(|item| item.quantity)
            .sum();
        let current: u32 = self
            .contents
            .iter()
            .flatten()
            .map(|item| item.quantity)
            .sum();
        if original == 0 {
            0.
        } else {
            current as f32 / original as f32
        }
    }

    fn rebuild(&mut self) -> io::Result<()> {
        let mut taken = [0u32; RESOURCE_SLOTS];
        let mut latest = 0;
        for receipt in self.harvested.values() {
            for (sum, quantity) in taken.iter_mut().zip(receipt.quantities) {
                *sum = sum
                    .checked_add(quantity)
                    .ok_or_else(|| invalid("Resource harvest counter overflow."))?;
            }
            latest = latest.max(receipt.completed_at_ms);
        }
        self.contents = self.initial.clone();
        for (slot, taken) in self.contents.iter_mut().zip(taken) {
            let quantity = slot.as_ref().map_or(0, |item| item.quantity);
            if taken > quantity {
                return Err(invalid(
                    "Harvest receipts exceed original resource contents.",
                ));
            }
            if quantity == taken {
                *slot = None;
            } else {
                slot.as_mut().unwrap().quantity -= taken;
            }
        }
        self.depleted_at_ms = (!self.available() && latest > 0).then_some(latest);
        Ok(())
    }
}

/// Prepared before the inventory commit, installed infallibly afterward.
pub struct HarvestMutation {
    pub receipt: HarvestReceipt,
    state: ResourceState,
}

pub struct ResourceField {
    pub sector: u32,
    nodes: HashMap<u32, ResourceState>,
    generations: BTreeMap<u32, u64>,
    generation_path: PathBuf,
    items: ItemCatalog,
    last_respawn_poll: Instant,
}

impl ResourceField {
    pub fn load(
        sector: &Sector,
        items: &ItemCatalog,
        generation_path: PathBuf,
    ) -> io::Result<Self> {
        let generations: BTreeMap<u32, u64> = if generation_path.exists() {
            serde_json::from_slice(&fs::read(&generation_path)?)?
        } else {
            BTreeMap::new()
        };
        if generations.len() > 100_000 {
            return Err(invalid("Resource generation file exceeds supported size."));
        }
        let mut nodes = HashMap::new();
        for definition in &sector.resources {
            let id = definition.object.id;
            nodes.insert(
                id,
                ResourceState::generate(
                    definition,
                    generations.get(&id).copied().unwrap_or(0),
                    items,
                )?,
            );
        }
        Ok(Self {
            sector: sector.id,
            nodes,
            generations,
            generation_path,
            items: items.clone(),
            last_respawn_poll: Instant::now(),
        })
    }

    pub fn get(&self, id: u32) -> io::Result<&ResourceState> {
        self.nodes
            .get(&id)
            .ok_or_else(|| invalid("Unknown resource node."))
    }

    pub fn register_receipts(
        &mut self,
        owner: u32,
        receipts: &BTreeMap<u32, HarvestReceipt>,
    ) -> io::Result<()> {
        for (&id, receipt) in receipts
            .iter()
            .filter(|(_, receipt)| receipt.sector == self.sector)
        {
            let Some(node) = self.nodes.get_mut(&id) else {
                continue;
            }; // content removed from a newer public export
            if receipt.generation < node.generation {
                continue;
            }
            if receipt.generation > node.generation {
                return Err(invalid("Missing resource respawn generation history."));
            }
            if node.harvested.get(&owner) == Some(receipt) {
                continue;
            }
            let mut candidate = node.clone();
            candidate.harvested.insert(owner, receipt.clone());
            candidate.rebuild()?;
            *node = candidate;
        }
        Ok(())
    }

    pub fn claim(&mut self, node: u32, generation: u64, claim: MiningClaim) -> io::Result<()> {
        let state = self
            .nodes
            .get_mut(&node)
            .ok_or_else(|| invalid("Unknown resource node."))?;
        let item = state
            .contents
            .get(claim.slot)
            .and_then(Option::as_ref)
            .ok_or_else(|| invalid("Resource slot is empty."))?;
        if state.claim.is_some() {
            return Err(invalid("This resource is already being prospected."));
        }
        if state.generation != generation || claim.quantity == 0 || claim.quantity > item.quantity {
            return Err(invalid("Resource contents changed before prospecting."));
        }
        state.claim = Some(claim);
        Ok(())
    }

    pub fn release(&mut self, id: u32, owner: u32) {
        if let Some(state) = self.nodes.get_mut(&id) {
            if state
                .claim
                .as_ref()
                .is_some_and(|claim| claim.owner == owner)
            {
                state.claim = None;
            }
        }
    }

    pub fn prepare_harvest(
        &self,
        id: u32,
        generation: u64,
        claim: &MiningClaim,
        now_ms: u64,
    ) -> io::Result<HarvestMutation> {
        let mut state = self.get(id)?.clone();
        if state.generation != generation || state.claim.as_ref() != Some(claim) {
            return Err(invalid("Mining claim is no longer valid."));
        }
        let mut receipt = state
            .harvested
            .get(&claim.owner)
            .cloned()
            .unwrap_or(HarvestReceipt {
                sector: self.sector,
                generation,
                quantities: [0; RESOURCE_SLOTS],
                completed_at_ms: now_ms,
            });
        receipt.quantities[claim.slot] = receipt.quantities[claim.slot]
            .checked_add(claim.quantity)
            .ok_or_else(|| invalid("Harvest quantity overflow."))?;
        receipt.completed_at_ms = now_ms;
        state.harvested.insert(claim.owner, receipt.clone());
        state.claim = None;
        state.rebuild()?;
        Ok(HarvestMutation { receipt, state })
    }

    pub fn commit_harvest(&mut self, mutation: HarvestMutation) {
        self.nodes
            .insert(mutation.state.definition.object.id, mutation.state);
    }

    pub fn advance(&mut self) -> io::Result<()> {
        if self.last_respawn_poll.elapsed() < Duration::from_millis(500) {
            return Ok(());
        }
        self.last_respawn_poll = Instant::now();
        self.respawn_due(unix_ms())
    }

    pub fn respawn_due(&mut self, now_ms: u64) -> io::Result<()> {
        let mut replacements = Vec::new();
        for state in self.nodes.values().filter(|state| {
            state.definition.resource_type < 9
                && state
                    .depleted_at_ms
                    .is_some_and(|at| now_ms.saturating_sub(at) >= RESPAWN_MS)
        }) {
            let next = state
                .generation
                .checked_add(1)
                .ok_or_else(|| invalid("Resource generation overflow."))?;
            replacements.push(ResourceState::generate(
                &state.definition,
                next,
                &self.items,
            )?);
        }
        if replacements.is_empty() {
            return Ok(());
        }
        let mut generations = self.generations.clone();
        for state in &replacements {
            generations.insert(state.definition.object.id, state.generation);
        }
        if let Some(parent) = self.generation_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let temporary = self.generation_path.with_extension("json.tmp");
        fs::write(&temporary, serde_json::to_vec(&generations)?)?;
        fs::rename(temporary, &self.generation_path)?;
        self.generations = generations;
        for state in replacements {
            self.nodes.insert(state.definition.object.id, state);
        }
        Ok(())
    }
}

pub fn lock_resources(field: &Mutex<ResourceField>) -> io::Result<MutexGuard<'_, ResourceField>> {
    field
        .lock()
        .map_err(|_| io::Error::other("Resource state lock poisoned."))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn field() -> ResourceField {
        let mut sector: Sector =
            serde_json::from_str(include_str!("../data/asteroid_belt_alpha.json")).unwrap();
        sector.resources.truncate(1);
        ResourceField::load(
            &sector,
            &ItemCatalog::load().unwrap(),
            std::env::temp_dir()
                .join(format!("enb-resources-{}", rand::random::<u64>()))
                .join("generations.json"),
        )
        .unwrap()
    }

    #[test]
    fn shared_claims_and_durable_receipts_rebuild_depletion_idempotently() {
        let mut original = field();
        let id = *original.nodes.keys().next().unwrap();
        let before = original.get(id).unwrap().contents.clone();
        let slot = before.iter().position(Option::is_some).unwrap();
        let claim = MiningClaim {
            owner: 0x40000001,
            slot,
            quantity: 1,
        };
        original.claim(id, 0, claim.clone()).unwrap();
        assert!(original
            .claim(
                id,
                0,
                MiningClaim {
                    owner: 0x40000002,
                    ..claim
                }
            )
            .is_err());
        let mutation = original.prepare_harvest(id, 0, &claim, 1000).unwrap();
        let receipts = BTreeMap::from([(id, mutation.receipt.clone())]);
        original.commit_harvest(mutation);
        let node = &original.get(id).unwrap().definition;
        let sector = Sector {
            resources: vec![node.clone()],
            ..serde_json::from_str(include_str!("../data/sector.json")).unwrap()
        };
        let mut restored =
            ResourceField::load(&sector, &original.items, original.generation_path.clone())
                .unwrap();
        restored.register_receipts(claim.owner, &receipts).unwrap();
        restored.register_receipts(claim.owner, &receipts).unwrap();
        assert_eq!(
            restored.get(id).unwrap().contents,
            original.get(id).unwrap().contents
        );
        let taken: u32 = before
            .iter()
            .flatten()
            .map(|item| item.quantity)
            .sum::<u32>()
            - restored
                .get(id)
                .unwrap()
                .contents
                .iter()
                .flatten()
                .map(|item| item.quantity)
                .sum::<u32>();
        assert_eq!(taken, 1);
        assert!(restored.claim(id, 1, claim.clone()).is_err());
        restored.release(id, claim.owner);
    }

    #[test]
    fn respawn_is_persisted_before_new_stock_and_ignores_old_generation_receipts() {
        let mut field = field();
        let id = *field.nodes.keys().next().unwrap();
        let mut receipts = BTreeMap::new();
        let contents = field.get(id).unwrap().contents.clone();
        for (slot, item) in contents
            .iter()
            .enumerate()
            .filter_map(|(slot, item)| item.as_ref().map(|item| (slot, item)))
        {
            let claim = MiningClaim {
                owner: 0x40000001,
                slot,
                quantity: item.quantity,
            };
            field.claim(id, 0, claim.clone()).unwrap();
            let mutation = field.prepare_harvest(id, 0, &claim, 1000).unwrap();
            receipts.insert(id, mutation.receipt.clone());
            field.commit_harvest(mutation);
        }
        assert!(!field.get(id).unwrap().available());
        field.respawn_due(1000 + RESPAWN_MS - 1).unwrap();
        assert_eq!(field.get(id).unwrap().generation, 0);
        field.respawn_due(1000 + RESPAWN_MS).unwrap();
        assert!(field.get(id).unwrap().available());
        assert_eq!(field.get(id).unwrap().generation, 1);
        let sector = Sector {
            resources: vec![field.get(id).unwrap().definition.clone()],
            ..serde_json::from_str(include_str!("../data/sector.json")).unwrap()
        };
        let mut restored =
            ResourceField::load(&sector, &field.items, field.generation_path.clone()).unwrap();
        restored.register_receipts(0x40000001, &receipts).unwrap();
        assert_eq!(restored.get(id).unwrap().generation, 1);
        assert_eq!(restored.get(id).unwrap().fraction(), 1.);
        // A failed world-file write must not expose a new generation.
        let node = restored.nodes.get_mut(&id).unwrap();
        node.contents.fill(None);
        node.depleted_at_ms = Some(1000);
        let blocked = field.generation_path.parent().unwrap().join("blocked");
        fs::write(&blocked, b"file, not directory").unwrap();
        restored.generation_path = blocked.join("generation.json");
        assert!(restored.respawn_due(1000 + RESPAWN_MS).is_err());
        assert_eq!(restored.get(id).unwrap().generation, 1);
        assert!(!restored.get(id).unwrap().available());
    }
}
