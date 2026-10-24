//! Per-player simulation and rendering phases. Multiplayer calls these around
//! one shared sector-combat tick, so adding clients cannot multiply MOB speed,
//! damage or respawn timers. The public advance helper is for standalone tests.
use super::*;

impl PlayerSession {
    pub fn advance(&mut self, dt: f32) -> io::Result<()> {
        lock_resources(&self.resource_field)?.advance()?;
        self.advance_player(dt)?;
        if self.logged_in {
            lock_loot(&self.loot_world)?
                .capture_deaths(&*lock_combat(&self.combat)?, crate::resources::unix_ms())?;
            let hits = lock_combat(&self.combat)?.advance(dt, &[self.threat()])?;
            for hit in hits {
                self.receive_hit(hit.attacker_index, hit.damage)?;
            }
            let visuals = lock_combat(&self.combat)?.take_projectile_visuals();
            for visual in &visuals {
                self.publish_projectile_visual(visual);
            }
            let reports = lock_combat(&self.combat)?.take_damage_reports();
            for report in &reports {
                self.publish_weapon_damage(report);
            }
            self.publish_mobs()?;
            self.publish_resources()?;
            lock_loot(&self.loot_world)?
                .capture_deaths(&*lock_combat(&self.combat)?, crate::resources::unix_ms())?;
            self.publish_corpses()?;
        }
        self.harvest_visuals.clear(); // standalone helper has no peer coordinator
        self.finish_tick()
    }

    pub(crate) fn advance_player(&mut self, dt: f32) -> io::Result<()> {
        if !dt.is_finite() || dt < 0. {
            return Err(invalid("invalid simulation duration"));
        }
        self.recover_weapon_launch()?;
        if self.selected_peer.is_some() && self.last_seen.elapsed() > Duration::from_secs(60) {
            self.logout()?;
        }
        if !self.logged_in {
            return Ok(());
        }
        self.advance_gating()?;
        if !self.logged_in {
            return Ok(());
        }
        self.advance_production(dt)?;
        for slot in 0..20 {
            let before = self.weapon_cooldowns[slot];
            self.weapon_cooldowns[slot] = (before - dt).max(0.);
            if before > 0. && self.weapon_cooldowns[slot] == 0. {
                self.publish_weapon_state(slot);
            }
        }
        if self.sector.station.is_some() {
            self.advance_ship_resources(dt);
            return Ok(());
        }
        self.advance_abilities(dt)?;
        if !self.logged_in {
            return Ok(());
        } // wormhole cast handed off
        let previous = self.position;
        let was_warping = self.warp.active();
        for event in self.warp.advance(
            &mut self.position,
            &mut self.motion,
            &self.sector.bounds,
            dt,
        )? {
            self.emit_warp_event(event);
        }
        let followed = self.advance_following(dt)?;
        if !followed && !was_warping && self.hull > 0. && self.formation_leader.is_none() {
            self.motion
                .advance(&mut self.position, &self.sector.bounds, dt);
        }
        self.dirty |= self.position != previous;
        self.advance_mining(dt)?;
        self.advance_looting(dt)?;
        self.advance_ship_resources(dt);
        self.fire_weapons()?;
        self.refresh_target_verbs(false);
        Ok(())
    }

    fn advance_ship_resources(&mut self, dt: f32) {
        let stats = self.equipment.ship;
        if self.warp.draining_energy() {
            self.energy = (self.energy - stats.warp_drain * dt).max(0.);
            if self.energy == 0. {
                let event = self.warp.finish(FinishReason::Cancelled, &mut self.motion);
                self.emit_warp_event(event);
                self.pending
                    .push(packets::message("Warp ended: reactor depleted."));
            }
        } else if !self.mining_drains_energy() {
            self.energy = (self.energy + stats.energy_recharge * dt).min(stats.energy_capacity);
        }
        if self.hull > 0. {
            self.shield = (self.shield + stats.shield_recharge * dt).min(stats.shield_capacity);
        }
        self.resource_sync -= dt;
        if self.resource_sync <= 0. {
            self.pending.push(packets::ship_resources(
                self.player_id(),
                &self.equipment.ship,
                Some(self.energy),
                Some(self.shield),
                Some(self.hull),
            ));
            self.resource_sync = 1.;
        }
    }

    pub(crate) fn threat(&self) -> PlayerThreat {
        PlayerThreat {
            id: self.player_id(),
            position: self.position,
            visible: self.logged_in && self.hull > 0. && self.skills.cloak_rank == 0,
        }
    }

