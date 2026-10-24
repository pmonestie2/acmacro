//! Durable corpse births and receipt-based depletion. A successful collection
//! must save its LootReceipt WITH player cargo/credits before collection.commit().
//! Initial corpse stock is persisted before it is exposed to any player.
use crate::inventory::ItemInstance;
use crate::items::ItemCatalog;
use crate::loot::{CorpseContents, LootCatalog, CORPSE_LIFETIME_MS, LOOT_SLOTS, PRIVATE_LOOT_MS};
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

const FIRST_CORPSE: u32 = 0x30000000;
const CORPSE_LIMIT: u32 = 0x40000000;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct LootReceipt {
    pub sector: u32,
    pub corpse: u32,
    pub slots: [bool; LOOT_SLOTS],
    pub credits: bool,
}

impl LootReceipt {
    /// Corpse IDs are sector-local. The combined key never aliases two sectors.
    pub fn key(&self) -> u64 {
        (u64::from(self.sector) << 32) | u64::from(self.corpse)
    }
    pub fn valid(&self) -> bool {
        (1..10000).contains(&self.sector)
            && (FIRST_CORPSE..CORPSE_LIMIT).contains(&self.corpse)
            && (self.credits || self.slots.iter().any(|slot| *slot))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct MobDeath {
    pub spawn: u32,
    pub generation: u64,
}

/// The killer and position must be resolved by authoritative combat, never by
/// an incoming loot packet. Generation identifies one life of one spawn.
pub struct CorpseBirth {
    pub death: MobDeath,
    pub template: u32,
    pub name: String,
    pub level: u32,
    pub killer: u32,
    pub position: [f32; 3],
    pub now_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Corpse {
    pub id: u32,
    pub death: MobDeath,
    pub name: String,
    pub level: u32,
    pub killer: u32,
    pub position: [f32; 3],
    pub created_ms: u64,
    pub initial: CorpseContents,
    #[serde(skip)]
    receipts: BTreeMap<u32, LootReceipt>,
    #[serde(skip)]
    claim: Option<(u32, usize)>,
}

impl Corpse {
    pub fn range_to(&self, position: [f32; 3]) -> io::Result<f32> {
        let radius =
            crate::asset_geometry::AssetGeometry::load()?.resource_radius(self.initial.asset)?;
        let range = crate::mob::distance(self.position, position) - radius;
        Ok(if range < 0. { 0. } else { range })
    }
    pub fn remaining(&self) -> io::Result<CorpseContents> {
        let mut remaining = self.initial.clone();
        for receipt in self.receipts.values() {
            if receipt.credits {
                remaining.credits = 0;
            }
            for (slot, taken) in receipt.slots.iter().enumerate() {
                if *taken && remaining.items[slot].take().is_none() {
                    return Err(invalid("Conflicting corpse item receipts."));
                }
            }
        }
        if self
            .receipts
            .values()
            .filter(|receipt| receipt.credits)
            .count()
            > 1
        {
            return Err(invalid("Conflicting corpse credit receipts."));
        }
        Ok(remaining)
    }

    pub fn available(&self, now_ms: u64) -> io::Result<bool> {
        let stock = self.remaining()?;
        Ok(now_ms < self.created_ms.saturating_add(CORPSE_LIFETIME_MS)
            && (stock.credits > 0 || stock.items.iter().any(Option::is_some)))
    }

    fn authorize(&self, player: u32, now_ms: u64, same_group: bool) -> io::Result<()> {
        if !self.available(now_ms)? {
            return Err(invalid("Corpse is empty or expired."));
        }
        let opened = self.receipts.values().any(|receipt| receipt.credits);
        if player != self.killer
            && !same_group
            && !opened
            && now_ms < self.created_ms.saturating_add(PRIVATE_LOOT_MS)
        {
            return Err(invalid("Corpse is still reserved for its credited killer."));
        }
        Ok(())
    }
}

#[derive(Clone, Serialize, Deserialize)]
struct DeathRecord {
    generation: u64,
    killed_ms: u64,
}

#[derive(Clone, Serialize, Deserialize)]
struct WorldFile {
    version: u32,
    sector: u32,
    next_id: u32,
    deaths: BTreeMap<u32, DeathRecord>,
    corpses: BTreeMap<u32, Corpse>,
}

pub struct LootWorld {
    path: PathBuf,
    state: WorldFile,
}

pub struct LootCollection<'world> {
    pub receipt: LootReceipt,
    pub item: Option<ItemInstance>,
    pub credits: u64,
    corpse: Corpse,
    // The mutable borrow prevents a second collection from preparing against
    // the same old stock while the first inventory transaction is in flight.
    world: &'world mut LootWorld,
}

impl LootCollection<'_> {
    /// Call only after the player's receipt and award are durably committed.
    /// Dropping this value instead leaves corpse stock unchanged.
    pub fn commit(self) {
        self.world.state.corpses.insert(self.corpse.id, self.corpse);
    }
}

impl LootWorld {
    pub fn load(path: PathBuf, sector: u32, items: &ItemCatalog) -> io::Result<Self> {
        let state: WorldFile = if path.exists() {
            serde_json::from_slice(&fs::read(&path)?)?
        } else {
            WorldFile {
                version: 1,
                sector,
                next_id: FIRST_CORPSE,
                deaths: BTreeMap::new(),
                corpses: BTreeMap::new(),
            }
        };
        if state.version != 1
            || state.sector != sector
            || !(1..20000).contains(&sector)
            || !(FIRST_CORPSE..=CORPSE_LIMIT).contains(&state.next_id)
            || state.deaths.len() > 8192
            || state
                .deaths
                .iter()
                .any(|(spawn, death)| *spawn == 0 || death.generation == 0 || death.killed_ms == 0)
            || state.corpses.len() > 4096
        {
            return Err(invalid("Invalid corpse world file."));
        }
        for (&id, corpse) in &state.corpses {
            if id != corpse.id
                || !(FIRST_CORPSE..state.next_id).contains(&id)
                || corpse.created_ms == 0
                || corpse.name.len() > 80
                || !corpse.name.is_ascii()
                || corpse.position.iter().any(|value| !value.is_finite())
                || !(1..=100).contains(&corpse.level)
                || corpse.death.spawn == 0
                || corpse.death.generation == 0
                || corpse.killer & 0xff000000 != 0x40000000
                || state
                    .deaths
                    .get(&corpse.death.spawn)
                    .is_none_or(|death| death.generation < corpse.death.generation)
                || corpse.initial.credits > 10_000
            {
                return Err(invalid("Invalid corpse record."));
            }
            for item in corpse.initial.items.iter().flatten() {
                let template = items.get(item.template)?;
                if item.quantity == 0
                    || item.quantity > template.max_stack
                    || item.quality != 1.
                    || item.structure != 1.
                {
                    return Err(invalid("Invalid corpse item instance."));
                }
            }
        }
        Ok(Self { path, state })
    }

