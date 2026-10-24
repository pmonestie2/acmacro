//! Authoritative JE ability activation. Packet handling chooses an ability;
//! these methods validate target/range, charge energy, advance its cast and
//! apply effects. Group support is queued for the shared-world coordinator.
use super::*;
use crate::skills::{AbilityDefinition, AbilityKind, Cast, SupportAction};

impl PlayerSession {
    pub(super) fn begin_ability(&mut self, index: u32) -> io::Result<()> {
        self.begin_ability_for(index, self.target)
    }

    pub(super) fn begin_ability_for(
        &mut self,
        index: u32,
        requested_target: u32,
    ) -> io::Result<()> {
        if self.gate_transit.is_some() {
            return Err(invalid(
                "Wait for the gate transition before using abilities.",
            ));
        }
        let ability = self.skill_catalog.ability(index)?;
        if ability.kind == AbilityKind::Jumpstart {
            self.jumpstart_definition()?;
        }
        if ability.kind == AbilityKind::Unsupported {
            return Err(invalid("This class ability is not implemented yet."));
        }
        if !self.logged_in || self.sector.station.is_some() {
            return Err(invalid("Enter space before using an ability."));
        }
        if ability.kind == AbilityKind::Cloak && self.skills.cloak_rank > 0 {
            self.stop_cloak();
            return Ok(());
        }
        if self.skills.cast.is_some() {
            return Err(invalid("Another ability is charging."));
        }
        let self_jumpstart = ability.kind == AbilityKind::Jumpstart
            && (requested_target == self.player_id() || requested_target == u32::MAX);
        if self.hull <= 0. && !self_jumpstart {
            return Err(invalid(
                "An incapacitated ship cannot activate this ability.",
            ));
        }
        if self.warp.active() && !(ability.kind == AbilityKind::Cloak && ability.rank >= 6) {
            return Err(invalid("Leave warp before activating this ability."));
        }
        if self
            .skills
            .cooldowns
            .get(&ability.skill)
            .is_some_and(|seconds| *seconds > 0.)
        {
            return Err(invalid("This skill is recovering."));
        }
        let target = self.ability_target(&ability, requested_target)?;
        let jumpstart = if ability.kind == AbilityKind::Jumpstart {
            Some(self.prepare_jumpstart_plan(target)?)
        } else {
            None
        };
        let energy = jumpstart
            .as_ref()
            .map_or(ability.energy, |plan| plan.energy);
        let charge = jumpstart
            .as_ref()
            .map_or(ability.charge, |plan| plan.charge_seconds);
        if self.energy < energy {
            return Err(invalid("Insufficient reactor energy."));
        }
        if ability.kind == AbilityKind::Wormhole {
            self.sectors.sector(ability.destination.unwrap())?;
        }
        if ability.kind == AbilityKind::Cloak && ability.rank < 5 && self.motion.speed.abs() > 0.1 {
            return Err(invalid("Stop the ship before using this cloak rank."));
        }
        self.energy -= energy;
        let effect = self.next_effect;
        self.next_effect = self.next_effect.wrapping_add(1);
        let asset = match ability.kind {
            AbilityKind::Unsupported => return Err(invalid("Unsupported class ability.")),
            AbilityKind::Cloak => 180,
            AbilityKind::Contemplation => 1179,
            AbilityKind::Wormhole => 689,
            AbilityKind::Environment => 719,
            AbilityKind::Fold => 202,
            AbilityKind::Jumpstart => {
                if target == self.player_id() {
                    411
                } else {
                    414
                }
            }
            AbilityKind::Leech => 1059,
        };
        if ability.kind == AbilityKind::Jumpstart {
            self.pending.push(packets::weapon_effect(
                self.player_id(),
                target,
                asset,
                effect,
                self.tick_ms(),
                500,
            ));
        } else {
            self.pending.push(packets::object_effect(
                self.player_id(),
                effect,
                asset,
                self.tick_ms(),
            ));
        }
        self.pending.push(packets::ability_progress(
            Some(index),
            self.tick_ms(),
            self.tick_ms().wrapping_add((charge * 1000.) as u32),
        ));
        self.pending.push(packets::ship_resources(
            self.player_id(),
            &self.equipment.ship,
            Some(self.energy),
            None,
            None,
        ));
        self.skills.cast = Some(Cast {
            ability: index,
            target,
            remaining: charge,
            effect,
            jumpstart,
        });
        println!(
            "Ability charging: {}; {} ({index}); target={target}",
            self.account.character.name, ability.name
        );
        Ok(())
    }