    pub(crate) fn receive_hit(&mut self, attacker: usize, mut damage: f32) -> io::Result<()> {
        use crate::combat_damage::{
            damage_control_fraction, player_damage, DamageType, PlayerDefense,
        };
        if !damage.is_finite() || damage < 0. {
            return Err(invalid("Invalid incoming weapon damage."));
        }
        if !self.logged_in || self.hull <= 0. {
            return Ok(());
        }
        let attack = lock_combat(&self.combat)?
            .actors
            .get(attacker)
            .map(|mob| mob.combat)
            .ok_or_else(|| invalid("Unknown MOB attacker."))?;
        let kind = DamageType::from_index(attack.damage_type)?;
        let incoming = damage;
        self.cancel_mining("Incoming fire interrupted prospecting.")?;
        if self.skills.environment_rank > 0 {
            damage *= if self.skills.environment_rank >= 3 {
                0.9
            } else {
                0.95
            };
            if self.skills.environment_rank >= 5 {
                let maximum = match self.skills.environment_rank {
                    5 => 10.,
                    6 => 25.,
                    _ => 50.,
                };
                if let Some(mob) = lock_combat(&self.combat)?.actors.get_mut(attacker) {
                    mob.damage_by(damage.min(maximum), self.player_id());
                }
            }
            if self.skills.environment_rank >= 6 {
                self.energy = (self.energy + (damage * 0.05).min(10.))
                    .min(self.equipment.ship.energy_capacity);
            }
        }
        let profile = &self.account.character;
        let damage_control = if profile.profession == 0 && matches!(profile.race, 0 | 2) {
            damage_control_fraction(self.skill_catalog.level(17))
        } else {
            0.
        };
        let result = player_damage(
            damage,
            kind,
            PlayerDefense {
                shield: self.shield,
                hull: self.hull,
                deflect_percent: self.equipment.ship.deflects[kind.index()],
                damage_control,
            },
        )?;
        self.shield = result.shield;
        self.hull = result.hull;
        self.pending.push(packets::client_damage(
            self.player_id(),
            MOB_ID + attacker as u32,
            result.reported_damage,
            result.reported_damage - incoming,
            kind.index() as u32,
            1,
        ));
        // ShipDamage cancels warp on its hull-overflow branch, including EMP
        // spill that cannot physically reduce the hull. Shield-only hits do not.
        if result.hull_branch && self.warp.active() {
            let event = self.warp.finish(FinishReason::Cancelled, &mut self.motion);
            self.emit_warp_event(event);
        }
        if self.hull == 0. {
            self.stop_weapons();
            self.stop_following();
            self.detach_formation();
            self.motion.stop();
            self.pending
                .push(packets::incapacitated(self.player_id(), true));
        }
        self.pending.push(packets::beam(
            MOB_ID + attacker as u32,
            self.player_id(),
            attack.effect,
            self.next_effect,
            self.tick_ms(),
        ));
        self.next_effect = self.next_effect.wrapping_add(1);
        self.pending.push(packets::ship_resources(
            self.player_id(),
            &self.equipment.ship,
            Some(self.energy),
            Some(self.shield),
            Some(self.hull),
        ));
        Ok(())
    }

    pub(crate) fn publish_mobs(&mut self) -> io::Result<()> {
        if !self.logged_in || self.sector.station.is_some() {
            return Ok(());
        }
        let broadcast = self.last_broadcast.elapsed() >= Duration::from_millis(200);
        let tick = self.tick_ms();
        let shared = Arc::clone(&self.combat);
        let combat = lock_combat(&shared)?;
        self.mob_states.resize(combat.actors.len(), MobState::Idle);
        self.mob_visible.resize(combat.actors.len(), false);
        for (index, mob) in combat.actors.iter().enumerate() {
            if mob.state != self.mob_states[index] {
                if mob.state == MobState::Dead && self.mob_visible[index] {
                    self.pending
                        .push(packets::message("MOB defeated; waiting for respawn."));
                }
                self.mob_states[index] = mob.state;
            }
            let visible =
                mob.state != MobState::Dead && distance(self.position, mob.position) <= 20_000.;
            if visible && !self.mob_visible[index] {
                self.pending
                    .extend(packets::mob_spawn(MOB_ID + index as u32, mob, tick));
            } else if !visible && self.mob_visible[index] {
                self.pending.push(Frame::new(
                    7,
                    (MOB_ID + index as u32).to_le_bytes().to_vec(),
                ));
            }
            self.mob_visible[index] = visible;
            if broadcast && visible {
                self.pending
                    .push(packets::movement(MOB_ID + index as u32, mob.position, tick));
                self.pending
                    .push(packets::mob_status(MOB_ID + index as u32, mob));
            }
        }
        let proxy_predicts = !self.warp.active()
            && self
                .last_motion_report
                .is_some_and(|at| at.elapsed() < Duration::from_millis(500));
        if broadcast && !proxy_predicts {
            self.pending.push(packets::player_movement(
                self.player_id(),
                self.position,
                tick,
                &self.motion,
            ));
        }
        if broadcast {
            self.last_broadcast = Instant::now();
        }
        Ok(())
    }