    pub fn generation(&self, spawn: u32) -> u64 {
        self.state
            .deaths
            .get(&spawn)
            .map_or(0, |death| death.generation)
    }
    pub fn reconcile_combat(&self, combat: &mut crate::sector_combat::SectorCombat, now_ms: u64) {
        for mob in &mut combat.actors {
            if let Some(death) = self.state.deaths.get(&mob.definition.source_id) {
                let elapsed = now_ms.saturating_sub(death.killed_ms) as f32 / 1000.;
                mob.reconcile_death(
                    death.generation,
                    (mob.definition.respawn_seconds - elapsed).max(0.),
                );
            }
        }
    }
    pub fn capture_deaths(
        &mut self,
        combat: &crate::sector_combat::SectorCombat,
        now_ms: u64,
    ) -> io::Result<()> {
        let catalog = LootCatalog::load()?;
        let items = ItemCatalog::load()?;
        for mob in &combat.actors {
            if mob.state != crate::mob::MobState::Dead
                || mob.death_generation <= self.generation(mob.definition.source_id)
            {
                continue;
            }
            let Some(killer) = mob.killer else {
                continue;
            };
            if catalog.get(mob.definition.template_id).is_none() {
                continue;
            }
            self.spawn(
                CorpseBirth {
                    death: MobDeath {
                        spawn: mob.definition.source_id,
                        generation: mob.death_generation,
                    },
                    template: mob.definition.template_id,
                    name: mob.definition.name.clone(),
                    level: mob.definition.level,
                    killer,
                    position: mob.position,
                    now_ms,
                },
                &catalog,
                &items,
            )?;
        }
        Ok(())
    }
    pub fn corpses(&self) -> impl Iterator<Item = &Corpse> {
        self.state.corpses.values()
    }
    pub fn get(&self, id: u32) -> io::Result<&Corpse> {
        self.state
            .corpses
            .get(&id)
            .ok_or_else(|| invalid("Unknown corpse."))
    }