    fn ability_target(
        &self,
        ability: &AbilityDefinition,
        requested_target: u32,
    ) -> io::Result<u32> {
        if ability.kind == AbilityKind::Jumpstart {
            return self.jumpstart_target(ability, requested_target);
        }
        let combat = lock_combat(&self.combat)?;
        let enemy = matches!(
            ability.kind,
            AbilityKind::Contemplation | AbilityKind::Leech
        ) || ability.id == 124;
        if enemy {
            let index = self
                .target
                .checked_sub(MOB_ID)
                .ok_or_else(|| invalid("Select a hostile MOB first."))?;
            let mob = combat
                .actors
                .get(index as usize)
                .ok_or_else(|| invalid("Select a hostile MOB first."))?;
            if !self.mob_visible_in_scan(index as usize, mob)
                || distance(self.position, mob.position) > ability.range as f32
            {
                return Err(invalid("The target is dead or out of ability range."));
            }
            return Ok(self.target);
        }
        if ability.id == 125 {
            let contact = self
                .group_contacts
                .iter()
                .find(|contact| contact.id == self.target)
                .ok_or_else(|| invalid("Select a group member first."))?;
            if contact.sector != self.sector.id
                || distance(contact.position, self.position) > ability.range as f32
            {
                return Err(invalid("Group member is out of fold range."));
            }
            return Ok(contact.id);
        }
        if ability.kind == AbilityKind::Environment
            && self.group_contacts.iter().any(|contact| {
                contact.id == self.target
                    && contact.sector == self.sector.id
                    && distance(contact.position, self.position) <= ability.range as f32
            })
        {
            return Ok(self.target);
        }
        Ok(self.player_id())
    }

    pub(super) fn advance_abilities(&mut self, dt: f32) -> io::Result<()> {
        let expired: Vec<u32> = self
            .skills
            .temporary_effects
            .iter_mut()
            .filter_map(|(effect, seconds)| {
                *seconds -= dt;
                (*seconds <= 0.).then_some(*effect)
            })
            .collect();
        for effect in expired {
            self.skills.temporary_effects.remove(&effect);
            self.remove_effect(effect);
        }
        for seconds in self.skills.cooldowns.values_mut() {
            *seconds = (*seconds - dt).max(0.);
        }
        self.skills.combat_cloak_seconds = (self.skills.combat_cloak_seconds - dt).max(0.);
        if self.skills.cloak_rank > 0 && self.skills.cloak_source.is_none() {
            let drain = match self.skills.cloak_rank {
                1 => 0.5,
                3 => 1.5,
                5 => 3.,
                6 => 6.,
                _ => 10.,
            };
            self.energy = (self.energy - drain * dt).max(0.);
            if self.energy == 0. || self.hull == 0. {
                self.stop_cloak();
            }
        }
        if self.skills.environment_rank > 0 {
            self.skills.environment_seconds -= dt;
            if self.skills.environment_seconds <= 0. {
                self.skills.environment_rank = 0;
                if let Some(effect) = self.skills.environment_effect.take() {
                    self.remove_effect(effect);
                }
            }
        }
        if let Some(mut cast) = self.skills.cast.take() {
            let definition = self.skill_catalog.ability(cast.ability)?;
            let self_jumpstart =
                definition.kind == AbilityKind::Jumpstart && cast.target == self.player_id();
            let invalid_jumpstart = definition.kind == AbilityKind::Jumpstart
                && (cast.jumpstart.as_ref().is_some_and(|plan| !plan.valid())
                    || cast.jumpstart.is_none() && self.jumpstart_definition().is_err()
                    || self
                        .jumpstart_target_in_range(
                            cast.target,
                            cast.jumpstart
                                .as_ref()
                                .map_or(definition.range as f32, |plan| plan.range),
                        )
                        .is_err()
                    || self.warp.active()
                    || self.gate_transit.is_some()
                    || !self.logged_in
                    || self.sector.station.is_some());
            if (self.hull <= 0. && !self_jumpstart)
                || invalid_jumpstart
                || definition.kind == AbilityKind::Cloak
                    && definition.rank < 5
                    && self.motion.speed.abs() > 0.1
            {
                self.remove_effect(cast.effect);
                self.pending.push(packets::ability_progress(None, 0, 0));
                self.pending.push(packets::message("Ability interrupted."));
                return Ok(());
            }
            cast.remaining -= dt;
            if cast.remaining > 0. {
                self.skills.cast = Some(cast);
            } else {
                self.remove_effect(cast.effect);
                self.pending.push(packets::ability_progress(None, 0, 0));
                let ability = self.skill_catalog.ability(cast.ability)?;
                let cooldown = cast
                    .jumpstart
                    .as_ref()
                    .map_or(ability.cooldown, |plan| plan.cooldown_seconds);
                if cooldown > 0. {
                    self.skills.cooldowns.insert(ability.skill, cooldown);
                } else {
                    self.skills.cooldowns.remove(&ability.skill);
                }
                self.pending.push(packets::skill_cooldown(
                    ability.skill,
                    self.tick_ms(),
                    (cooldown * 1000.) as u32,
                ));
                if let Err(error) =
                    self.apply_ability(&ability, cast.target, cast.jumpstart.as_ref())
                {
                    self.pending.push(packets::message(&error.to_string()));
                } else {
                    println!(
                        "Ability applied: {}; {}",
                        self.account.character.name, ability.name
                    );
                }
            }
        }
        Ok(())
    }

