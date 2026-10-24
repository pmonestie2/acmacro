//! Gate and wormhole handoff share the original JoinData packet. A transition
//! replaces sector content only after a valid destination has been resolved.
use super::*;

impl PlayerSession {
    pub(super) fn begin_docking(&mut self, target: u32) -> io::Result<()> {
        let station = self
            .sector
            .navpoints
            .iter()
            .find(|nav| nav.id == target && nav.object_type == 12)
            .ok_or_else(|| invalid("Select a station exterior first."))?;
        if !self.logged_in
            || self.target != target
            || self.hull <= 0.
            || distance(self.position, station.position) > 5000.
            || self.warp.active()
        {
            return Err(invalid(
                "Dock alive, outside warp, within 5000m of the selected station.",
            ));
        }
        let destination = station
            .gate_destination
            .ok_or_else(|| invalid("Station has no interior destination."))?;
        if self.sectors.sector(destination)?.station.is_none() {
            return Err(invalid("Station interior is unavailable."));
        }
        self.detach_formation();
        self.stop_following();
        self.motion.stop();
        self.docking_target = Some(target);
        let mut camera = Vec::from(5u32.to_be_bytes());
        camera.extend_from_slice(&target.to_be_bytes());
        self.pending.push(Frame::new(0x92, camera));
        self.pending
            .push(packets::message("Docking clearance granted."));
        Ok(())
    }
    pub(super) fn complete_docking(&mut self, target: u32) -> io::Result<()> {
        if self.docking_target != Some(target) || self.hull <= 0. {
            return Err(invalid("Docking was not authorized for this station."));
        }
        let destination = self
            .sector
            .navpoints
            .iter()
            .find(|nav| nav.id == target)
            .and_then(|nav| nav.gate_destination)
            .ok_or_else(|| invalid("Station destination disappeared."))?;
        self.enter_station(destination)
    }

