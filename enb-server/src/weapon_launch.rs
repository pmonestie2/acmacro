//! Weapons use one frozen volley transaction. The inventory journal commits
//! ammunition and pending rounds; the sector ledger then admits them once.
//! Resource/reload state is checkpointed before retiring the inventory journal.
use super::*;
use crate::equipment_stats::WeaponStats;
use crate::projectiles::{burst_interval_ms, travel_ms, LaunchJournal, Projectile};
use crate::weapon_accuracy::{roll_weapon_damage, weapon_in_firing_arc, HIT_ROLL_MAX};
use rand::Rng;

impl PlayerSession {
    fn prepare_weapon_launch(
        &self,
        roll: &mut impl FnMut() -> u32,
    ) -> io::Result<Option<Inventory>> {
        let combat = lock_combat(&self.combat)?;
        let Some(index) = self.target.checked_sub(MOB_ID).map(|index| index as usize) else {
            return Ok(None);
        };
        let Some(target) = combat
            .actors
            .get(index)
            .filter(|mob| self.mob_visible_in_scan(index, mob))
        else {
            return Ok(None);
        };
        let mut simulated = target.clone();
        let source = target.definition.source_id;
        let template = target.definition.template_id;
        let generation = target.death_generation;
        let range = target.range_to(self.position);
        // Archived Equipable::CheckRange adds scaled asset radius separately
        // from Object::RangeFrom's unscaled surface offset. At scale1 both
        // contributions are present; projectile flight uses only surface range.
        let target_size = if target.definition.asset == 0 {
            0.
        } else {
            target.definition.radius * target.definition.scale
        };
        let target_position = target.position;
        let heading = self.motion.heading();
        let mut sequence = self
            .inventory
            .shot_sequence
            .max(combat.accepted_sequence(self.player_id()));
        drop(combat);
        let mut candidate = self.inventory.clone();
        let now = crate::resources::unix_ms();
        let mut journal = LaunchJournal {
            owner: self.player_id(),
            sector: self.sector.id,
            sequence,
            energy_after: self.energy,
            created_ms: now,
            reloads: [0; 20],
            reload_seconds: [0.; 20],
            next_effect: self.next_effect,
            projectiles: Vec::new(),
        };
        for weapon in &self.equipment.weapons {
            if simulated.state == MobState::Dead {
                break;
            }
            if !self.weapon_firing[weapon.slot]
                || self.weapon_cooldowns[weapon.slot] > 0.
                || journal.energy_after < weapon.energy
                || range > weapon.range + target_size
                || weapon.damage <= 0.
            {
                continue;
            }
            // Equipable::UseWeapon checks the three-dimensional beam/PL cone
            // before consuming energy or ammunition. Turning away pauses this
            // mount without switching off the player's auto-fire intention.
            if !weapon_in_firing_arc(weapon.item_type, heading, self.position, target_position)? {
                continue;
            }
            let before = journal.projectiles.len();
            for round in 0..weapon.rounds {
                if simulated.state == MobState::Dead {
                    break;
                }
                let stats: WeaponStats = if weapon.item_type == 14 {
                    weapon.clone()
                } else {
                    let Some(ammo) = candidate.ammunition[weapon.slot].as_ref() else {
                        break;
                    };
                    if Some(ammo.template) == weapon.ammunition {
                        weapon.clone()
                    } else {
                        EquipmentStats::derive(&candidate, &self.items, &self.skill_catalog)?
                            .weapons
                            .into_iter()
                            .find(|stats| stats.slot == weapon.slot)
                            .ok_or_else(|| {
                                invalid("Launcher disappeared during volley preparation.")
                            })?
                    }
                };
                if weapon.item_type != 14 {
                    candidate.consume_ammunition(weapon.slot, &self.items)?;
                }
                sequence = sequence
                    .checked_add(1)
                    .ok_or_else(|| invalid("Weapon sequence exhausted."))?;
                let duration = travel_ms(weapon.item_type, range);
                let emitted = now.saturating_add(
                    burst_interval_ms(weapon.reload, weapon.rounds) * u64::from(round),
                );
                let damage = stats.damage
                    * if self.skills.combat_cloak_seconds > 0. {
                        2.
                    } else {
                        1.
                    };
                // One roll determines miss/critical after ordinary damage
                // bonuses. Freeze both wire outcome and damage in the journal;
                // admission/checkpoint recovery must never roll a second time.
                let result = roll_weapon_damage(damage, roll(), stats.hit_chances)?;
                let shot = Projectile {
                    owner: self.player_id(),
                    sector: self.sector.id,
                    slot: weapon.slot,
                    sequence,
                    target: source,
                    template,
                    generation,
                    emitted_ms: emitted,
                    due_ms: emitted + u64::from(duration),
                    duration_ms: if weapon.item_type == 14 {
                        500
                    } else {
                        duration
                    },
                    effect: stats.effect,
                    effect_id: journal.next_effect,
                    visual_sent: false,
                    damage: result.damage,
                    damage_type: stats.damage_type,
                    inflicted: result.outcome.inflicted(),
                };
                journal.next_effect = journal.next_effect.wrapping_add(1);
                if shot.due_ms <= now {
                    simulated.damage_by(result.damage, self.player_id());
                }
                journal.projectiles.push(shot);
            }
            if journal.projectiles.len() > before {
                journal.energy_after -= weapon.energy;
                journal.reload_seconds[weapon.slot] = weapon.reload;
                journal.reloads[weapon.slot] =
                    now.saturating_add((weapon.reload * 1000.).ceil() as u64);
            }
        }
        if journal.projectiles.is_empty() {
            return Ok(None);
        }
        journal.sequence = sequence;
        candidate.shot_sequence = sequence;
        candidate.pending_launch = Some(journal);
        candidate.validate_for_profile(
            &self.account.character,
            &self.items,
            &self.skill_catalog,
        )?;
        Ok(Some(candidate))
    }