    pub fn spawn(
        &mut self,
        birth: CorpseBirth,
        catalog: &LootCatalog,
        items: &ItemCatalog,
    ) -> io::Result<Option<u32>> {
        if self.state.sector >= 10000
            || birth.death.spawn == 0
            || birth.death.generation == 0
            || birth.now_ms == 0
            || !(1..=100).contains(&birth.level)
            || birth.name.len() > 70
            || !birth.name.is_ascii()
            || birth.position.iter().any(|value| !value.is_finite())
            || birth.killer & 0xff000000 != 0x40000000
        {
            return Err(invalid("Invalid authoritative corpse birth."));
        }
        if birth.death.generation <= self.generation(birth.death.spawn) {
            return Ok(self
                .corpses()
                .find(|corpse| corpse.death == birth.death)
                .map(|corpse| corpse.id));
        }
        let mut candidate = self.state.clone();
        candidate.corpses.retain(|_, corpse| {
            birth.now_ms < corpse.created_ms.saturating_add(CORPSE_LIFETIME_MS)
        });
        if candidate.corpses.len() >= 4096 || candidate.next_id >= CORPSE_LIMIT {
            return Err(invalid("Corpse world capacity reached."));
        }
        let id = candidate.next_id;
        let seed = (u64::from(self.state.sector) << 48)
            ^ (u64::from(birth.death.spawn) << 16)
            ^ birth.death.generation.wrapping_mul(0xd1342543de82ef95);
        let initial = catalog.generate(birth.template, birth.level, seed, items)?;
        candidate.next_id += 1;
        candidate.deaths.insert(
            birth.death.spawn,
            DeathRecord {
                generation: birth.death.generation,
                killed_ms: birth.now_ms,
            },
        );
        candidate.corpses.insert(
            id,
            Corpse {
                id,
                death: birth.death,
                name: format!("Corpse of {}", birth.name),
                level: birth.level,
                killer: birth.killer,
                position: birth.position,
                created_ms: birth.now_ms,
                initial,
                receipts: BTreeMap::new(),
                claim: None,
            },
        );
        // Birth stock and death identity become durable together. No corpse ID
        // or new stock is published if this write fails.
        self.save(&candidate)?;
        self.state = candidate;
        let corpse = &self.state.corpses[&id];
        let stock: Vec<String> = corpse
            .initial
            .items
            .iter()
            .enumerate()
            .filter_map(|(slot, item)| {
                item.as_ref()
                    .map(|item| format!("{slot}:{}x{}", item.template, item.quantity))
            })
            .collect();
        println!(
            "Corpse created: sector={}, corpse={id}, spawn={}, generation={}, template={}, killer={}, items=[{}], credits={}",
            self.state.sector, corpse.death.spawn, corpse.death.generation,
            birth.template, corpse.killer, stock.join(", "), corpse.initial.credits
        );
        Ok(Some(id))
    }

    fn save(&self, state: &WorldFile) -> io::Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        let temporary = self.path.with_extension("json.tmp");
        fs::write(&temporary, serde_json::to_vec(state)?)?;
        if let Err(error) = fs::rename(&temporary, &self.path) {
            let _ = fs::remove_file(&temporary);
            return Err(error);
        }
        Ok(())
    }

