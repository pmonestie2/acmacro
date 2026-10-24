//! Reconcile owned equipment with cached combat/movement parameters. All calls
//! run inside the authoritative player simulation, never from a socket worker.
use super::*;

impl PlayerSession {
    pub(super) fn ship_resources(&self) -> packets::ShipResources {
        packets::ShipResources {
            energy: self.energy,
            shield: self.shield,
            hull: self.hull,
        }
    }

    pub(super) fn refresh_equipment(&mut self) -> io::Result<()> {
        self.equipment = EquipmentStats::derive(&self.inventory, &self.items, &self.skill_catalog)?;
        self.reputation = self.factions.reactions(
            self.account.character.class(),
            &self.inventory.faction_standings,
        )?;
        self.apply_equipment_parameters();
        Ok(())
    }

    pub(super) fn apply_equipment_parameters(&mut self) {
        let stats = self.equipment.ship;
        self.energy = self.energy.min(stats.energy_capacity).max(0.);
        self.shield = self.shield.min(stats.shield_capacity).max(0.);
        self.hull = self.hull.min(stats.hull_capacity).max(0.);
        self.motion.max_speed = stats.impulse_speed;
        self.motion.acceleration = stats.acceleration;
        self.motion.turn_scale = stats.turn_rate / 45.;
        self.warp.speed = stats.warp_speed;
        self.warp.recovery_seconds = stats.warp_recovery;
        for slot in 0..20 {
            if !self
                .equipment
                .weapons
                .iter()
                .any(|weapon| weapon.slot == slot)
            {
                self.weapon_firing[slot] = false;
            }
        }
        self.player_firing = self.weapon_firing.iter().any(|firing| *firing);
        if stats.warp_speed == 0. && self.warp.active() {
            let event = self.warp.finish(FinishReason::Cancelled, &mut self.motion);
            self.emit_warp_event(event);
        }
        if !self.warp.active() {
            self.motion.speed = self
                .motion
                .speed
                .clamp(-stats.impulse_speed / 2., stats.impulse_speed);
            self.motion.set_speed = self
                .motion
                .set_speed
                .clamp(-stats.impulse_speed / 2., stats.impulse_speed);
        }
        if self.logged_in {
            self.pending.push(packets::ship_parameters(
                self.player_id(),
                &stats,
                self.ship_resources(),
            ));
            self.publish_weapon_states();
        }
    }

    pub(super) fn stop_weapons(&mut self) {
        self.player_firing = false;
        self.weapon_firing.fill(false);
        if self.logged_in {
            self.publish_weapon_states();
        }
    }

    pub(super) fn toggle_all_weapons(&mut self) {
        let enabled = !self.weapon_firing.iter().any(|firing| *firing);
        self.weapon_firing.fill(false);
        for weapon in &self.equipment.weapons {
            self.weapon_firing[weapon.slot] = enabled;
        }
        self.player_firing = enabled;
        self.publish_weapon_states();
    }

    pub(super) fn publish_weapon_states(&mut self) {
        let slots: Vec<usize> = self
            .equipment
            .weapons
            .iter()
            .map(|weapon| weapon.slot)
            .collect();
        for slot in slots {
            self.publish_weapon_state(slot);
        }
    }

    pub(super) fn publish_weapon_state(&mut self, slot: usize) {
        if let Some(weapon) = self
            .equipment
            .weapons
            .iter()
            .find(|weapon| weapon.slot == slot)
        {
            let cooling = self.weapon_cooldowns[slot] > 0.;
            let state = 0x2000
                | u32::from(self.weapon_firing[slot])
                | if cooling { 0x80 } else { 0 }
                | if weapon.item_type != 14 && weapon.ammunition.is_none() {
                    8
                } else {
                    0
                };
            let ready = if cooling {
                self.tick_ms()
                    .wrapping_add((self.weapon_cooldowns[slot] * 1000.) as u32)
            } else {
                0
            };
            self.pending.push(packets::weapon_state(
                self.player_id(),
                slot,
                ready,
                weapon.range,
                state,
            ));
        }
    }

    pub(crate) fn equip_cargo(&mut self, argument: &str) -> io::Result<()> {
        let slots = argument
            .split_whitespace()
            .map(str::parse::<usize>)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| invalid("Use /equip CARGO_SLOT MOUNT_SLOT (zero-based slots)."))?;
        if slots.len() != 2 {
            return Err(invalid(
                "Use /equip CARGO_SLOT MOUNT_SLOT (zero-based slots).",
            ));
        }
        self.transfer_inventory(ItemTransfer {
            source: 1,
            source_slot: slots[0],
            destination: 2,
            destination_slot: slots[1],
            quantity: 1,
        })
    }

    pub(crate) fn unequip(&mut self, argument: &str) -> io::Result<()> {
        let slot = argument
            .parse::<usize>()
            .map_err(|_| invalid("Use /unequip MOUNT_SLOT (zero-based)."))?;
        let cargo = self
            .inventory
            .cargo
            .iter()
            .position(Option::is_none)
            .ok_or_else(|| invalid("Cargo is full."))?;
        self.transfer_inventory(ItemTransfer {
            source: 2,
            source_slot: slot,
            destination: 1,
            destination_slot: cargo,
            quantity: 1,
        })
    }

    pub(super) fn activate_equipped_item(&mut self, body: &[u8]) -> io::Result<()> {
        if body.len() != 6
            || !self.logged_in
            || self.hull <= 0.
            || self.warp.active()
            || self.sector.station.is_some()
        {
            return Err(invalid(
                "Equipped item unavailable in the current ship state.",
            ));
        }
        let mut reader = Reader::new(body);
        let owner = reader.u32()?;
        let inventory = reader.take(1)?[0];
        let slot = reader.take(1)?[0] as usize;
        if owner != self.player_id() || inventory != 2 || slot >= 20 {
            return Err(invalid("Invalid equipment activation owner or slot."));
        }
        if self
            .equipment
            .weapons
            .iter()
            .any(|weapon| weapon.slot == slot)
        {
            self.weapon_firing[slot] = !self.weapon_firing[slot];
            self.player_firing = self.weapon_firing.iter().any(|firing| *firing);
            self.publish_weapon_state(slot);
            if self.skills.cloak_rank > 0 {
                self.stop_cloak();
            }
        } else {
            self.pending.push(packets::message(
                "This item activation is not implemented yet.",
            ));
        }
        Ok(())
    }
}
