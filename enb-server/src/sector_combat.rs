//! One authoritative MOB simulation per sector, shared by every player in it.
//! The coordinator ticks this once, then delivers resulting hits to players.
//! Rendering and packet sequence state remain per client in PlayerSession.
use crate::mob::{Mob, MobDefinition};
pub use crate::mob_aggro::PlayerThreat;
use std::collections::BTreeMap;
use std::io;
use std::path::PathBuf;
use std::time::Instant;
#[path = "combat_storage.rs"]
mod storage;
use std::sync::{Mutex, MutexGuard};

pub struct MobHit {
    pub target: u32,
    pub attacker_index: usize,
    pub damage: f32,
}

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
            last_saved: Instant::now(),
        }
    }
    pub fn advance(&mut self, dt: f32, players: &[PlayerThreat]) -> io::Result<Vec<MobHit>> {
        if !dt.is_finite() || dt < 0. {
            return Err(crate::protocol::invalid("Invalid combat tick duration."));
        }
        let now = crate::resources::unix_ms();
        let will_persist = self.save_due()
            || self
                .projectiles
                .iter()
                .any(|shot| shot.due_ms <= now || !shot.visual_sent && shot.emitted_ms <= now);
        let previous_actors = will_persist.then(|| self.actors.clone());
        let previous_projectiles = will_persist.then(|| self.projectiles.clone());
        let old_effects = self.effects.len();
        let old_reports = self.reports.len();
        let mut hits = Vec::new();
        for (index, mob) in self.actors.iter_mut().enumerate() {
            let target = mob.select_target(players).map(|index| &players[index]);
            let damage = match target {
                Some(player) => mob.tick_target(dt, player),
                None => mob.tick_visible(dt, mob.definition.spawn, false),
            };
            if let Some(player) = target {
                if damage > 0. {
                    hits.push(MobHit {
                        target: player.id,
                        attacker_index: index,
                        damage,
                    });
                }
            }
        }
        self.resolve_projectiles(now);
        if will_persist {
            if let Err(error) = self.persist() {
                self.actors = previous_actors.expect("snapshot before persistence");
                self.projectiles = previous_projectiles.expect("snapshot before persistence");
                self.effects.truncate(old_effects);
                self.reports.truncate(old_reports);
                return Err(error);
            }
        }
        Ok(hits)
    }
}

pub fn lock_combat(combat: &Mutex<SectorCombat>) -> io::Result<MutexGuard<'_, SectorCombat>> {
    combat
        .lock()
        .map_err(|_| io::Error::other("sector combat lock poisoned"))
}