    fn apply_launch_resources(&mut self, journal: &LaunchJournal, fresh: bool) {
        if self.last_launch < journal.sequence {
            self.energy = journal
                .energy_after
                .min(self.equipment.ship.energy_capacity);
            self.next_effect = journal.next_effect;
            self.last_launch = journal.sequence;
        }
        let now = crate::resources::unix_ms();
        for slot in 3..9 {
            if journal.reloads[slot] > 0 {
                self.weapon_cooldowns[slot] = if fresh {
                    journal.reload_seconds[slot]
                } else {
                    journal.reloads[slot].saturating_sub(now) as f32 / 1000.
                };
            }
        }
        for weapon in &self.equipment.weapons {
            if weapon.item_type != 14 && self.inventory.ammunition[weapon.slot].is_none() {
                self.weapon_firing[weapon.slot] = false;
            }
        }
        self.player_firing = self.weapon_firing.iter().any(|firing| *firing);
        self.publish_weapon_states();
        self.pending.push(packets::ship_resources(
            self.player_id(),
            &self.equipment.ship,
            Some(self.energy),
            None,
            None,
        ));
    }

    fn complete_weapon_launch(&mut self, journal: &LaunchJournal) -> io::Result<()> {
        if journal.owner != self.player_id() || journal.sector != self.sector.id {
            return Err(invalid(
                "Weapon journal belongs to another character or sector.",
            ));
        }
        {
            let mut combat = lock_combat(&self.combat)?;
            combat.admit(&journal.projectiles)?;
            combat.resolve_due(crate::resources::unix_ms())?;
        }
        self.checkpoint_session(true)?;
        let mut inventory = self.inventory.clone();
        inventory.pending_launch = None;
        // Journal retirement changes no client-visible item fields. Do not
        // publish a second full cargo/vault update for the same volley.
        crate::durable_json::replace(&self.state_dir.join("inventory.json"), &inventory)?;
        self.inventory = inventory;
        Ok(())
    }

    pub(crate) fn recover_weapon_launch(&mut self) -> io::Result<()> {
        let Some(journal) = self.inventory.pending_launch.clone() else {
            return Ok(());
        };
        self.apply_launch_resources(&journal, false);
        self.complete_weapon_launch(&journal)
    }