    fn apply_ability(
        &mut self,
        ability: &AbilityDefinition,
        target: u32,
        jumpstart: Option<&crate::skills::JumpstartPlan>,
    ) -> io::Result<()> {
        let shared = Arc::clone(&self.combat);
        let mut combat = lock_combat(&shared)?;
        match ability.kind {
            AbilityKind::Unsupported => return Err(invalid("Unsupported class ability.")),
            AbilityKind::Cloak => {
                self.skills.cloak_rank = ability.rank;
                self.skills.cloak_source = None;
                let stealth = crate::mob_targeting::cloak_stealth(
                    self.account.character.total_level(),
                    self.skill_catalog.level(12),
                );
                self.skills.cloak_stealth = Some(stealth);
                self.stop_weapons();
                let asset = match ability.rank {
                    1 => 180,
                    3 => 181,
                    5 => 182,
                    6 => 183,
                    _ => 184,
                };
                self.skills.cloak_effect =
                    Some(self.add_skill_effect(self.account.character.game_id, asset));
                self.pending
                    .push(packets::cloak(self.account.character.game_id, true));
                if ability.rank >= 6 {
                    self.support_actions.push(SupportAction::Cloak {
                        enabled: true,
                        rank: ability.rank,
                        stealth,
                    });
                }
            }
            AbilityKind::Contemplation => {
                let mob = combat
                    .actors
                    .get_mut(target.saturating_sub(MOB_ID) as usize)
                    .ok_or_else(|| invalid("Target no longer exists."))?;
                if mob.state == MobState::Dead
                    || distance(mob.position, self.position) > ability.range as f32
                {
                    return Err(invalid("Target left contemplation range."));
                }
                mob.pacify(30.);
                self.add_skill_effect(target, 1179);
            }
            AbilityKind::Environment => {
                if target == self.account.character.game_id {
                    self.apply_environment(ability.rank);
                } else {
                    self.support_actions.push(SupportAction::Environment {
                        target,
                        rank: ability.rank,
                    });
                }
                if ability.rank == 7 {
                    self.support_actions
                        .push(SupportAction::Environment { target: 0, rank: 7 });
                }
            }
            AbilityKind::Fold => {
                if ability.id == 126 {
                    self.fold_self();
                } else if ability.id == 125 {
                    self.support_actions.push(SupportAction::Fold { target });
                } else {
                    let mob = combat
                        .actors
                        .get_mut(target.saturating_sub(MOB_ID) as usize)
                        .ok_or_else(|| invalid("Target no longer exists."))?;
                    if mob.state == MobState::Dead
                        || distance(mob.position, self.position) > ability.range as f32
                    {
                        return Err(invalid("Target left fold range."));
                    }
                    let length = distance(mob.position, self.position).max(1.);
                    for axis in 0..3 {
                        mob.position[axis] = (mob.position[axis]
                            + (mob.position[axis] - self.position[axis]) / length * 5000.)
                            .clamp(self.sector.bounds.min[axis], self.sector.bounds.max[axis]);
                    }
                    self.pending
                        .push(packets::movement(target, mob.position, self.tick_ms()));
                    self.add_skill_effect(target, 267);
                }
            }
            AbilityKind::Jumpstart => {
                // Missing plans exist only on pre-native in-flight checkpoints.
                let action = SupportAction::Jumpstart {
                    target,
                    repair_fraction: jumpstart.map_or(0.49, |plan| plan.repair_fraction),
                    range: jumpstart.map_or(ability.range as f32, |plan| plan.range),
                };
                if target == self.player_id() {
                    self.receive_support(self.player_id(), &action)?;
                } else {
                    self.support_actions.push(action);
                }
            }
            AbilityKind::Leech => {
                let center = combat
                    .actors
                    .get(target.saturating_sub(MOB_ID) as usize)
                    .ok_or_else(|| invalid("Target no longer exists."))?
                    .position;
                if distance(center, self.position) > ability.range as f32 {
                    return Err(invalid("Target left leech range."));
                }
                let mut total = 0.;
                for (index, mob) in combat.actors.iter_mut().enumerate() {
                    if MOB_ID + index as u32 == target
                        || ability.radius > 0
                            && distance(center, mob.position) <= ability.radius as f32
                    {
                        total += mob.drain_shield_by(
                            75. * ability.rank as f32,
                            self.account.character.game_id,
                        );
                        self.pending.push(packets::beam(
                            self.account.character.game_id,
                            MOB_ID + index as u32,
                            1047,
                            self.next_effect,
                            self.tick_offset
                                .wrapping_add(self.started.elapsed().as_millis() as u32),
                        ));
                        self.next_effect = self.next_effect.wrapping_add(1);
                    }
                }
                if ability.rank >= 3 {
                    self.energy =
                        (self.energy + total * 0.5).min(self.equipment.ship.energy_capacity);
                }
                if matches!(ability.id, 52 | 53) {
                    self.support_actions.push(SupportAction::Energy {
                        amount: total * 0.5,
                    });
                }
            }
            AbilityKind::Wormhole => {
                let destination = ability.destination.unwrap();
                self.support_actions.push(SupportAction::Wormhole {
                    destination,
                    origin_sector: self.sector.id,
                    origin: self.position,
                });
                self.transfer_sector(destination, false)?;
            }
        }
        self.pending.push(packets::ship_resources(
            self.account.character.game_id,
            &self.equipment.ship,
            Some(self.energy),
            None,
            None,
        ));
        Ok(())
    }