    pub fn register_receipts(
        &mut self,
        owner: u32,
        receipts: &BTreeMap<u64, LootReceipt>,
    ) -> io::Result<()> {
        for (&key, receipt) in receipts {
            if !receipt.valid() || key != receipt.key() {
                return Err(invalid("Invalid loot receipt."));
            }
            if receipt.sector != self.state.sector {
                continue;
            }
            if receipt.corpse >= self.state.next_id {
                // Pruned, expired births are below the allocation cursor. A
                // newer receipt proves the world file was lost or rolled back;
                // reusing those IDs would alias previously collected corpses.
                return Err(invalid("Missing corpse birth history."));
            }
            let Some(corpse) = self.state.corpses.get_mut(&receipt.corpse) else {
                continue;
            };
            let mut candidate = corpse.clone();
            if candidate.receipts.get(&owner).is_some_and(|old| {
                old.credits && !receipt.credits
                    || old
                        .slots
                        .iter()
                        .zip(receipt.slots)
                        .any(|(was_taken, now_taken)| *was_taken && !now_taken)
            }) {
                return Err(invalid("Loot receipt moved backwards."));
            }
            candidate.receipts.insert(owner, receipt.clone());
            candidate.remaining()?;
            *corpse = candidate;
        }
        Ok(())
    }

    pub fn claim(
        &mut self,
        owner: u32,
        corpse: u32,
        slot: usize,
        now_ms: u64,
        same_group: bool,
    ) -> io::Result<()> {
        let corpse = self
            .state
            .corpses
            .get_mut(&corpse)
            .ok_or_else(|| invalid("Unknown corpse."))?;
        corpse.authorize(owner, now_ms, same_group)?;
        if slot >= LOOT_SLOTS || corpse.remaining()?.items[slot].is_none() {
            return Err(invalid("Corpse slot is empty or invalid."));
        }
        if corpse.claim.is_some() {
            return Err(invalid("Corpse is already being looted."));
        }
        corpse.claim = Some((owner, slot));
        Ok(())
    }

    pub fn release(&mut self, owner: u32, corpse: u32) {
        if let Some(corpse) = self.state.corpses.get_mut(&corpse) {
            if corpse.claim.is_some_and(|(claimant, _)| claimant == owner) {
                corpse.claim = None;
            }
        }
    }

    /// None claims only opening credits; Some(slot) completes a reserved item.
    /// The returned receipt must be saved with the award before installation.
    pub fn prepare_collection(
        &mut self,
        owner: u32,
        id: u32,
        slot: Option<usize>,
        now_ms: u64,
        same_group: bool,
    ) -> io::Result<Option<LootCollection<'_>>> {
        let corpse = self.get(id)?;
        corpse.authorize(owner, now_ms, same_group)?;
        if let Some(slot) = slot {
            if slot >= LOOT_SLOTS || corpse.claim != Some((owner, slot)) {
                return Err(invalid("Loot claim is no longer valid."));
            }
        } else if corpse.claim.is_some_and(|(claimant, _)| claimant != owner) {
            return Err(invalid("Corpse is already being looted."));
        }
        let remaining = corpse.remaining()?;
        if slot.is_none() && corpse.receipts.values().any(|receipt| receipt.credits) {
            return Ok(None); // Already opened: UI permission needs no second award.
        }
        let item = slot
            .map(|slot| {
                remaining.items[slot]
                    .clone()
                    .ok_or_else(|| invalid("Corpse item was already collected."))
            })
            .transpose()?;
        let mut candidate = corpse.clone();
        let mut receipt = candidate
            .receipts
            .get(&owner)
            .cloned()
            .unwrap_or(LootReceipt {
                sector: self.state.sector,
                corpse: id,
                slots: [false; LOOT_SLOTS],
                credits: false,
            });
        if let Some(slot) = slot {
            receipt.slots[slot] = true;
        }
        // Opening also releases the killer lock, even when the corpse has no
        // credits. Exactly one receipt records that opening operation.
        if !candidate.receipts.values().any(|receipt| receipt.credits) {
            receipt.credits = true;
        }
        candidate.receipts.insert(owner, receipt.clone());
        candidate.claim = None;
        candidate.remaining()?;
        Ok(Some(LootCollection {
            receipt,
            item,
            credits: remaining.credits,
            corpse: candidate,
            world: self,
        }))
    }
}