    pub(crate) fn publish_weapon_damage(&mut self, report: &crate::projectiles::DamageReport) {
        if !self.logged_in
            || self.sector.station.is_some()
            || (report.owner != self.player_id() && !self.peer_ids.contains(&report.owner))
        {
            return;
        }
        let Some(index) = report.target.checked_sub(MOB_ID) else {
            return;
        };
        if !self
            .mob_visible
            .get(index as usize)
            .copied()
            .unwrap_or(false)
        {
            return;
        }
        self.pending.push(packets::client_damage(
            report.target,
            report.owner,
            report.damage,
            0.,
            report.damage_type as u32,
            report.inflicted,
        ));
    }

    pub(crate) fn publish_projectile_visual(
        &mut self,
        visual: &crate::projectiles::ProjectileVisual,
    ) {
        if !self.logged_in
            || self.sector.station.is_some()
            || (visual.owner != self.player_id() && !self.peer_ids.contains(&visual.owner))
        {
            return;
        }
        let Some(index) = visual.target.checked_sub(MOB_ID) else {
            return;
        };
        if !self
            .mob_visible
            .get(index as usize)
            .copied()
            .unwrap_or(false)
        {
            return;
        }
        self.pending.push(packets::weapon_effect(
            visual.owner,
            visual.target,
            visual.effect,
            visual.effect_id,
            self.tick_ms(),
            visual.duration_ms,
        ));
    }

    pub(super) fn fire_weapons(&mut self) -> io::Result<()> {
        let mut random = rand::thread_rng();
        self.fire_weapons_with_roll(|| random.gen_range(1..=HIT_ROLL_MAX))
    }

