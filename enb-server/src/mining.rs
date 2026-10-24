//! Authoritative prospect/drain/tractor lifecycle. Cargo and harvest receipts
//! commit together; shared field depletion is then a simple in-memory install.
use super::*;
use crate::resources::{unix_ms, MiningClaim};

const PROSPECT_SKILL: u32 = 41;

#[cfg(test)]
#[path = "mining_geometry_tests.rs"]
mod geometry_tests;

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub(super) enum MiningPhase {
    Prospect,
    Tractor,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub(super) struct MiningCast {
    pub node: u32,
    pub generation: u64,
    pub slot: usize,
    pub quantity: u32,
    pub template: u32,
    pub before_harvested: u32,
    pub remaining: f32,
    pub energy_left: f32,
    pub effect: u32,
    pub article: Option<u32>,
    pub phase: MiningPhase,
}

impl MiningCast {
    pub fn valid(&self) -> bool {
        self.slot < crate::resources::RESOURCE_SLOTS
            && self.quantity > 0
            && self.remaining.is_finite()
            // Prospecting caps at 100s; a heavily debuffed tractor may take
            // longer (maximum range / minimum supported speed).
            && (0.0..=2500.).contains(&self.remaining)
            && self.energy_left.is_finite()
            && self.energy_left >= 0.
    }
}

impl PlayerSession {
    pub(crate) fn register_resource_receipts(&self, field: &mut ResourceField) -> io::Result<()> {
        field.register_receipts(self.player_id(), &self.inventory.harvests)
    }

    pub(crate) fn attach_resource_field(
        &mut self,
        field: Arc<Mutex<ResourceField>>,
    ) -> io::Result<()> {
        if Arc::ptr_eq(&self.resource_field, &field) {
            return Ok(());
        }
        if let Some(cast) = &self.mining {
            lock_resources(&self.resource_field)?.release(cast.node, self.player_id());
        }
        self.resource_field = field;
        self.restore_mining_claim()
    }

    fn already_harvested(&self, node: u32, generation: u64, slot: usize) -> u32 {
        self.inventory
            .harvests
            .get(&node)
            .filter(|receipt| receipt.generation == generation)
            .map_or(0, |receipt| receipt.quantities[slot])
    }

    pub(super) fn restore_mining_claim(&mut self) -> io::Result<()> {
        let Some(cast) = self.mining.clone() else {
            return Ok(());
        };
        if !cast.valid()
            || self.already_harvested(cast.node, cast.generation, cast.slot)
                != cast.before_harvested
        {
            return self
                .cancel_mining("A completed or incompatible mining checkpoint was discarded.");
        }
        let claim = MiningClaim {
            owner: self.player_id(),
            slot: cast.slot,
            quantity: cast.quantity,
        };
        let result = lock_resources(&self.resource_field)?.claim(cast.node, cast.generation, claim);
        if result.is_err() {
            self.cancel_mining("Mining claim could not be restored.")?;
        }
        Ok(())
    }

    pub(super) fn prospect_range(&self) -> f32 {
        750. + 250. * self.skill_catalog.level(PROSPECT_SKILL) as f32
    }

    pub(super) fn resume_mining_visuals(&mut self) -> io::Result<()> {
        let Some(mut cast) = self.mining.clone() else {
            return Ok(());
        };
        self.emit_harvest_visual(Frame::new(0xf, cast.effect.to_le_bytes().to_vec()));
        if let Some(article) = cast.article {
            self.emit_harvest_visual(Frame::new(7, article.to_le_bytes().to_vec()));
        }
        cast.effect = self.next_effect;
        self.next_effect = self.next_effect.wrapping_add(1);
        if cast.phase == MiningPhase::Prospect {
            self.emit_harvest_visual(packets::start_prospect(
                self.player_id(),
                cast.node,
                cast.effect,
                self.tick_ms(),
                (cast.remaining * 1000.) as u32,
            ));
        } else {
            cast.article = Some(self.next_effect);
            self.next_effect = self.next_effect.wrapping_add(1);
            let position = lock_resources(&self.resource_field)?
                .get(cast.node)?
                .definition
                .object
                .position;
            self.emit_harvest_visual(packets::tractor_ore(&packets::OreTractor {
                player: self.player_id(),
                article: cast.article.unwrap(),
                effect: cast.effect,
                tick: self.tick_ms(),
                milliseconds: (cast.remaining * 1000.) as u32,
                speed: self.equipment.ship.tractor_speed,
                position,
                item: self.items.get(cast.template)?,
            }));
        }
        self.mining = Some(cast);
        Ok(())
    }

    fn check_mining_position(&self, id: u32) -> io::Result<()> {
        if !self.logged_in
            || self.sector.station.is_some()
            || self.hull <= 0.
            || self.skills.cloak_rank > 0
            || self.warp.active()
            || self.motion.speed.abs() > 0.5
            || self.motion.set_speed.abs() > 0.5
            || self.skill_catalog.level(PROSPECT_SKILL) == 0
            || self.account.character.profession != 2
            || self.target != id
        {
            return Err(invalid(
                "Prospecting requires a stopped, alive, uncloaked explorer outside warp.",
            ));
        }
        let definition = self
            .sector
            .resources
            .iter()
            .find(|node| node.object.id == id)
            .ok_or_else(|| invalid("Select a resource node first."))?;
        if definition.object.range_to(self.position) > self.prospect_range() {
            return Err(invalid("Resource is outside prospecting range."));
        }
        Ok(())
    }

    pub(super) fn open_resource_window(&mut self, target: u32) -> io::Result<()> {
        if self.resource_window == Some(self.target) {
            self.resource_window = None;
            self.pending.push(packets::resource_interface(false));
            return self.cancel_mining("Prospecting window closed.");
        }
        if target != 0 && target != self.target {
            return Err(invalid("Prospect action target mismatch."));
        }
        self.check_mining_position(self.target)?;
        if !lock_resources(&self.resource_field)?
            .get(self.target)?
            .available()
        {
            return Err(invalid("This resource is depleted."));
        }
        self.resource_window = Some(self.target);
        self.refresh_resource_target(true)?;
        self.pending.push(packets::resource_interface(true));
        Ok(())
    }

    pub(super) fn begin_mining(&mut self, transfer: ItemTransfer) -> io::Result<()> {
        // Source 18 is a mining command encoded as an inventory move. The
        // native click path leaves destination/quantity unspecified; C++ uses
        // only FromSlot here. Collection always chooses validated cargo space.
        if transfer.source != 18 || transfer.source_slot >= crate::resources::RESOURCE_SLOTS {
            return Err(invalid("Invalid prospect inventory request."));
        }
        self.check_mining_position(self.target)?;
        if self.resource_window != Some(self.target) {
            return Err(invalid("Open the prospecting window first."));
        }
        if self.mining.is_some() || self.loot_cast.is_some() {
            return Err(invalid("Prospecting is already active."));
        }
        let shared = Arc::clone(&self.resource_field);
        let mut field = lock_resources(&shared)?;
        let node = field.get(self.target)?;
        let item = node
            .contents
            .get(transfer.source_slot)
            .and_then(Option::as_ref)
            .ok_or_else(|| invalid("Resource slot is empty or invalid."))?;
        let template = self.items.get(item.template)?;
        let rank =
            self.skill_catalog.level(PROSPECT_SKILL) as f32 + self.equipment.ship.prospect_bonus;
        let cost = (42.5 * 2f32.powi(i32::from(template.level)) / 2f32.powf(rank - 1.)).max(10.);
        let quantity = item
            .quantity
            .min((self.energy / cost).floor().max(0.) as u32);
        if quantity == 0 {
            return Err(invalid("Insufficient reactor energy to prospect one item."));
        }
        // Legacy mining consumes the selected stack, limited by current energy;
        // the native Num field is not a requested partial-stack quantity.
        self.inventory
            .received(item.template, quantity, u32::MAX as usize, &self.items)?;
        let template = item.template;
        let generation = node.generation;
        let remaining = (cost * quantity as f32 / 25.).clamp(0.1, 100.);
        let before_harvested =
            self.already_harvested(self.target, generation, transfer.source_slot);
        field.claim(
            self.target,
            generation,
            MiningClaim {
                owner: self.player_id(),
                slot: transfer.source_slot,
                quantity,
            },
        )?;
        let effect = self.next_effect;
        self.next_effect = self.next_effect.wrapping_add(1);
        self.mining = Some(MiningCast {
            node: self.target,
            generation,
            slot: transfer.source_slot,
            quantity,
            template,
            before_harvested,
            remaining,
            energy_left: cost * quantity as f32,
            effect,
            article: None,
            phase: MiningPhase::Prospect,
        });
        self.emit_harvest_visual(packets::start_prospect(
            self.player_id(),
            self.target,
            effect,
            self.tick_ms(),
            (remaining * 1000.) as u32,
        ));
        println!(
            "Mining started: {}; node={}; slot={}; item={template}; quantity={quantity}",
            self.account.character.name, self.target, transfer.source_slot
        );
        Ok(())
    }

    pub(super) fn cancel_mining(&mut self, reason: &str) -> io::Result<()> {
        if let Some(cast) = self.mining.take() {
            lock_resources(&self.resource_field)?.release(cast.node, self.player_id());
            self.emit_harvest_visual(Frame::new(0xf, cast.effect.to_le_bytes().to_vec()));
            if let Some(article) = cast.article {
                self.emit_harvest_visual(Frame::new(7, article.to_le_bytes().to_vec()));
            }
            self.pending.push(packets::message(reason));
            println!(
                "Mining stopped: {}; node={}; reason={reason}",
                self.account.character.name, cast.node
            );
        }
        Ok(())
    }

    pub(super) fn advance_mining(&mut self, dt: f32) -> io::Result<()> {
        let Some(cast) = self.mining.as_ref() else {
            return self.refresh_resource_target(false);
        };
        if self.check_mining_position(cast.node).is_err() {
            return self.cancel_mining("Prospecting interrupted by ship state, target or range.");
        }
        let mut cast = self.mining.clone().unwrap();
        let step = dt.min(cast.remaining);
        if cast.phase == MiningPhase::Prospect && cast.remaining > 0. {
            let drain = cast.energy_left * step / cast.remaining;
            if self.energy + 0.001 < drain {
                return self.cancel_mining("Reactor depleted while prospecting.");
            }
            self.energy = (self.energy - drain).max(0.);
            cast.energy_left = (cast.energy_left - drain).max(0.);
        }
        cast.remaining = (cast.remaining - step).max(0.);
        self.mining = Some(cast.clone());
        if cast.remaining > 0. {
            return Ok(());
        }
        if cast.phase == MiningPhase::Prospect {
            let position = lock_resources(&self.resource_field)?
                .get(cast.node)?
                .definition
                .object
                .position;
            let speed = self.equipment.ship.tractor_speed;
            cast.remaining = (distance(self.position, position) / speed - 0.2).max(0.1);
            cast.phase = MiningPhase::Tractor;
            cast.article = Some(self.next_effect);
            self.next_effect = self.next_effect.wrapping_add(1);
            let effect = self.next_effect;
            self.next_effect = self.next_effect.wrapping_add(1);
            self.emit_harvest_visual(packets::tractor_ore(&packets::OreTractor {
                player: self.player_id(),
                article: cast.article.unwrap(),
                effect,
                tick: self.tick_ms(),
                milliseconds: (cast.remaining * 1000.) as u32,
                speed,
                position,
                item: self.items.get(cast.template)?,
            }));
            self.mining = Some(cast);
            return Ok(());
        }
        match self.collect_mined_item(&cast) {
            Ok(()) => {
                self.mining = None;
                self.refresh_resource_target(true)
            }
            Err(error) => self.cancel_mining(&format!("Mining collection stopped: {error}")),
        }
    }

    fn collect_mined_item(&mut self, cast: &MiningCast) -> io::Result<()> {
        let shared = Arc::clone(&self.resource_field);
        let mut field = lock_resources(&shared)?;
        let mutation = field.prepare_harvest(
            cast.node,
            cast.generation,
            &MiningClaim {
                owner: self.player_id(),
                slot: cast.slot,
                quantity: cast.quantity,
            },
            unix_ms(),
        )?;
        let mut candidate = self.inventory.received(
            cast.template,
            cast.quantity,
            u32::MAX as usize,
            &self.items,
        )?;
        candidate
            .harvests
            .insert(cast.node, mutation.receipt.clone());
        let xp =
            260 * u64::from(self.items.get(cast.template)?.level) * u64::from(cast.quantity) / 60; // development Explore level 50
        candidate.explore_xp = candidate
            .explore_xp
            .checked_add(xp)
            .ok_or_else(|| invalid("Exploration XP overflow."))?;
        self.pending.push(self.items.get(cast.template)?.packet());
        self.commit_inventory(candidate)?;
        field.commit_harvest(mutation);
        if let Some(article) = cast.article {
            self.emit_harvest_visual(Frame::new(7, article.to_le_bytes().to_vec()));
        }
        self.pending.push(packets::message(&format!(
            "Prospected {} {} (+{xp} exploration XP).",
            cast.quantity,
            self.items.get(cast.template)?.name
        )));
        println!(
            "Mining collected: {}; node={}; item={}; quantity={}; explore_xp={}",
            self.account.character.name,
            cast.node,
            cast.template,
            cast.quantity,
            self.inventory.explore_xp
        );
        Ok(())
    }

    pub(super) fn mining_drains_energy(&self) -> bool {
        self.mining
            .as_ref()
            .is_some_and(|cast| cast.phase == MiningPhase::Prospect)
    }

    pub(super) fn refresh_resource_target(&mut self, force: bool) -> io::Result<()> {
        if !self.logged_in || self.sector.station.is_some() {
            return Ok(());
        }
        let shared = Arc::clone(&self.resource_field);
        let field = lock_resources(&shared)?;
        let Ok(node) = field.get(self.target) else {
            return Ok(());
        };
        let revision = (
            self.target,
            node.generation,
            node.contents
                .iter()
                .map(|item| item.as_ref().map_or(0, |item| item.quantity))
                .collect(),
        );
        if force || self.resource_revision.as_ref() != Some(&revision) {
            for template in node
                .contents
                .iter()
                .flatten()
                .map(|item| item.template)
                .collect::<std::collections::BTreeSet<_>>()
            {
                self.pending.push(self.items.get(template)?.packet());
            }
            self.pending
                .push(packets::resource_contents(node, &self.items));
            self.resource_revision = Some(revision);
        }
        if !node.available() && self.resource_window.take().is_some() {
            self.pending.push(packets::resource_interface(false));
        }
        Ok(())
    }
}