pub fn lock_loot(world: &Mutex<LootWorld>) -> io::Result<MutexGuard<'_, LootWorld>> {
    world
        .lock()
        .map_err(|_| io::Error::other("Loot world lock poisoned."))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inventory::Inventory;
    use crate::loot::{DropEntry, MobDrops};

    const ALICE: u32 = 0x40000001;
    const BOB: u32 = 0x40000002;

    #[test]
    fn combat_killer_death_generations_persist_and_prevent_checkpoint_resurrection() {
        let items = ItemCatalog::load().unwrap();
        let sector: crate::content::Sector =
            serde_json::from_str(include_str!("../data/sector.json")).unwrap();
        let mut definition = sector.mobs[0].clone();
        definition.source_id = 10;
        definition.template_id = 1; // Actual public structural MOB, including credit loot.
        let path = path();
        let mut world = LootWorld::load(path.clone(), 1076, &items).unwrap();
        let mut combat = crate::sector_combat::SectorCombat::new(std::slice::from_ref(&definition));
        combat.actors[0].damage_by(100_000., ALICE);
        assert_eq!(combat.actors[0].killer, Some(ALICE));
        world.capture_deaths(&combat, 1000).unwrap();
        world.capture_deaths(&combat, 1000).unwrap();
        assert_eq!(world.corpses().count(), 1);
        let mut expired =
            crate::sector_combat::SectorCombat::new(std::slice::from_ref(&definition));
        expired.actors[0] = combat.actors[0].clone();
        world.reconcile_combat(
            &mut expired,
            1000 + ((definition.respawn_seconds + 1.) * 1000.) as u64,
        );
        expired.advance(0.01, &[]).unwrap();
        assert_ne!(expired.actors[0].state, crate::mob::MobState::Dead);
        let mut fresh = crate::sector_combat::SectorCombat::new(std::slice::from_ref(&definition));
        world.reconcile_combat(&mut fresh, 1001);
        assert_eq!(fresh.actors[0].state, crate::mob::MobState::Dead);
        fresh.advance(definition.respawn_seconds + 1., &[]).unwrap();
        assert_ne!(fresh.actors[0].state, crate::mob::MobState::Dead);
        fresh.actors[0].damage_by(100_000., BOB);
        world
            .capture_deaths(
                &fresh,
                1000 + ((definition.respawn_seconds + 1.) * 1000.) as u64,
            )
            .unwrap();
        assert_eq!(world.generation(10), 2);
        assert_eq!(world.corpses().count(), 2);
        fs::remove_file(&path).unwrap();
        fs::remove_dir(path.parent().unwrap()).unwrap();
    }

    fn catalog() -> LootCatalog {
        LootCatalog::from_mobs(
            vec![MobDrops {
                id: 1,
                kind: Some(1),
                drops: vec![DropEntry {
                    source_id: 1,
                    item: 2837,
                    chance_percent: 100.,
                    quantity: 3,
                    kind: 0,
                    usage_chance: 0,
                }],
            }],
            &ItemCatalog::load().unwrap(),
        )
        .unwrap()
    }
    fn birth(generation: u64) -> CorpseBirth {
        CorpseBirth {
            death: MobDeath {
                spawn: 10,
                generation,
            },
            template: 1,
            name: "Drone".into(),
            level: 10,
            killer: ALICE,
            position: [0.; 3],
            now_ms: 1000,
        }
    }
    fn path() -> PathBuf {
        std::env::temp_dir()
            .join(format!("enb-loot-{}", rand::random::<u64>()))
            .join("1076.json")
    }

    #[test]
    fn births_are_durable_idempotent_and_not_exposed_after_failed_writes() {
        let items = ItemCatalog::load().unwrap();
        let path = path();
        let mut world = LootWorld::load(path.clone(), 1076, &items).unwrap();
        let id = world.spawn(birth(1), &catalog(), &items).unwrap().unwrap();
        assert_eq!(world.spawn(birth(1), &catalog(), &items).unwrap(), Some(id));
        let mut restored = LootWorld::load(path.clone(), 1076, &items).unwrap();
        assert_eq!(
            restored.get(id).unwrap().initial,
            world.get(id).unwrap().initial
        );
        assert_eq!(
            restored.spawn(birth(1), &catalog(), &items).unwrap(),
            Some(id)
        );
        // Force an ordinary filesystem error without modifying permissions.
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        assert!(restored.spawn(birth(2), &catalog(), &items).is_err());
        assert_eq!(restored.generation(10), 1);
        assert_eq!(restored.corpses().count(), 1);
        fs::remove_dir(&path).unwrap();
        fs::remove_dir(path.parent().unwrap()).unwrap();
    }

    #[test]
    fn ownership_claims_atomic_inventory_and_receipts_survive_reload() {
        let items = ItemCatalog::load().unwrap();
        let path = path();
        let mut world = LootWorld::load(path.clone(), 1076, &items).unwrap();
        let id = world.spawn(birth(1), &catalog(), &items).unwrap().unwrap();
        assert!(world.claim(BOB, id, 0, 1001, false).is_err());
        world.claim(ALICE, id, 0, 1001, false).unwrap();
        assert!(world.claim(BOB, id, 0, 1001, true).is_err());
        let award = world
            .prepare_collection(ALICE, id, Some(0), 1002, false)
            .unwrap()
            .unwrap();
        let mut inventory = Inventory::starter(&items).unwrap();
        let old_credits = inventory.credits;
        let quantity = award.item.as_ref().unwrap().quantity;
        let credits = award.credits;
        // Preparing an award cannot deplete the corpse or mutate inventory.
        assert!(inventory.cargo.iter().all(Option::is_none));
        inventory = inventory.collected_loot(&award, &items).unwrap();
        assert!(inventory.collected_loot(&award, &items).is_err());
        award.commit();
        assert_eq!(inventory.credits, old_credits + credits);
        assert_eq!(inventory.cargo[0].as_ref().unwrap().quantity, quantity);
        let mut restored = LootWorld::load(path.clone(), 1076, &items).unwrap();
        restored
            .register_receipts(ALICE, &inventory.loot_receipts)
            .unwrap();
        restored
            .register_receipts(ALICE, &inventory.loot_receipts)
            .unwrap();
        assert!(!restored.get(id).unwrap().available(1003).unwrap());
        let mut missing =
            LootWorld::load(path.with_extension("missing.json"), 1076, &items).unwrap();
        assert!(missing
            .register_receipts(ALICE, &inventory.loot_receipts)
            .is_err());
        assert!(restored.claim(ALICE, id, 0, 1003, false).is_err());
        // A different player's conflicting claim is rejected on reconstruction.
        assert!(restored
            .register_receipts(BOB, &inventory.loot_receipts)
            .is_err());
        fs::remove_file(path.with_extension("json")).unwrap();
        fs::remove_dir(path.parent().unwrap()).unwrap();
    }

    #[test]
    fn public_timeout_opening_and_full_cargo_do_not_duplicate_or_destroy_drops() {
        let items = ItemCatalog::load().unwrap();
        let path = path();
        let mut world = LootWorld::load(path.clone(), 1076, &items).unwrap();
        let id = world.spawn(birth(1), &catalog(), &items).unwrap().unwrap();
        assert!(world
            .prepare_collection(BOB, id, None, 1001, false)
            .is_err());
        let opening = world
            .prepare_collection(BOB, id, None, 1000 + PRIVATE_LOOT_MS, false)
            .unwrap()
            .unwrap();
        let bob = Inventory::starter(&items)
            .unwrap()
            .collected_loot(&opening, &items)
            .unwrap();
        opening.commit();
        assert!(world
            .prepare_collection(ALICE, id, None, 1000 + PRIVATE_LOOT_MS, false)
            .unwrap()
            .is_none());
        world
            .claim(BOB, id, 0, 1000 + PRIVATE_LOOT_MS, false)
            .unwrap();
        let award = world
            .prepare_collection(BOB, id, Some(0), 1000 + PRIVATE_LOOT_MS, false)
            .unwrap()
            .unwrap();
        assert_eq!(award.credits, 0);
        let mut full = bob.clone();
        full.cargo.fill(Some(ItemInstance {
            template: 769,
            quantity: 1,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        }));
        assert!(full.collected_loot(&award, &items).is_err());
        drop(award);
        world.release(BOB, id);
        assert!(world.get(id).unwrap().remaining().unwrap().items[0].is_some());
        assert!(!world
            .get(id)
            .unwrap()
            .available(1000 + CORPSE_LIFETIME_MS)
            .unwrap());
        assert!(world
            .claim(ALICE, id, 0, 1000 + CORPSE_LIFETIME_MS, false)
            .is_err());
        fs::remove_file(&path).unwrap();
        fs::remove_dir(path.parent().unwrap()).unwrap();
    }
}
