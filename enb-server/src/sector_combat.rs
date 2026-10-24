//! One authoritative MOB simulation per sector, shared by every player in it.
//! The coordinator ticks this once, then delivers resulting hits to players.
//! Rendering and packet sequence state remain per client in PlayerSession.
use crate::mob::{Mob, MobDefinition};
pub use crate::mob_aggro::PlayerThreat;
use crate::npc_projectiles::PendingNpcShot;
pub use crate::npc_projectiles::{MobHit, MobLaunchVisual};
use std::collections::BTreeMap;
use std::io;
use std::path::PathBuf;
use std::time::Instant;
#[path = "combat_storage.rs"]
mod storage;
use std::sync::{Mutex, MutexGuard};
#[cfg(test)]
#[path = "npc_combat_tests.rs"]
mod npc_tests;

pub const MOB_ID: u32 = 0x10000001;

pub struct SectorCombat {
    pub actors: Vec<Mob>,
    projectiles: Vec<crate::projectiles::Projectile>,
    effects: Vec<crate::projectiles::ProjectileVisual>,
    reports: Vec<crate::projectiles::DamageReport>,
    accepted: BTreeMap<u32, u64>,
    path: Option<PathBuf>,
    sector: u32,
    next_developer_spawn: u32,
    npc_shots: Vec<PendingNpcShot>,
    npc_sequence: u64,
    npc_visuals: Vec<MobLaunchVisual>,
    last_saved: Instant,
}

impl SectorCombat {
    pub fn new(definitions: &[MobDefinition]) -> Self {
        Self::from_actors(definitions.iter().cloned().map(Mob::new).collect())
    }
    pub fn from_actors(actors: Vec<Mob>) -> Self {
        Self {
            actors,
            projectiles: Vec::new(),
            effects: Vec::new(),
            reports: Vec::new(),
            accepted: BTreeMap::new(),
            path: None,
            sector: 0,
            next_developer_spawn: storage::DEVELOPER_SOURCE_BASE,
            npc_shots: Vec::new(),
            npc_sequence: 0,
            npc_visuals: Vec::new(),
            last_saved: Instant::now(),
        }
    }
    pub fn advance(&mut self, dt: f32, players: &[PlayerThreat]) -> io::Result<Vec<MobHit>> {
        self.advance_at(dt, players, crate::resources::unix_ms())
    }

    /// Standalone sessions have no storage path, but still need a durable-domain
    /// identity for their receipts. Persistent worlds are already sector-bound.
    pub fn bind_sector(&mut self, sector: u32) -> io::Result<()> {
        if sector == 0 || self.sector != 0 && self.sector != sector {
            return Err(crate::protocol::invalid(
                "Combat world belongs to a different sector.",
            ));
        }
        self.sector = sector;
        Ok(())
    }

    /// Explicit clock input keeps travel/recovery tests deterministic without
    /// sleeping or changing the production wall-clock deadline semantics.
    pub fn advance_at(
        &mut self,
        dt: f32,
        players: &[PlayerThreat],
        now: u64,
    ) -> io::Result<Vec<MobHit>> {
        if !dt.is_finite() || dt < 0. {
            return Err(crate::protocol::invalid("Invalid combat tick duration."));
        }
        let will_persist = self.save_due()
            || self
                .projectiles
                .iter()
                .any(|shot| shot.due_ms <= now || !shot.visual_sent && shot.emitted_ms <= now);
        // Quiet movement/cooldown ticks do not copy the sector or its queues.
        // A ready nearby NPC conservatively reserves a rollback snapshot before
        // targeting; a newly emitted shot always commits cooldown/RNG with it.
        let snapshot_needed = will_persist
            || self
                .actors
                .iter()
                .any(|mob| mob.may_fire_this_tick(dt, players));
        let previous = snapshot_needed.then(|| {
            (
                self.actors.clone(),
                self.projectiles.clone(),
                self.npc_shots.clone(),
                self.npc_sequence,
            )
        });
        let old_effects = self.effects.len();
        let old_reports = self.reports.len();
        let visuals = match self.advance_and_persist(dt, players, now, will_persist) {
            Ok(visuals) => visuals,
            Err(error) => {
                if let Some((actors, projectiles, shots, sequence)) = previous {
                    self.actors = actors;
                    self.projectiles = projectiles;
                    self.npc_shots = shots;
                    self.npc_sequence = sequence;
                }
                self.effects.truncate(old_effects);
                self.reports.truncate(old_reports);
                return Err(error);
            }
        };
        self.npc_visuals.extend(visuals);
        // Due impacts stay durable until the coordinator commits the player's
        // receipt and acknowledges them. Cloak/source death cannot cancel a
        // shot already launched; target-sector departure is handled by caller.
        Ok(self
            .npc_shots
            .iter()
            .filter(|shot| shot.due_ms <= now)
            .map(|shot| shot.hit.clone())
            .collect())
    }

    fn advance_and_persist(
        &mut self,
        dt: f32,
        players: &[PlayerThreat],
        now: u64,
        will_persist: bool,
    ) -> io::Result<Vec<MobLaunchVisual>> {
        let mut visuals = Vec::new();
        for (index, mob) in self.actors.iter_mut().enumerate() {
            // Backpressure must still return due shots for acknowledgment.
            // Failing the whole tick at capacity would leave the queue full
            // forever, because the coordinator could never retire old hits.
            if self.npc_shots.len() >= 16384 {
                mob.tick_idle(dt, players);
                continue;
            }
            let target = mob
                .select_target_with_clock(dt, players)
                .map(|index| &players[index]);
            let damage = match target {
                Some(player) => mob.tick_target(dt, player),
                None => mob.tick_idle(dt, players),
            };
            if let Some(player) = target {
                if damage > 0. {
                    self.npc_sequence = self.npc_sequence.checked_add(1).ok_or_else(|| {
                        crate::protocol::invalid("NPC projectile sequence exhausted.")
                    })?;
                    let duration_ms = crate::npc_projectiles::travel_ms(
                        &mob.definition,
                        mob.range_to(player.position),
                    )?;
                    let hit = MobHit {
                        sector: self.sector,
                        sequence: self.npc_sequence,
                        target: player.id,
                        target_epoch: player.session_epoch,
                        attacker_index: index,
                        attacker_source: mob.definition.source_id,
                        attacker_template: mob.definition.template_id,
                        attacker_generation: mob.death_generation,
                        damage,
                        damage_type: mob.combat.damage_type,
                    };
                    let shot = PendingNpcShot {
                        hit,
                        effect: mob.combat.effect,
                        emitted_ms: now,
                        due_ms: now.checked_add(u64::from(duration_ms)).ok_or_else(|| {
                            crate::protocol::invalid("NPC projectile deadline overflow.")
                        })?,
                        duration_ms,
                    };
                    if !shot.valid() {
                        return Err(crate::protocol::invalid(
                            "Invalid NPC projectile preparation.",
                        ));
                    }
                    visuals.push(MobLaunchVisual {
                        sector: self.sector,
                        sequence: self.npc_sequence,
                        target: player.id,
                        attacker_index: index,
                        effect: shot.effect,
                        duration_ms,
                    });
                    self.npc_shots.push(shot);
                }
            }
        }
        self.resolve_projectiles(now);
        if will_persist || !visuals.is_empty() {
            self.persist()?;
        }
        Ok(visuals)
    }
}

pub fn lock_combat(combat: &Mutex<SectorCombat>) -> io::Result<MutexGuard<'_, SectorCombat>> {
    combat
        .lock()
        .map_err(|_| io::Error::other("sector combat lock poisoned"))
}
