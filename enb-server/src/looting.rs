//! Native corpse targeting, credit permission and timed tractor collection.
//! The corpse retains its stock while reserved. Cargo and its receipt commit
//! before shared depletion, so cancellation and disk failure never destroy loot.
use super::*;
use crate::resources::unix_ms;

#[cfg(test)]
#[path = "loot_geometry_tests.rs"]
mod geometry_tests;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub(super) struct LootCast {
    corpse: u32,
    slot: usize,
    template: u32,
    quantity: u32,
    remaining: f32,
    article: u32,
    effect: u32,
}

impl LootCast {
    pub fn valid(&self) -> bool {
        self.slot < crate::loot::LOOT_SLOTS
            && self.quantity > 0
            && self.remaining.is_finite()
            && (0.0..=2000.).contains(&self.remaining)
            && (0x30000000..0x40000000).contains(&self.corpse)
    }
}

impl PlayerSession {
    pub(crate) fn request_loot_open(&mut self, target: u32, toggle: bool) -> io::Result<()> {
        if target != self.target {
            return Err(invalid("Loot target changed before opening."));
        }
        if toggle && self.loot_window.take().is_some() {
            self.cancel_looting("Loot window closed.")?;
            self.pending.push(packets::resource_interface(false));
            return Ok(());
        }
        self.open_loot()
    }
    pub(crate) fn request_loot_begin(&mut self, target: u32, slot: usize) -> io::Result<()> {
        if target != self.target {
            return Err(invalid("Loot target changed before collection."));
        }
        self.begin_looting(slot)
    }
    pub(crate) fn register_loot_receipts(&self, world: &mut LootWorld) -> io::Result<()> {
        world.register_receipts(self.player_id(), &self.inventory.loot_receipts)
    }

    pub(crate) fn attach_loot_world(&mut self, world: Arc<Mutex<LootWorld>>) -> io::Result<()> {
        if Arc::ptr_eq(&world, &self.loot_world) {
            return Ok(());
        }
        if let Some(cast) = &self.loot_cast {
            lock_loot(&self.loot_world)?.release(self.player_id(), cast.corpse);
        }
        self.loot_world = world;
        lock_loot(&self.loot_world)?.reconcile_combat(&mut *lock_combat(&self.combat)?, unix_ms());
        self.restore_loot_claim()
    }

    fn loot_group_allowed(&self, killer: u32) -> bool {
        self.group_contacts
            .iter()
            .any(|contact| contact.id == killer)
    }

    fn check_loot_position(&self, corpse: u32, range: f32) -> io::Result<()> {
        if !self.logged_in
            || self.sector.station.is_some()
            || self.hull <= 0.
            || self.warp.active()
            || self.target != corpse
        {
            return Err(invalid(
                "Loot requires a living ship in space, outside warp, targeting its corpse.",
            ));
        }
        let world = lock_loot(&self.loot_world)?;
        if !world.get(corpse)?.available(unix_ms())?
            || world.get(corpse)?.range_to(self.position)? > range
        {
            return Err(invalid("Corpse is empty, expired or outside loot range."));
        }
        Ok(())
    }

    pub(super) fn open_loot(&mut self) -> io::Result<()> {
        self.check_loot_position(self.target, 6000.)?;
        self.cancel_mining("Loot window opened.")?;
        if self.resource_window.take().is_some() {
            self.pending.push(packets::resource_interface(false));
        }
        let shared = Arc::clone(&self.loot_world);
        let mut world = lock_loot(&shared)?;
        let group = self.loot_group_allowed(world.get(self.target)?.killer);
        if let Some(collection) =
            world.prepare_collection(self.player_id(), self.target, None, unix_ms(), group)?
        {
            let credits = collection.credits;
            let candidate = self.inventory.collected_loot(&collection, &self.items)?;
            self.commit_inventory(candidate)?;
            collection.commit();
            if credits > 0 {
                self.pending
                    .push(packets::message(&format!("Looted {credits} credits.")));
            }
        }
        drop(world);
        self.loot_window = Some(self.target);
        self.refresh_loot_target(true)?;
        // Native Loot sends 0x51; permission uses network-order corpse ID.
        self.pending
            .push(Frame::new(0x8c, self.target.to_be_bytes().to_vec()));
        self.pending.push(packets::resource_interface(true));
        Ok(())
    }