    pub(super) fn apply_environment(&mut self, rank: u32) {
        if let Some(effect) = self.skills.environment_effect.take() {
            self.remove_effect(effect);
        }
        self.skills.environment_rank = rank;
        self.skills.environment_seconds = 180.;
        self.skills.environment_effect = Some(self.add_skill_effect(self.player_id(), 216));
    }
    pub(super) fn fold_self(&mut self) {
        let angle = rand::random::<f32>() * std::f32::consts::TAU;
        self.position[0] = (self.position[0] + angle.cos() * 5000.)
            .clamp(self.sector.bounds.min[0], self.sector.bounds.max[0]);
        self.position[1] = (self.position[1] + angle.sin() * 5000.)
            .clamp(self.sector.bounds.min[1], self.sector.bounds.max[1]);
        self.motion.stop();
        self.last_motion_report = None;
        self.dirty = true;
        self.add_skill_effect(self.player_id(), 267);
        self.pending.push(packets::player_movement(
            self.player_id(),
            self.position,
            self.tick_ms(),
            &self.motion,
        ));
    }
    pub(super) fn stop_cloak(&mut self) {
        let rank = self.skills.cloak_rank;
        let inherited = self.skills.cloak_source.is_some();
        self.skills.cloak_rank = 0;
        self.skills.cloak_source = None;
        self.skills.cloak_stealth = None;
        if let Some(effect) = self.skills.cloak_effect.take() {
            self.remove_effect(effect);
        }
        if rank >= 5 {
            self.skills.combat_cloak_seconds = 5.;
        }
        self.pending.push(packets::cloak(self.player_id(), false));
        if rank >= 6 && !inherited {
            self.support_actions.push(SupportAction::Cloak {
                enabled: false,
                rank,
                stealth: 0,
            });
        }
    }
    fn add_skill_effect(&mut self, target: u32, asset: u16) -> u32 {
        let effect = self.next_effect;
        self.next_effect = self.next_effect.wrapping_add(1);
        self.pending.push(packets::object_effect(
            target,
            effect,
            asset,
            self.tick_ms(),
        ));
        if matches!(asset, 267 | 1179) {
            self.skills.temporary_effects.insert(effect, 2.);
        }
        effect
    }
    fn remove_effect(&mut self, effect: u32) {
        self.pending
            .push(Frame::new(0xf, effect.to_le_bytes().to_vec()));
    }
}