    /// Docking/towing restore the ship only if station entry commits. Before
    /// the journal rename a rejected transfer must retain its original health.
    pub(super) fn enter_station(&mut self, destination: u32) -> io::Result<()> {
        if self.sectors.sector(destination)?.station.is_none() {
            return Err(invalid("Destination is not a station."));
        }
        let before = (
            self.home_station,
            self.skills.clone(),
            self.hull,
            self.shield,
            self.energy,
        );
        self.home_station = destination;
        self.skills = SkillState::default();
        self.hull = self.equipment.ship.hull_capacity;
        self.shield = self.equipment.ship.shield_capacity;
        self.energy = self.equipment.ship.energy_capacity;
        let result = self.transfer_sector(destination, false);
        if result.is_err() {
            (
                self.home_station,
                self.skills,
                self.hull,
                self.shield,
                self.energy,
            ) = before;
        }
        result
    }
    pub(super) fn station_request(&mut self, station_id: u32, action: u8) -> io::Result<()> {
        let station = self
            .sector
            .station
            .clone()
            .ok_or_else(|| invalid("Enter a station first."))?;
        if !self.logged_in {
            return Err(invalid("Station session has not started."));
        }
        match action {
            4 => self.open_npc(station_id),
            1 => {
                if station_id != 0 && station_id != station.id {
                    return Err(invalid("Station launch identity mismatch."));
                }
                // transfer_sector clears pending frames, so put the departure
                // notification before the retained handoff after it returns.
                self.transfer_sector(station.parent, false)?;
                self.pending.insert(0, packets::starbase_set(station.id, 1));
                Ok(())
            }
            _ => {
                self.pending.push(packets::message(
                    "This station service is under development.",
                ));
                Ok(())
            }
        }
    }
    pub(super) fn transfer_sector(
        &mut self,
        destination: u32,
        use_return_gate: bool,
    ) -> io::Result<()> {
        self.recover_weapon_launch()?;
        if self.transition_pending.is_some() {
            return Err(invalid(
                "A committed sector transition is waiting for storage.",
            ));
        }
        if self.inventory.refine_job.is_some()
            || self.inventory.craft_job.is_some()
            || self.inventory.production_target.iter().any(Option::is_some)
        {
            return Err(invalid(
                "Finish or cancel production and return the analyzer sample to cargo before leaving.",
            ));
        }
        let previous = self.sector.id;
        // The small loop-gate fixture deliberately re-enters its own content.
        let next = if destination == previous {
            self.sector.clone()
        } else {
            self.sectors.sector(destination)?
        };
        let mut resources = ResourceField::load(
            &next,
            &self.items,
            self.world_root.join(format!("resources/{}.json", next.id)),
        )?;
        resources.register_receipts(self.player_id(), &self.inventory.harvests)?;
        self.cancel_mining("Sector transition.")?;
        self.cancel_looting("Sector transition.")?;
        let mut loot = LootWorld::load(
            self.world_root.join(format!("loot/{}.json", next.id)),
            next.id,
            &self.items,
        )?;
        loot.register_receipts(self.player_id(), &self.inventory.loot_receipts)?;
        let position = if destination == previous {
            self.position
        } else if use_return_gate {
            next.navpoints
                .iter()
                .find(|nav| nav.object_type == 11 && nav.gate_destination == Some(previous))
                .map(|nav| {
                    std::array::from_fn(|axis| {
                        let toward_center = -nav.position[axis];
                        (nav.position[axis] + toward_center.signum() * 2000.)
                            .clamp(next.bounds.min[axis], next.bounds.max[axis])
                    })
                })
                .unwrap_or([0.; 3])
        } else if let Some(station) = self
            .sector
            .station
            .as_ref()
            .filter(|station| station.parent == destination)
        {
            let exterior = next
                .navpoints
                .iter()
                .find(|nav| nav.id == station.exterior)
                .ok_or_else(|| invalid("Station launch exterior is missing."))?;
            std::array::from_fn(|axis| {
                (exterior.position[axis] + if axis == 0 { 4500. } else { 0. })
                    .clamp(next.bounds.min[axis], next.bounds.max[axis])
            })
        } else {
            [0.; 3]
        };
        let mut join =
            packets::character_ticket(next.id, &self.nonce, self.player_id()).body[4..].to_vec();
        // MasterJoin.FromSectorID is required for a real cross-sector native
        // transition. A loop-gate test cannot detect a missing source sector.
        join[24..28].copy_from_slice(&previous.to_be_bytes());
        for name in [
            &self.sector.name,
            &self.sector.system,
            &next.name,
            &next.system,
        ] {
            crate::protocol::string(&mut join, name);
        }
        // The journal commit precedes every location/transport mutation below.
        let transition = self.prepare_transition(&next, position, &join)?;
        self.sector = next;
        self.workbench = None;
        self.workbench_lab_ready = false;
        self.loot_world = Arc::new(Mutex::new(loot));
        self.visible_corpses.clear();
        self.loot_window = None;
        self.loot_revision = None;
        self.last_loot_publish = None;
        self.resource_field = Arc::new(Mutex::new(resources));
        self.resource_window = None;
        self.resource_revision = None;
        self.npc_interaction = None;
        self.position = position;
        self.stop_following();
        self.motion.stop();
        self.warp.reset(&mut self.motion);
        self.warp_effects.clear();
        self.last_motion_report = None;
        self.target = u32::MAX;
        self.stop_weapons();
        self.combat = Arc::new(Mutex::new(SectorCombat::new(&self.sector.mobs)));
        self.mob_states = vec![MobState::Idle; self.sector.mobs.len()];
        self.mob_visible = vec![false; self.sector.mobs.len()];
        self.mob_relationships = vec![None; self.sector.mobs.len()];
        self.logged_in = false;
        self.gate_transit = None;
        self.visible_resources.clear();
        self.last_resource_visibility = None;
        self.docking_target = None;
        self.station_pose = None;
        self.pending.clear();
        self.station_peers.clear();
        self.station_view_dirty = true;
        self.handoff = Some(join.clone());
        self.transition_pending = Some(transition);
        if let Err(error) = self.finish_transition() {
            println!("Sector transition committed; waiting for storage: {}; sector={destination}; reason={error}",self.account.character.name);
        }
        println!(
            "Sector handoff: {}; {previous} -> {destination}",
            self.account.character.name
        );
        Ok(())
    }
}