    pub(super) fn begin_looting(&mut self, slot: usize) -> io::Result<()> {
        self.check_loot_position(self.target, 2000.)?;
        if self.loot_window != Some(self.target) {
            return Err(invalid("Open the loot window first."));
        }
        if self.loot_cast.is_some() || self.mining.is_some() {
            return Err(invalid("Tractor beam already in use."));
        }
        let shared = Arc::clone(&self.loot_world);
        let mut world = lock_loot(&shared)?;
        let corpse = world.get(self.target)?;
        let item = corpse
            .remaining()?
            .items
            .get(slot)
            .and_then(Clone::clone)
            .ok_or_else(|| invalid("Corpse slot is empty or invalid."))?;
        self.inventory
            .received(item.template, item.quantity, u32::MAX as usize, &self.items)?;
        let remaining =
            (distance(self.position, corpse.position) / self.equipment.ship.tractor_speed - 0.15)
                .max(0.1);
        let group = self.loot_group_allowed(corpse.killer);
        world.claim(self.player_id(), self.target, slot, unix_ms(), group)?;
        let cast = LootCast {
            corpse: self.target,
            slot,
            template: item.template,
            quantity: item.quantity,
            remaining,
            article: self.next_effect,
            effect: self.next_effect.wrapping_add(1),
        };
        self.next_effect = self.next_effect.wrapping_add(2);
        drop(world);
        self.send_loot_tractor(&cast)?;
        println!(
            "Loot started: {}; corpse={}; slot={slot}; item={}; quantity={}",
            self.account.character.name, self.target, item.template, item.quantity
        );
        self.loot_cast = Some(cast);
        Ok(())
    }

    fn send_loot_tractor(&mut self, cast: &LootCast) -> io::Result<()> {
        let position = lock_loot(&self.loot_world)?.get(cast.corpse)?.position;
        let mut frame = packets::tractor_ore(&packets::OreTractor {
            player: self.player_id(),
            article: cast.article,
            effect: cast.effect,
            tick: self.tick_ms(),
            milliseconds: (cast.remaining * 1000.) as u32,
            speed: self.equipment.ship.tractor_speed,
            position,
            item: self.items.get(cast.template)?,
        });
        frame.opcode = 0x2014; // Same packed fields, distinct proxy loot effect.
        self.emit_harvest_visual(frame);
        Ok(())
    }

    pub(super) fn cancel_looting(&mut self, reason: &str) -> io::Result<()> {
        if let Some(cast) = self.loot_cast.take() {
            lock_loot(&self.loot_world)?.release(self.player_id(), cast.corpse);
            self.emit_harvest_visual(Frame::new(0xf, cast.effect.to_le_bytes().to_vec()));
            self.emit_harvest_visual(Frame::new(7, cast.article.to_le_bytes().to_vec()));
            self.pending.push(packets::message(reason));
            println!(
                "Loot stopped: {}; corpse={}; reason={reason}",
                self.account.character.name, cast.corpse
            );
        }
        Ok(())
    }

    pub(super) fn restore_loot_claim(&mut self) -> io::Result<()> {
        let Some(cast) = self.loot_cast.clone() else {
            return Ok(());
        };
        let key = (u64::from(self.sector.id) << 32) | u64::from(cast.corpse);
        if !cast.valid()
            || self
                .inventory
                .loot_receipts
                .get(&key)
                .is_some_and(|receipt| receipt.slots[cast.slot])
        {
            return self.cancel_looting("Completed or incompatible loot checkpoint discarded.");
        }
        let result = {
            let mut world = lock_loot(&self.loot_world)?;
            match world.get(cast.corpse) {
                Ok(_) => {
                    // Restore a prior authenticated reservation before groups
                    // are rebuilt. Collection checks current permissions again.
                    world.claim(self.player_id(), cast.corpse, cast.slot, unix_ms(), true)
                }
                Err(error) => Err(error),
            }
        };
        if result.is_err() {
            self.cancel_looting("Loot claim could not be restored.")?;
        }
        Ok(())
    }

    pub(super) fn resume_loot_visuals(&mut self) -> io::Result<()> {
        if let Some(mut cast) = self.loot_cast.clone() {
            self.emit_harvest_visual(Frame::new(0xf, cast.effect.to_le_bytes().to_vec()));
            self.emit_harvest_visual(Frame::new(7, cast.article.to_le_bytes().to_vec()));
            cast.article = self.next_effect;
            cast.effect = self.next_effect.wrapping_add(1);
            self.next_effect = self.next_effect.wrapping_add(2);
            self.send_loot_tractor(&cast)?;
            self.loot_cast = Some(cast);
        }
        Ok(())
    }

