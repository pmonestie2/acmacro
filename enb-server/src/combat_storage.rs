//! A sector owns its combat state independently of any character checkpoint.
//! Projectile admission and resolution persist actors, scheduled hits and the
//! acceptance high-water marks together before the result can be published.
use super::*;
use crate::projectiles::Projectile;
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};
use std::path::PathBuf;
use std::time::{Duration, Instant};

pub(super) const DEVELOPER_SOURCE_BASE: u32 = 0xe0000000;
fn initial_developer_source() -> u32 {
    DEVELOPER_SOURCE_BASE
}

#[derive(Serialize, Deserialize)]
struct SavedCombat {
    version: u32,
    sector: u32,
    actors: Vec<Mob>,
    projectiles: Vec<Projectile>,
    accepted: BTreeMap<u32, u64>,
    #[serde(default = "initial_developer_source")]
    next_developer_spawn: u32,
    #[serde(default)]
    npc_shots: Vec<crate::npc_projectiles::PendingNpcShot>,
    #[serde(default)]
    npc_sequence: u64,
}

impl SectorCombat {
    pub fn persistent(path: PathBuf, sector: u32, initial: &Self) -> io::Result<Self> {
        let mut world = Self::from_actors(initial.actors.clone());
        world.path = Some(path.clone());
        world.sector = sector;
        match std::fs::read(&path) {
            Ok(bytes) => {
                let saved: SavedCombat = serde_json::from_slice(&bytes)?;
                if saved.next_developer_spawn < DEVELOPER_SOURCE_BASE
                    || saved.version != 1
                    || saved.sector != sector
                    || saved.actors.len() > 256
                    || saved.projectiles.len() > 16384
                    || saved.npc_shots.len() > 16384
                    || saved.accepted.len() > 65535
                    || saved.accepted.iter().any(|(owner, sequence)| {
                        !(0x40000001..=0x4000ffff).contains(owner) || *sequence == 0
                    })
                {
                    return Err(invalid("Invalid shared combat snapshot."));
                }
                let mut identities = HashSet::new();
                for actor in saved.actors {
                    if !identities.insert(actor.definition.source_id) {
                        return Err(invalid("Duplicate saved MOB identity."));
                    }
                    if let Some(current) = world
                        .actors
                        .iter_mut()
                        .find(|mob| mob.definition.source_id == actor.definition.source_id)
                    {
                        current.restore_from(actor)?;
                    } else if actor.definition.source_id >= DEVELOPER_SOURCE_BASE {
                        if world.actors.len() >= 256 {
                            return Err(invalid(
                                "Public and developer MOBs exceed sector capacity.",
                            ));
                        }
                        actor.definition.validate()?;
                        let mut current = Mob::new(actor.definition.clone());
                        current.restore_from(actor)?;
                        world.actors.push(current);
                    }
                }
                world.next_developer_spawn = saved.next_developer_spawn;
                let mut npc_sequences = HashSet::new();
                if saved.npc_shots.iter().any(|shot| {
                    !shot.valid()
                        || shot.hit.sector != sector
                        || shot.hit.sequence > saved.npc_sequence
                        || !npc_sequences.insert(shot.hit.sequence)
                }) {
                    return Err(invalid("Invalid saved NPC projectile identity."));
                }
                world.npc_sequence = saved.npc_sequence;
                world.npc_shots = saved.npc_shots;
                for actor in &world.actors {
                    if actor.definition.source_id >= DEVELOPER_SOURCE_BASE {
                        world.next_developer_spawn = world.next_developer_spawn.max(
                            actor.definition.source_id.checked_add(1).ok_or_else(|| {
                                invalid("Developer MOB identity space exhausted.")
                            })?,
                        );
                    }
                }
                let mut shots = HashSet::new();
                if saved.projectiles.iter().any(|shot| {
                    !shot.valid()
                        || shot.sector != sector
                        || saved
                            .accepted
                            .get(&shot.owner)
                            .is_none_or(|sequence| *sequence < shot.sequence)
                        || !shots.insert((shot.owner, shot.sequence))
                }) {
                    return Err(invalid("Invalid shared projectile ledger."));
                }
                world.projectiles = saved.projectiles;
                world.accepted = saved.accepted;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => world.persist()?,
            Err(error) => return Err(error),
        }
        Ok(world)
    }

    pub fn spawn_developer(
        &mut self,
        definitions: Vec<crate::mob::MobDefinition>,
    ) -> io::Result<Vec<u32>> {
        if definitions.is_empty() || self.actors.len() + definitions.len() > 256 {
            return Err(invalid(
                "Developer spawn would exceed sector MOB capacity (256).",
            ));
        }
        let previous_count = self.actors.len();
        let previous_source = self.next_developer_spawn;
        let mut next_source = previous_source;
        let mut actors = Vec::with_capacity(definitions.len());
        let mut ids = Vec::with_capacity(definitions.len());
        for mut definition in definitions {
            definition.source_id = next_source;
            next_source = next_source
                .checked_add(1)
                .ok_or_else(|| invalid("Developer MOB identity space exhausted."))?;
            definition.validate()?;
            ids.push(MOB_ID + (previous_count + actors.len()) as u32);
            actors.push(Mob::new(definition));
        }
        self.actors.extend(actors);
        self.next_developer_spawn = next_source;
        if let Err(error) = self.persist() {
            self.actors.truncate(previous_count);
            self.next_developer_spawn = previous_source;
            return Err(error);
        }
        Ok(ids)
    }

    pub fn accepted_sequence(&self, owner: u32) -> u64 {
        self.accepted.get(&owner).copied().unwrap_or(0)
    }

    pub fn pending_projectiles(&self) -> &[Projectile] {
        &self.projectiles
    }

    /// A retry can re-present an already accepted round after a crash. Its
    /// sequence was committed with the first admission, so it is never queued
    /// twice, including after it has already hit or been discarded.
    pub fn admit(&mut self, projectiles: &[Projectile]) -> io::Result<()> {
        let mut ordered = BTreeMap::new();
        for shot in projectiles {
            if !shot.valid()
                || self.sector != 0 && shot.sector != self.sector
                || ordered
                    .get(&shot.owner)
                    .is_some_and(|previous| *previous >= shot.sequence)
            {
                return Err(invalid(
                    "Projectile batch must have valid, increasing owner sequences.",
                ));
            }
            ordered.insert(shot.owner, shot.sequence);
        }
        let mut candidate = self.projectiles.clone();
        let mut accepted = self.accepted.clone();
        for shot in projectiles {
            if !shot.valid() {
                return Err(invalid("Invalid launched projectile."));
            }
            let previous = accepted.entry(shot.owner).or_default();
            if shot.sequence <= *previous {
                continue;
            }
            if candidate.len() >= 16384 {
                return Err(invalid("Sector projectile capacity reached."));
            }
            *previous = shot.sequence;
            candidate.push(shot.clone());
        }
        let old_projectiles = std::mem::replace(&mut self.projectiles, candidate);
        let old_accepted = std::mem::replace(&mut self.accepted, accepted);
        if let Err(error) = self.persist() {
            self.projectiles = old_projectiles;
            self.accepted = old_accepted;
            return Err(error);
        }
        Ok(())
    }

    pub(super) fn resolve_projectiles(&mut self, now: u64) -> bool {
        let mut changed = false;
        let actors = &mut self.actors;
        let effects = &mut self.effects;
        let reports = &mut self.reports;
        self.projectiles.retain_mut(|shot| {
            let target = actors.iter_mut().enumerate().find(|(_, mob)| {
                mob.definition.source_id == shot.target
                    && mob.definition.template_id == shot.template
                    && mob.death_generation == shot.generation
                    && mob.state != crate::mob::MobState::Dead
            });
            if !shot.visual_sent && shot.emitted_ms <= now {
                changed = true;
                shot.visual_sent = true;
                if let Some((index, _)) = &target {
                    let remaining = u64::from(shot.duration_ms)
                        .saturating_sub(now.saturating_sub(shot.emitted_ms));
                    if remaining > 0 {
                        effects.push(crate::projectiles::ProjectileVisual {
                            owner: shot.owner,
                            target: MOB_ID + *index as u32,
                            effect: shot.effect,
                            effect_id: shot.effect_id,
                            duration_ms: remaining as u16,
                        });
                    }
                }
            }
            if shot.due_ms > now {
                return true;
            }
            changed = true;
            if let Some((index, target)) = target {
                target.damage_by(shot.damage, shot.owner);
                reports.push(crate::projectiles::DamageReport {
                    owner: shot.owner,
                    target: MOB_ID + index as u32,
                    damage: shot.damage,
                    damage_type: shot.damage_type,
                    inflicted: shot.inflicted,
                });
            }
            false
        });
        changed
    }

    /// Beams land immediately through the same durable hit path as launchers.
    pub fn resolve_due(&mut self, now: u64) -> io::Result<()> {
        let actors = self.actors.clone();
        let shots = self.projectiles.clone();
        let effects = self.effects.len();
        let reports = self.reports.len();
        if self.resolve_projectiles(now) {
            if let Err(error) = self.persist() {
                self.actors = actors;
                self.projectiles = shots;
                self.effects.truncate(effects);
                self.reports.truncate(reports);
                return Err(error);
            }
        }
        Ok(())
    }
    pub fn take_damage_reports(&mut self) -> Vec<crate::projectiles::DamageReport> {
        std::mem::take(&mut self.reports)
    }
    pub fn take_projectile_visuals(&mut self) -> Vec<crate::projectiles::ProjectileVisual> {
        std::mem::take(&mut self.effects)
    }

    pub fn pending_mob_shots(&self) -> &[crate::npc_projectiles::PendingNpcShot] {
        &self.npc_shots
    }

    pub fn take_mob_launch_visuals(&mut self) -> Vec<crate::npc_projectiles::MobLaunchVisual> {
        std::mem::take(&mut self.npc_visuals)
    }

    /// The player first commits HP plus this receipt to its checkpoint. Only
    /// then may the coordinator retire the world shot and apply frozen shield
    /// reflection. Retrying a successful ack cannot reflect a second time.
    pub fn acknowledge_mob_hit(&mut self, sequence: u64, reflection: f32) -> io::Result<()> {
        if sequence == 0 || !reflection.is_finite() || reflection < 0. {
            return Err(invalid("Invalid NPC projectile acknowledgment."));
        }
        let Some(index) = self
            .npc_shots
            .iter()
            .position(|shot| shot.hit.sequence == sequence)
        else {
            return Ok(());
        };
        let shot = self.npc_shots.remove(index);
        let reflected_actor = if reflection > 0. {
            self.actors.iter().position(|mob| {
                mob.definition.source_id == shot.hit.attacker_source
                    && mob.definition.template_id == shot.hit.attacker_template
                    && mob.death_generation == shot.hit.attacker_generation
                    && mob.state != crate::mob::MobState::Dead
            })
        } else {
            None
        };
        let previous_actor = reflected_actor.map(|index| (index, self.actors[index].clone()));
        if let Some(index) = reflected_actor {
            self.actors[index].damage_by(reflection, shot.hit.target);
        }
        if let Err(error) = self.persist() {
            self.npc_shots.insert(index, shot);
            if let Some((index, actor)) = previous_actor {
                self.actors[index] = actor;
            }
            return Err(error);
        }
        Ok(())
    }

    pub fn persist(&mut self) -> io::Result<()> {
        if let Some(path) = &self.path {
            crate::durable_json::replace(
                path,
                &SavedCombat {
                    version: 1,
                    sector: self.sector,
                    actors: self.actors.clone(),
                    projectiles: self.projectiles.clone(),
                    accepted: self.accepted.clone(),
                    next_developer_spawn: self.next_developer_spawn,
                    npc_shots: self.npc_shots.clone(),
                    npc_sequence: self.npc_sequence,
                },
            )?;
        }
        self.last_saved = Instant::now();
        Ok(())
    }

    pub(super) fn save_due(&self) -> bool {
        self.path.is_some()
            && !self.actors.is_empty()
            && self.last_saved.elapsed() >= Duration::from_secs(1)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::content::Sector;
    use crate::mob::MobState;

    fn world() -> (SectorCombat, PathBuf) {
        let sector: Sector = serde_json::from_str(include_str!("../data/sector.json")).unwrap();
        let initial = SectorCombat::new(&sector.mobs);
        let path = std::env::temp_dir()
            .join(format!("enb-combat-ledger-{}", rand::random::<u64>()))
            .join("combat.json");
        (
            SectorCombat::persistent(path.clone(), sector.id, &initial).unwrap(),
            path,
        )
    }
    fn shot(world: &SectorCombat, sequence: u64, due: u64) -> Projectile {
        let target = &world.actors[0];
        Projectile {
            owner: 0x40000001,
            sector: 1076,
            slot: 3,
            emitted_ms: 1,
            duration_ms: 500,
            effect: 35,
            effect_id: 1,
            visual_sent: false,
            sequence,
            target: target.definition.source_id,
            template: target.definition.template_id,
            generation: target.death_generation,
            due_ms: due,
            damage: 25.,
            damage_type: 3,
            inflicted: 0,
        }
    }
    #[test]
    fn frozen_misses_and_criticals_survive_restart_without_changing_outcome() {
        let (mut world, path) = world();
        let initial = SectorCombat::from_actors(world.actors.clone());
        let mut miss = shot(&world, 1, 1);
        miss.damage = 0.;
        let mut critical = shot(&world, 2, 1);
        critical.damage = 50.;
        critical.inflicted = 3;
        world.admit(&[miss.clone(), critical.clone()]).unwrap();
        let mut restored = SectorCombat::persistent(path.clone(), 1076, &initial).unwrap();
        restored.resolve_due(1).unwrap();
        assert_eq!(restored.actors[0].shield, 100.);
        let reports = restored.take_damage_reports();
        assert_eq!(reports.len(), 2);
        assert_eq!((reports[0].damage, reports[0].inflicted), (0., 0));
        assert_eq!((reports[1].damage, reports[1].inflicted), (50., 3));
        let mut again = SectorCombat::persistent(path, 1076, &initial).unwrap();
        again.admit(&[miss, critical]).unwrap();
        again.resolve_due(1).unwrap();
        assert_eq!(again.actors[0].shield, 100.);
        assert!(again.take_damage_reports().is_empty());
    }

    #[test]
    fn legacy_rounds_default_to_normal_and_invalid_outcomes_are_rejected() {
        let (world, _) = world();
        let mut value = serde_json::to_value(shot(&world, 1, 1)).unwrap();
        value.as_object_mut().unwrap().remove("inflicted");
        let mut legacy: Projectile = serde_json::from_value(value).unwrap();
        assert_eq!(legacy.inflicted, 0);
        assert!(legacy.valid());
        legacy.inflicted = 1;
        assert!(!legacy.valid());
        legacy.inflicted = 3;
        legacy.damage = 0.;
        assert!(!legacy.valid());
        legacy.inflicted = 0;
        assert!(legacy.valid());
        legacy.damage = -1.;
        assert!(!legacy.valid());
    }

    #[test]
    fn launched_hit_survives_owner_departure_restart_and_replay_without_double_damage() {
        let (mut world, path) = world();
        let initial = SectorCombat::from_actors(world.actors.clone());
        let round = shot(&world, 1, 1);
        world.admit(std::slice::from_ref(&round)).unwrap();
        let shield = world.actors[0].shield;
        assert_eq!(shield, 150.);
        let mut resumed = SectorCombat::persistent(path.clone(), 1076, &initial).unwrap();
        resumed.admit(std::slice::from_ref(&round)).unwrap();
        resumed.advance(0., &[]).unwrap(); // no connected player or threat required
        assert_eq!(resumed.actors[0].shield, shield - 25.);
        let mut again = SectorCombat::persistent(path, 1076, &initial).unwrap();
        again.admit(&[round]).unwrap();
        again.advance(0., &[]).unwrap();
        assert_eq!(again.actors[0].shield, shield - 25.);
        assert!(again.projectiles.is_empty());
    }
    #[test]
    fn future_rounds_wait_and_late_rounds_cannot_hit_another_life_or_template() {
        let (mut world, _) = world();
        let future = shot(&world, 1, crate::resources::unix_ms() + 30_000);
        world.admit(&[future]).unwrap();
        world.advance(0., &[]).unwrap();
        assert_eq!(world.actors[0].shield, 150.);
        let mut stale = shot(&world, 2, 1);
        world.actors[0].damage_by(100_000., 0x40000002);
        world.actors[0].tick(20., [0.; 3]);
        assert_eq!(world.actors[0].state, MobState::Idle);
        world.admit(&[stale.clone()]).unwrap();
        world.advance(0., &[]).unwrap();
        assert_eq!(world.actors[0].shield, 150.);
        stale.sequence = 3;
        stale.generation = world.actors[0].death_generation;
        stale.template += 1;
        world.admit(&[stale]).unwrap();
        world.advance(0., &[]).unwrap();
        assert_eq!(world.actors[0].shield, 150.);
    }
    #[test]
    fn failed_admission_and_hit_write_leave_both_memory_and_disk_unchanged() {
        let (mut world, path) = world();
        let round = shot(&world, 1, 1);
        let blocked = path.with_extension("json.next");
        let before = std::fs::read(&path).unwrap();
        std::fs::create_dir(&blocked).unwrap();
        assert!(world.admit(std::slice::from_ref(&round)).is_err());
        assert_eq!(world.accepted_sequence(round.owner), 0);
        assert!(world.projectiles.is_empty());
        assert_eq!(std::fs::read(&path).unwrap(), before);
        std::fs::remove_dir(&blocked).unwrap();
        world.admit(std::slice::from_ref(&round)).unwrap();
        let admitted = std::fs::read(&path).unwrap();
        std::fs::create_dir(&blocked).unwrap();
        assert!(world.advance(0., &[]).is_err());
        assert_eq!(world.actors[0].shield, 150.);
        assert!(world.take_damage_reports().is_empty());
        assert_eq!(world.projectiles, [round]);
        assert_eq!(std::fs::read(&path).unwrap(), admitted);
        std::fs::remove_dir(blocked).unwrap();
        world.advance(0., &[]).unwrap();
        assert_eq!(world.actors[0].shield, 125.);
        let reports = world.take_damage_reports();
        assert_eq!(reports.len(), 1);
        assert_eq!(reports[0].damage, 25.);
        assert_eq!(reports[0].owner, 0x40000001);
    }
    #[test]
    fn out_of_order_or_malformed_batches_never_partially_admit_or_hide_dropped_shots() {
        let (mut world, path) = world();
        let first = shot(&world, 1, 1);
        let mut invalid = shot(&world, 2, 1);
        invalid.damage = f32::NAN;
        assert!(world.admit(&[first.clone(), invalid]).is_err());
        assert!(world.admit(&[shot(&world, 2, 1), first]).is_err());
        assert_eq!(world.accepted_sequence(0x40000001), 0);
        assert!(world.projectiles.is_empty());
        let fresh = SectorCombat::from_actors(world.actors.clone());
        let wrong = SectorCombat::persistent(path, 9999, &fresh);
        assert!(wrong.is_err());
    }

    #[test]
    fn developer_spawns_preserve_definition_health_and_unique_ids_across_restart() {
        let (mut world, path) = world();
        let initial = SectorCombat::from_actors(world.actors.clone());
        let mut definition = world.actors[0].definition.clone();
        definition.spawn = [1000., 0., 0.];
        let ids = world
            .spawn_developer(vec![definition.clone(), definition.clone()])
            .unwrap();
        assert_eq!(ids, [MOB_ID + 1, MOB_ID + 2]);
        assert_eq!(world.actors[1].definition.source_id, DEVELOPER_SOURCE_BASE);
        world.actors[1].damage_by(25., 0x40000001);
        world.persist().unwrap();
        let mut restored = SectorCombat::persistent(path, 1076, &initial).unwrap();
        assert_eq!(restored.actors.len(), 3);
        assert_eq!(restored.actors[1].position, [1000., 0., 0.]);
        assert_eq!(restored.actors[1].shield, 125.);
        let next = restored.spawn_developer(vec![definition]).unwrap();
        assert_eq!(next, [MOB_ID + 3]);
        assert_eq!(
            restored.actors[3].definition.source_id,
            DEVELOPER_SOURCE_BASE + 2
        );
    }

    #[test]
    fn developer_spawn_write_failure_or_bad_batch_never_partially_adds_actors() {
        let (mut world, path) = world();
        let definition = world.actors[0].definition.clone();
        let blocked = path.with_extension("json.next");
        std::fs::create_dir(&blocked).unwrap();
        assert!(world.spawn_developer(vec![definition.clone()]).is_err());
        assert_eq!(world.actors.len(), 1);
        assert_eq!(world.next_developer_spawn, DEVELOPER_SOURCE_BASE);
        std::fs::remove_dir(blocked).unwrap();
        let mut bad = definition.clone();
        bad.asset = 0;
        assert!(world
            .spawn_developer(vec![definition.clone(), bad])
            .is_err());
        assert_eq!(world.actors.len(), 1);
        assert!(world
            .spawn_developer(vec![definition.clone(); 256])
            .is_err());
        assert_eq!(world.actors.len(), 1);
        world.spawn_developer(vec![definition]).unwrap();
        assert_eq!(world.actors[1].definition.source_id, DEVELOPER_SOURCE_BASE);
    }
}