    /// Supplying rolls separates deterministic behavioral tests from the live
    /// random source. The callback runs once per newly prepared round only.
    pub(super) fn fire_weapons_with_roll(
        &mut self,
        mut roll: impl FnMut() -> u32,
    ) -> io::Result<()> {
        self.recover_weapon_launch()?;
        if self.hull <= 0.
            || self.warp.active()
            || self.skills.cloak_rank > 0
            || self.gate_transit.is_some()
            || self.sector.station.is_some()
        {
            return Ok(());
        }
        if let Some(candidate) = self.prepare_weapon_launch(&mut roll)? {
            let journal = candidate
                .pending_launch
                .clone()
                .expect("prepared weapon journal");
            self.commit_inventory(candidate)?;
            self.apply_launch_resources(&journal, true);
            self.complete_weapon_launch(&journal)?;
        }
        let dead = if let Some(index) = self.target.checked_sub(MOB_ID) {
            lock_combat(&self.combat)?
                .actors
                .get(index as usize)
                .is_some_and(|mob| mob.state == MobState::Dead)
        } else {
            false
        };
        if dead && self.weapon_firing.iter().any(|firing| *firing) {
            self.stop_weapons();
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inventory::ItemInstance;
    use std::path::Path;

    fn armed(launcher: u32, ammo: u32) -> PlayerSession {
        let mut ship = super::super::tests::session();
        ship.logged_in = true;
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22343".parse().unwrap());
        ship.position = [0.; 3];
        ship.target = MOB_ID;
        ship.inventory.equipped[3] = Some(ItemInstance::stock(launcher, 1));
        ship.inventory.ammunition[3] = Some(ItemInstance::stock(ammo, 1));
        ship.inventory.cargo[0] = Some(ItemInstance::stock(ammo, 5));
        ship.refresh_equipment().unwrap();
        ship.energy = 100.;
        ship.weapon_firing[3] = true;
        ship.player_firing = true;
        lock_combat(&ship.combat).unwrap().actors[0].position = [1000., 0., 0.];
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let path = ship.state_dir.join("combat/1076.json");
        let persistent =
            SectorCombat::persistent(path, 1076, &lock_combat(&ship.combat).unwrap()).unwrap();
        ship.combat = Arc::new(Mutex::new(persistent));
        ship
    }
    fn restore(original: &PlayerSession, path: &Path) -> PlayerSession {
        let mut restored = PlayerSession::new(
            original.sector.clone(),
            original.bind,
            original.state_dir.clone(),
        )
        .unwrap();
        let combat = SectorCombat::persistent(
            path.to_path_buf(),
            1076,
            &lock_combat(&restored.combat).unwrap(),
        )
        .unwrap();
        restored.combat = Arc::new(Mutex::new(combat));
        restored
    }

    #[test]
    fn player_weapon_reach_uses_both_native_radius_offsets_but_flight_uses_surface_range() {
        let ship = armed(3022, 512);
        let reach = ship.equipment.weapons[0].range;
        {
            let mut combat = lock_combat(&ship.combat).unwrap();
            let target = &mut combat.actors[0];
            target.definition.radius = 500.;
            target.definition.scale = 2.;
            target.position = [reach + 1500., 0., 0.];
        }
        let candidate = ship.prepare_weapon_launch(&mut || 5000).unwrap().unwrap();
        let journal = candidate.pending_launch.unwrap();
        assert!(!journal.projectiles.is_empty());
        assert_eq!(
            journal.projectiles[0].duration_ms,
            travel_ms(16, reach + 1000.)
        );
        // Preparing does not spend resources; a center just beyond the two
        // native offsets must not create a second speculative volley.
        lock_combat(&ship.combat).unwrap().actors[0].position[0] += 1.;
        assert!(ship.prepare_weapon_launch(&mut || 5000).unwrap().is_none());
        assert_eq!(ship.energy, 100.);
        assert_eq!(ship.inventory.ammunition[3].as_ref().unwrap().quantity, 1);
    }

    #[test]
    fn player_visibility_measures_to_npc_surface_without_applying_visual_scale() {
        let ship = armed(3022, 512);
        let limit = ship.equipment.ship.scan_range + crate::mob_targeting::MOB_SIGNATURE;
        let mut combat = lock_combat(&ship.combat).unwrap();
        let mob = &mut combat.actors[0];
        mob.definition.radius = 500.;
        mob.definition.scale = 10.;
        mob.position = [limit + 499., 0., 0.];
        assert!(ship.mob_visible_in_scan(0, mob));
        mob.position[0] = limit + 500.;
        assert!(!ship.mob_visible_in_scan(0, mob));
    }

    #[test]
    fn beam_and_projectile_cones_pause_without_cost_and_resume_when_the_ship_turns() {
        for weapon in [769, 3022] {
            let mut ship = armed(3022, 512);
            if weapon == 769 {
                ship.inventory.equipped[3] = Some(ItemInstance::stock(769, 1));
                ship.inventory.ammunition[3] = None;
                ship.refresh_equipment().unwrap();
            }
            lock_combat(&ship.combat).unwrap().actors[0].position = [-100., 0., 0.];
            crate::durable_json::replace(&ship.state_dir.join("inventory.json"), &ship.inventory)
                .unwrap();
            let inventory = serde_json::to_vec(&ship.inventory).unwrap();
            let disk = fs::read(ship.state_dir.join("inventory.json")).unwrap();
            let pending = ship.pending.len();
            let effect = ship.next_effect;
            let cooldowns = ship.weapon_cooldowns;
            let energy = ship.energy;
            let mut rolls = 0;
            for _ in 0..2 {
                ship.fire_weapons_with_roll(|| {
                    rolls += 1;
                    5000
                })
                .unwrap();
            }
            assert_eq!(rolls, 0);
            assert_eq!(serde_json::to_vec(&ship.inventory).unwrap(), inventory);
            assert_eq!(
                fs::read(ship.state_dir.join("inventory.json")).unwrap(),
                disk
            );
            assert_eq!(ship.energy, energy);
            assert_eq!(ship.weapon_cooldowns, cooldowns);
            assert_eq!(ship.next_effect, effect);
            assert_eq!(ship.pending.len(), pending);
            assert!(ship.weapon_firing[3] && ship.player_firing);
            assert!(lock_combat(&ship.combat)
                .unwrap()
                .pending_projectiles()
                .is_empty());
            assert_eq!(lock_combat(&ship.combat).unwrap().actors[0].shield, 150.);
            // Auto-fire stays selected: rotating the native +X model heading
            // is sufficient to launch; the user need not toggle the mount.
            ship.motion.face([-1., 0., 0.]).unwrap();
            ship.fire_weapons_with_roll(|| {
                rolls += 1;
                5000
            })
            .unwrap();
            assert_eq!(rolls, 1);
            assert_eq!(ship.inventory.shot_sequence, 1);
            assert!(ship.energy < energy && ship.weapon_cooldowns[3] > 0.);
            assert!(ship.weapon_firing[3] && ship.player_firing);
        }
    }

    #[test]
    fn actual_missile_launcher_fires_behind_the_native_ship_heading() {
        let mut ship = armed(2656, 507); // Iron Dragonclaw and owned missiles
        ship.account.character.race = 0;
        ship.account.character.profession = 0;
        ship.skill_catalog = SkillCatalog::for_profile(&ship.account.character).unwrap();
        for (slot, template) in [(0, 3628), (1, 3184), (2, 2507)] {
            ship.inventory.equipped[slot] = Some(ItemInstance::stock(template, 1));
        }
        ship.refresh_equipment().unwrap();
        ship.energy = ship.equipment.ship.energy_capacity;
        let energy = ship.energy;
        let cost = ship.equipment.weapons[0].energy;
        lock_combat(&ship.combat).unwrap().actors[0].position = [-1000., 0., 0.];
        ship.fire_weapons_with_roll(|| 1000).unwrap();
        assert_eq!(ship.inventory.shot_sequence, 1);
        assert_eq!(ship.energy, energy - cost);
        assert_eq!(ship.inventory.ammunition[3].as_ref().unwrap().quantity, 5);
        assert!(ship.inventory.cargo[0].is_none());
        let world = lock_combat(&ship.combat).unwrap();
        assert_eq!(world.pending_projectiles().len(), 1);
        assert_eq!(world.pending_projectiles()[0].duration_ms, 2000);
    }

    #[test]
    fn a_missed_round_still_consumes_ammo_energy_and_reload_and_reports_zero_damage() {
        let mut ship = armed(3022, 512);
        ship.fire_weapons_with_roll(|| 1).unwrap();
        assert_eq!(ship.energy, 89.);
        assert_eq!(ship.inventory.shot_sequence, 1);
        assert_eq!(ship.inventory.ammunition[3].as_ref().unwrap().quantity, 5);
        assert!(ship.inventory.cargo[0].is_none());
        assert!(ship.weapon_cooldowns[3] > 0.);
        let mut world = lock_combat(&ship.combat).unwrap();
        let shot = world.pending_projectiles()[0].clone();
        assert_eq!(shot.damage, 0.);
        assert_eq!(shot.inflicted, 0);
        world.resolve_due(shot.due_ms).unwrap();
        assert_eq!(world.actors[0].shield, 150.);
        // The normal report flag and zero damage identify the native miss.
        let reports = world.take_damage_reports();
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].damage, 0.);
        assert_eq!(reports[0].inflicted, 0);
        drop(world);
        ship.fire_weapons_with_roll(|| panic!("cooldown must not roll again"))
            .unwrap();
        assert_eq!(ship.inventory.shot_sequence, 1);
        assert_eq!(ship.energy, 89.);
    }