    pub(super) fn advance_looting(&mut self, dt: f32) -> io::Result<()> {
        if let Some(mut cast) = self.loot_cast.clone() {
            if self.check_loot_position(cast.corpse, 2000.).is_err() {
                return self.cancel_looting("Loot interrupted by ship state, target or range.");
            }
            cast.remaining = (cast.remaining - dt).max(0.);
            self.loot_cast = Some(cast.clone());
            if cast.remaining == 0. {
                let result = self.collect_loot(&cast);
                if let Err(error) = result {
                    self.cancel_looting(&format!("Loot collection stopped: {error}"))?;
                } else {
                    self.loot_cast = None;
                    self.emit_harvest_visual(Frame::new(7, cast.article.to_le_bytes().to_vec()));
                }
            }
        }
        self.refresh_loot_target(false)
    }

    fn collect_loot(&mut self, cast: &LootCast) -> io::Result<()> {
        let shared = Arc::clone(&self.loot_world);
        let mut world = lock_loot(&shared)?;
        let group = self.loot_group_allowed(world.get(cast.corpse)?.killer);
        let collection = world
            .prepare_collection(
                self.player_id(),
                cast.corpse,
                Some(cast.slot),
                unix_ms(),
                group,
            )?
            .ok_or_else(|| invalid("Loot collection produced no item."))?;
        if collection
            .item
            .as_ref()
            .is_none_or(|item| item.template != cast.template || item.quantity != cast.quantity)
        {
            return Err(invalid("Loot contents changed during tractor collection."));
        }
        let candidate = self.inventory.collected_loot(&collection, &self.items)?;
        self.commit_inventory(candidate)?;
        collection.commit();
        self.pending.push(packets::message(&format!(
            "Looted {} {}.",
            cast.quantity,
            self.items.get(cast.template)?.name
        )));
        println!(
            "Loot collected: {}; corpse={}; item={}; quantity={}",
            self.account.character.name, cast.corpse, cast.template, cast.quantity
        );
        Ok(())
    }

    pub(super) fn refresh_loot_target(&mut self, force: bool) -> io::Result<()> {
        if !self.logged_in
            || self.sector.station.is_some()
            || !self.visible_corpses.contains_key(&self.target)
        {
            return Ok(());
        }
        let world = lock_loot(&self.loot_world)?;
        let Ok(corpse) = world.get(self.target) else {
            return Ok(());
        };
        let stock = corpse.remaining()?;
        let quantities = stock
            .items
            .iter()
            .map(|item| item.as_ref().map_or(0, |item| item.quantity))
            .collect::<Vec<_>>();
        if force || self.loot_revision.as_ref() != Some(&(self.target, quantities.clone())) {
            for item in stock.items.iter().flatten() {
                self.pending.push(self.items.get(item.template)?.packet());
            }
            self.pending
                .push(packets::corpse_contents(corpse, &stock, &self.items));
            self.loot_revision = Some((self.target, quantities));
        }
        Ok(())
    }

    pub(crate) fn publish_corpses(&mut self) -> io::Result<()> {
        if !self.logged_in || self.sector.station.is_some() {
            return Ok(());
        }
        if self
            .last_loot_publish
            .is_some_and(|at| at.elapsed() < Duration::from_millis(200))
        {
            return self.refresh_loot_target(false);
        }
        self.last_loot_publish = Some(Instant::now());
        let world = lock_loot(&self.loot_world)?;
        let mut wanted = HashMap::new();
        for corpse in world.corpses() {
            if corpse.range_to(self.position)? <= 20_000. && corpse.available(unix_ms())? {
                wanted.insert(corpse.id, corpse.position);
                if !self.visible_corpses.contains_key(&corpse.id) {
                    self.pending.push(packets::corpse_create(corpse));
                    self.pending.push(packets::corpse_name(corpse));
                }
            }
        }
        for id in self
            .visible_corpses
            .keys()
            .filter(|id| !wanted.contains_key(id))
        {
            self.pending.push(Frame::new(7, id.to_le_bytes().to_vec()));
            if self.loot_window == Some(*id) {
                self.loot_window = None;
                self.pending.push(packets::resource_interface(false));
            }
        }
        self.visible_corpses = wanted;
        drop(world);
        self.refresh_loot_target(false)
    }
}