    pub(crate) fn finish_tick(&mut self) -> io::Result<()> {
        if self.transition_pending.is_some() {
            if self.transition_retry.elapsed() < Duration::from_millis(250) {
                return Ok(());
            }
            // The journal is already committed. Hold this character's output,
            // not the shared sector loop, while mirror storage is unavailable.
            if self.finish_transition().is_err() {
                return Ok(());
            }
        }
        if self.dirty && self.last_saved.elapsed() > Duration::from_secs(2) {
            self.save()?;
        }
        self.flush()?;
        self.checkpoint_session(false)
    }
}

#[cfg(test)]
mod damage_tests {
    use super::*;

    fn ship(kind: usize) -> PlayerSession {
        let mut ship = super::super::tests::session();
        ship.logged_in = true;
        ship.shield = 20.;
        ship.hull = 100.;
        lock_combat(&ship.combat).unwrap().actors[0]
            .combat
            .damage_type = kind;
        ship
    }
    #[test]
    fn incoming_chemical_and_emp_use_real_layer_rules_and_native_damage_feedback() {
        let mut chemical = ship(5);
        chemical.receive_hit(0, 30.).unwrap();
        assert_eq!(chemical.shield, 0.);
        assert_eq!(chemical.hull, 88.);
        let feedback = chemical
            .pending
            .iter()
            .find(|frame| frame.opcode == 0x64)
            .unwrap();
        assert_eq!(feedback.body.len(), 24);
        assert_eq!(
            f32::from_le_bytes(feedback.body[..4].try_into().unwrap()),
            30.
        );
        assert_eq!(
            u32::from_le_bytes(feedback.body[8..12].try_into().unwrap()),
            5
        );
        let mut emp = ship(4);
        emp.receive_hit(0, 100.).unwrap();
        assert_eq!(emp.shield, 0.);
        assert_eq!(emp.hull, 100.);
        let mut plasma = ship(2);
        plasma.receive_hit(0, 19.).unwrap();
        assert_eq!(plasma.shield, 0.);
        assert_eq!(plasma.hull, 100.);
    }
    #[test]
    fn damage_control_is_owned_by_te_and_pw_and_reduces_only_hull_spill() {
        let mut warrior = ship(3);
        warrior.account.character.race = 2;
        warrior.account.character.profession = 0;
        warrior.skill_catalog = SkillCatalog::for_profile(&warrior.account.character).unwrap();
        warrior.receive_hit(0, 60.).unwrap();
        assert_eq!(warrior.shield, 0.);
        assert_eq!(warrior.hull, 80.);
        let mut explorer = ship(3);
        explorer.receive_hit(0, 60.).unwrap();
        assert_eq!(explorer.hull, 60.);
    }
    #[test]
    fn invalid_incoming_damage_is_rejected_before_state_or_feedback_changes() {
        let mut ship = ship(3);
        let before = ship.pending.len();
        assert!(ship.receive_hit(0, f32::NAN).is_err());
        assert!(ship.receive_hit(9999, 10.).is_err());
        assert_eq!(ship.shield, 20.);
        assert_eq!(ship.hull, 100.);
        assert_eq!(ship.pending.len(), before);
    }
    #[test]
    fn emp_hull_spill_cancels_warp_without_incapacitating_the_ship() {
        let mut ship = ship(4);
        ship.warp
            .start(VecDeque::new(), ship.position, &mut ship.motion)
            .unwrap();
        assert!(ship.warp.active());
        ship.receive_hit(0, 30.).unwrap();
        assert!(!ship.warp.active());
        assert_eq!(ship.hull, 100.);
    }
}