    #[test]
    fn a_critical_outcome_is_frozen_across_failed_admission_and_restart() {
        let mut ship = armed(3022, 512);
        let path = ship.state_dir.join("combat/1076.json");
        let blocked = path.with_extension("json.next");
        fs::create_dir(&blocked).unwrap();
        assert!(ship.fire_weapons_with_roll(|| 9500).is_err());
        let journal = ship.inventory.pending_launch.clone().unwrap();
        assert_eq!(journal.projectiles[0].damage, 22.);
        assert_eq!(journal.projectiles[0].inflicted, 3);
        assert!(ship
            .fire_weapons_with_roll(|| panic!("recovery must not reroll"))
            .is_err());
        assert_eq!(ship.inventory.pending_launch.as_ref(), Some(&journal));
        fs::remove_dir(blocked).unwrap();
        let mut resumed = restore(&ship, &path);
        resumed.recover_weapon_launch().unwrap();
        assert_eq!(resumed.energy, 89.);
        assert_eq!(resumed.inventory.shot_sequence, 1);
        assert_eq!(
            resumed.inventory.ammunition[3].as_ref().unwrap().quantity,
            5
        );
        let mut world = lock_combat(&resumed.combat).unwrap();
        world.resolve_due(journal.projectiles[0].due_ms).unwrap();
        assert_eq!(world.actors[0].shield, 128.);
        let reports = world.take_damage_reports();
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].inflicted, 3);
        assert_eq!(reports[0].damage, 22.);
        world.admit(&journal.projectiles).unwrap();
        world
            .resolve_due(journal.projectiles[0].due_ms + 1)
            .unwrap();
        assert_eq!(world.actors[0].shield, 128.);
        assert!(world.take_damage_reports().is_empty());
    }

    #[test]
    fn projectile_volley_consumes_owned_rounds_auto_reloads_and_hits_only_after_travel() {
        let mut ship = armed(937, 530); // public two-round Killing Dragontooth
        let stats = ship.equipment.weapons[0].clone();
        assert_eq!(stats.rounds, 2);
        assert_eq!(stats.damage, 16.5);
        assert_eq!(stats.ammunition, Some(530));
        ship.fire_weapons_with_roll(|| 5000).unwrap();
        assert_eq!(ship.energy, 89.); // one activation, not one cost per round
        assert_eq!(ship.inventory.ammunition[3].as_ref().unwrap().quantity, 4);
        assert!(ship.inventory.cargo[0].is_none());
        assert_eq!(ship.inventory.shot_sequence, 2);
        assert!(ship.inventory.pending_launch.is_none());
        assert_eq!(lock_combat(&ship.combat).unwrap().actors[0].shield, 150.);
        let due = lock_combat(&ship.combat)
            .unwrap()
            .pending_projectiles()
            .iter()
            .map(|shot| shot.due_ms)
            .max()
            .unwrap();
        lock_combat(&ship.combat).unwrap().resolve_due(due).unwrap();
        assert_eq!(lock_combat(&ship.combat).unwrap().actors[0].shield, 117.);
        ship.fire_weapons_with_roll(|| 5000).unwrap();
        assert_eq!(ship.inventory.shot_sequence, 2); // reload prevents toggle bypass
    }

    #[test]
    fn failed_world_admission_retains_one_cost_and_restarts_without_spending_or_hitting_twice() {
        let mut ship = armed(3022, 512);
        let path = ship.state_dir.join("combat/1076.json");
        let blocked = path.with_extension("json.next");
        fs::create_dir(&blocked).unwrap();
        assert!(ship.fire_weapons_with_roll(|| 5000).is_err());
        assert_eq!(ship.energy, 89.);
        assert_eq!(ship.inventory.ammunition[3].as_ref().unwrap().quantity, 5);
        let journal = ship.inventory.pending_launch.clone().unwrap();
        assert!(ship.fire_weapons_with_roll(|| 5000).is_err());
        assert_eq!(ship.inventory.pending_launch.as_ref(), Some(&journal));
        assert_eq!(ship.inventory.shot_sequence, 1);
        let mut resumed = restore(&ship, &path);
        assert_eq!(resumed.energy, 100.); // old transport checkpoint predates launch
        assert!(resumed.recover_weapon_launch().is_err());
        assert_eq!(resumed.energy, 89.);
        fs::remove_dir(blocked).unwrap();
        resumed.recover_weapon_launch().unwrap();
        assert_eq!(resumed.energy, 89.);
        assert!(resumed.inventory.pending_launch.is_none());
        let mut world = lock_combat(&resumed.combat).unwrap();
        assert_eq!(world.accepted_sequence(resumed.player_id()), 1);
        world.resolve_due(journal.projectiles[0].due_ms).unwrap();
        assert_eq!(world.actors[0].shield, 139.);
        world.admit(&journal.projectiles).unwrap();
        world
            .resolve_due(journal.projectiles[0].due_ms + 1)
            .unwrap();
        assert_eq!(world.actors[0].shield, 139.);
    }

    #[test]
    fn journal_retirement_failure_does_not_rewind_resources_or_repeat_an_instant_beam_hit() {
        let mut ship = armed(3022, 512);
        ship.inventory.equipped[3] = Some(ItemInstance::stock(769, 1));
        ship.inventory.ammunition[3] = None;
        ship.refresh_equipment().unwrap();
        lock_combat(&ship.combat).unwrap().actors[0].position = [100., 0., 0.];
        let blocked = ship.state_dir.join("inventory.json.next");
        fs::create_dir(&blocked).unwrap();
        assert!(ship.fire_weapons_with_roll(|| 5000).is_err());
        assert_eq!(lock_combat(&ship.combat).unwrap().actors[0].shield, 141.);
        assert_eq!(ship.energy, 89.);
        assert!(ship.inventory.pending_launch.is_some());
        ship.energy += 5.; // an independently recorded resource change is retained
        fs::remove_dir(blocked).unwrap();
        ship.recover_weapon_launch().unwrap();
        assert_eq!(ship.energy, 94.);
        assert_eq!(lock_combat(&ship.combat).unwrap().actors[0].shield, 141.);
        assert!(ship.inventory.pending_launch.is_none());
    }

    #[test]
    fn crash_after_world_admission_before_checkpoint_recovers_cost_and_round_only_once() {
        let mut ship = armed(3022, 512);
        let blocked = ship.state_dir.join("session.local.json.tmp");
        fs::create_dir(&blocked).unwrap();
        assert!(ship.fire_weapons_with_roll(|| 5000).is_err());
        let journal = ship.inventory.pending_launch.clone().unwrap();
        assert_eq!(
            lock_combat(&ship.combat)
                .unwrap()
                .accepted_sequence(ship.player_id()),
            1
        );
        let path = ship.state_dir.join("combat/1076.json");
        let mut resumed = restore(&ship, &path);
        assert_eq!(resumed.energy, 100.);
        assert!(resumed.recover_weapon_launch().is_err());
        assert_eq!(resumed.energy, 89.);
        // Recovery can occur after the travel deadline. The shot is either
        // still queued or already applied, and remains accepted exactly once.
        let world = lock_combat(&resumed.combat).unwrap();
        assert_eq!(world.accepted_sequence(resumed.player_id()), 1);
        assert!(world.pending_projectiles().len() <= 1);
        drop(world);
        fs::remove_dir(blocked).unwrap();
        resumed.recover_weapon_launch().unwrap();
        assert_eq!(resumed.energy, 89.);
        assert_eq!(
            resumed.inventory.ammunition[3].as_ref().unwrap().quantity,
            5
        );
        let mut world = lock_combat(&resumed.combat).unwrap();
        world.resolve_due(journal.projectiles[0].due_ms).unwrap();
        assert_eq!(world.actors[0].shield, 139.);
        assert!(world.pending_projectiles().is_empty());
    }

    #[test]
    fn failed_inventory_commit_has_no_cost_ammo_admission_or_damage() {
        let mut ship = armed(3022, 512);
        let blocked = ship.state_dir.join("inventory.json.tmp");
        fs::create_dir(&blocked).unwrap();
        assert!(ship.fire_weapons_with_roll(|| 5000).is_err());
        assert_eq!(ship.energy, 100.);
        assert_eq!(ship.inventory.ammunition[3].as_ref().unwrap().quantity, 1);
        assert_eq!(ship.inventory.shot_sequence, 0);
        assert!(ship.inventory.pending_launch.is_none());
        assert_eq!(
            lock_combat(&ship.combat)
                .unwrap()
                .accepted_sequence(ship.player_id()),
            0
        );
        assert_eq!(lock_combat(&ship.combat).unwrap().actors[0].shield, 150.);
        fs::remove_dir(blocked).unwrap();
    }
}
