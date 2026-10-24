//! Initial deterministic MOB state machine, following MOBClass/MOBSpawnClass.
//! Archived level/weapon stats drive combat; faction matrices and group hate remain unfinished.
use crate::mob_aggro::{MobAggro, PlayerThreat};
use crate::mob_stats::{MobStats, MobWeapon};
use crate::motion::Motion;
use serde::{Deserialize, Serialize};
#[path = "mob_motion.rs"]
mod movement;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MobDefinition {
    pub source_id: u32,
    #[serde(default)]
    pub mob_type: i8,
    #[serde(default = "default_scale")]
    pub shield_modifier: f32,
    #[serde(default = "default_scale")]
    pub damage_modifier: f32,
    #[serde(default = "default_scale")]
    pub range_modifier: f32,
    #[serde(default)]
    pub weapon: Option<MobWeapon>,
    /// Explicit custom scenario stats; public SQL maps use the archived curves.
    #[serde(default)]
    pub combat_override: Option<MobStats>,
    #[serde(default)]
    pub template_id: u32,
    #[serde(default)]
    pub spawn_group: u32,
    pub name: String,
    pub level: u32,
    pub asset: u16,
    pub spawn: [f32; 3],
    pub respawn_seconds: f32,
    pub aggressive: bool,
    #[serde(default = "default_scale")]
    pub scale: f32,
    #[serde(default)]
    pub hsv: [f32; 3],
}

impl MobDefinition {
    pub fn validate(&self) -> std::io::Result<()> {
        if self.source_id == 0
            || self
                .spawn
                .iter()
                .any(|coordinate| !coordinate.is_finite() || coordinate.abs() > 2_000_000.)
            || !(-1..=6).contains(&self.mob_type)
            || [
                self.shield_modifier,
                self.damage_modifier,
                self.range_modifier,
            ]
            .iter()
            .any(|value| !value.is_finite() || *value <= 0. || *value > 1000.)
            || !MobStats::derive(self).valid()
            || self.weapon.as_ref().is_some_and(|weapon| {
                weapon.item == 0
                    || !matches!(weapon.item_type, 14..=16)
                    || !weapon.damage.is_finite()
                    || weapon.damage < 0.
                    || !weapon.reload.is_finite()
                    || weapon.item_type == 14
                        && (weapon.reload < 0.1 || weapon.reload > 300. || weapon.damage <= 0.)
            })
            || self.level > 66
            || self.asset == 0
            || self.name.len() > 100
            || !self.name.is_ascii()
            || !self.scale.is_finite()
            || self.scale == 0.
            || self.hsv.iter().any(|value| !value.is_finite())
            || !self.respawn_seconds.is_finite()
            || self.respawn_seconds < 1.
        {
            return Err(crate::protocol::invalid("Invalid MOB definition."));
        }
        Ok(())
    }
}

fn default_scale() -> f32 {
    1.
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum MobState {
    Idle,
    Chasing,
    Attacking,
    Dead,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Mob {
    pub definition: MobDefinition,
    pub state: MobState,
    pub position: [f32; 3],
    #[serde(default)]
    pub speed: f32,
    /// Native render motion follows authoritative MOB movement. The AI's
    /// displacement remains authoritative; this is not a second flight solver.
    #[serde(default)]
    pub motion: Motion,
    pub health: f32,
    pub max_health: f32,
    #[serde(default)]
    pub combat: MobStats,
    #[serde(default)]
    random_state: u64,
    #[serde(default)]
    pub shield: f32,
    #[serde(default)]
    pub max_shield: f32,
    #[serde(default)]
    pub disabled_seconds: f32,
    #[serde(default)]
    pub death_generation: u64,
    #[serde(default)]
    pub killer: Option<u32>,
    cooldown: f32,
    respawn_remaining: f32,
    provoked: bool,
    #[serde(default)]
    aggro: MobAggro,
    /// Unowned damage is retained for standalone scenarios/tests; an owned hit
    /// must never make a neutral MOB retaliate against an unrelated bystander.
    #[serde(default)]
    unowned_provoked: bool,
}

pub fn distance(a: [f32; 3], b: [f32; 3]) -> f32 {
    a.iter()
        .zip(b)
        .map(|(a, b)| (a - b).powi(2))
        .sum::<f32>()
        .sqrt()
}

pub fn move_towards(position: &mut [f32; 3], target: [f32; 3], distance: f32) {
    let length = self::distance(*position, target);
    if length == 0. {
        return;
    }
    let ratio = (distance / length).min(1.);
    for i in 0..3 {
        position[i] += (target[i] - position[i]) * ratio;
    }
}

impl Mob {
    pub fn new(definition: MobDefinition) -> Self {
        let combat = MobStats::derive(&definition);
        let max_health = combat.hull;
        let random_state =
            u64::from(definition.source_id) ^ (u64::from(definition.template_id) << 32);
        Self {
            position: definition.spawn,
            speed: 0.,
            motion: Motion::default(),
            definition,
            state: MobState::Idle,
            health: max_health,
            max_health,
            combat,
            random_state,
            shield: combat.shield,
            max_shield: combat.shield,
            disabled_seconds: 0.,
            death_generation: 0,
            killer: None,
            cooldown: 0.,
            respawn_remaining: 0.,
            provoked: false,
            aggro: MobAggro::default(),
            unowned_provoked: false,
        }
    }
    /// Content is authoritative after a build. Preserve damage fractions and
    /// transport-era AI state, but do not restore old prototype caps/definitions.
    pub(crate) fn restore_from(&mut self, mut saved: Self) -> std::io::Result<()> {
        if self.definition.source_id != saved.definition.source_id
            || self.definition.template_id != saved.definition.template_id
        {
            return Ok(());
        }
        saved.aggro.validate()?;
        saved.validate_motion()?;
        // Older simulation ticks let an elapsed respawn countdown run just
        // below zero before setting Idle. Those are valid expired timers, not
        // negative health. Normalize them without hiding non-finite corruption.
        if saved.respawn_remaining.is_finite() && saved.respawn_remaining < 0. {
            saved.respawn_remaining = 0.;
        }
        if [
            saved.health,
            saved.max_health,
            saved.shield,
            saved.max_shield,
            saved.cooldown,
            saved.respawn_remaining,
            saved.disabled_seconds,
            saved.speed,
        ]
        .iter()
        .any(|value| !value.is_finite() || *value < 0.)
            || saved
                .position
                .iter()
                .any(|value| !value.is_finite() || value.abs() > 2_000_000.)
            || saved.health > saved.max_health
            || saved.shield > saved.max_shield
        {
            return Err(crate::protocol::invalid("Invalid MOB checkpoint state."));
        }
        let fraction = |value: f32, cap: f32| {
            if cap > 0. {
                (value / cap).clamp(0., 1.)
            } else {
                1.
            }
        };
        saved.health = if saved.state == MobState::Dead {
            0.
        } else {
            fraction(saved.health, saved.max_health) * self.max_health
        };
        saved.shield = if saved.state == MobState::Dead {
            0.
        } else {
            fraction(saved.shield, saved.max_shield) * self.max_shield
        };
        saved.max_health = self.max_health;
        saved.max_shield = self.max_shield;
        saved.combat = self.combat;
        saved.cooldown = saved.cooldown.min(self.combat.reload);
        if saved.random_state == 0 {
            saved.random_state = self.random_state;
        }
        saved.definition = self.definition.clone();
        *self = saved;
        Ok(())
    }
    pub fn damage(&mut self, damage: f32) {
        if self.state != MobState::Dead && damage.is_finite() && damage > 0. {
            self.unowned_provoked = true;
        }
        self.apply_damage(damage);
    }
    fn apply_damage(&mut self, damage: f32) {
        if self.state == MobState::Dead || !damage.is_finite() || damage <= 0. {
            return;
        }
        self.provoked = true;
        self.disabled_seconds = 0.; // Attacks break contemplation.
        let absorbed = damage.min(self.shield);
        self.shield -= absorbed;
        self.health = (self.health - (damage - absorbed)).max(0.);
        if self.health == 0. && self.shield == 0. {
            self.stop_motion();
            self.state = MobState::Dead;
            self.respawn_remaining = self.definition.respawn_seconds;
            self.death_generation = self.death_generation.saturating_add(1);
            self.killer = None;
            self.aggro.clear();
            self.unowned_provoked = false;
        }
    }
    pub fn damage_by(&mut self, damage: f32, player: u32) {
        let alive = self.state != MobState::Dead;
        if alive {
            self.aggro.add(player, damage);
        }
        self.apply_damage(damage);
        if alive && self.state == MobState::Dead {
            self.killer = Some(player);
        }
    }
    /// A durable corpse birth can be newer than the transport checkpoint.
    pub(crate) fn reconcile_death(&mut self, generation: u64, remaining: f32) {
        if self.death_generation <= generation {
            self.death_generation = generation;
            if remaining > 0. {
                self.state = MobState::Dead;
                self.health = 0.;
                self.shield = 0.;
                self.respawn_remaining = remaining;
                self.stop_motion();
            } else if self.state == MobState::Dead {
                // A stale transport checkpoint must not restart an already
                // elapsed respawn timer. Let the next simulation tick respawn.
                self.respawn_remaining = 0.;
            }
        }
    }
    /// Shield Leech drains only shields; it cannot silently kill the hull.
    pub fn drain_shield(&mut self, amount: f32) -> f32 {
        if self.state == MobState::Dead || !amount.is_finite() || amount <= 0. {
            return 0.;
        }
        let drained = amount.min(self.shield);
        self.damage(drained);
        drained
    }
    pub fn drain_shield_by(&mut self, amount: f32, player: u32) -> f32 {
        if self.state == MobState::Dead || !amount.is_finite() || amount <= 0. {
            return 0.;
        }
        let drained = amount.min(self.shield);
        self.damage_by(drained, player);
        drained
    }
    pub fn pacify(&mut self, seconds: f32) {
        if self.state == MobState::Dead {
            return;
        }
        self.disabled_seconds = seconds.max(0.);
        self.stop_motion();
        self.state = MobState::Idle;
        self.provoked = false;
        self.unowned_provoked = false;
        self.aggro.clear();
    }
    /// LockTarget prefers positive attacker hate. The coordinator supplies only
    /// local player identities; invisible/cloaked ships lose hate. Ordinary
    /// temporary target loss retains hate, like native LostTarget.
    /// The existing scan/leash limits remain the acquisition boundaries.
    pub fn select_target(&mut self, players: &[PlayerThreat]) -> Option<usize> {
        let eligible = |player: &PlayerThreat| {
            player.id != 0
                && player.visible
                && player.position.iter().all(|value| value.is_finite())
        };
        self.aggro.retain(|id| {
            !players.iter().any(|player| {
                player.id == id
                    && (!player.visible || player.position.iter().any(|value| !value.is_finite()))
            })
        });
        if self.state == MobState::Dead || self.disabled_seconds > 0. {
            self.aggro.target(None);
            return None;
        }
        let hated = self.aggro.most_hated(|id| {
            players
                .iter()
                .any(|player| player.id == id && eligible(player))
        });
        self.provoked = hated.is_some() || self.unowned_provoked;
        let selected = hated
            .and_then(|id| {
                players
                    .iter()
                    .position(|player| player.id == id && eligible(player))
            })
            .or_else(|| {
                let scan = 2500. + 150. * self.definition.level as f32;
                players
                    .iter()
                    .enumerate()
                    .filter(|(_, player)| {
                        eligible(player)
                            && (self.unowned_provoked
                                || self.definition.aggressive
                                    && distance(player.position, self.position) <= scan)
                    })
                    .min_by(|(a_index, a), (b_index, b)| {
                        distance(a.position, self.position)
                            .total_cmp(&distance(b.position, self.position))
                            .then_with(|| a_index.cmp(b_index))
                    })
                    .map(|(index, _)| index)
            });
        self.aggro.target(selected.map(|index| players[index].id));
        selected
    }
    /// Actual departure/range-list removal discards that player's hate; merely
    /// losing the current target is not sufficient to call this method.
    pub fn remove_player_hate(&mut self, player: u32) {
        self.aggro.remove(player);
    }
    /// Native FireWeapon decreases this victim's hate by truncated damage/10.
    /// Called on the authoritative simulation only, before the tick is saved.
    pub fn tick_target(&mut self, dt: f32, player: &PlayerThreat) -> f32 {
        let damage = self.tick_visible(dt, player.position, player.visible);
        self.aggro.subtract(player.id, damage);
        damage
    }
    /// Conservative preflight for a world rollback snapshot. Target/hate
    /// selection still runs in the normal tick; this only proves that no shot
    /// can be produced while every player is outside the firing/leash bounds.
    pub(crate) fn may_fire_this_tick(&self, dt: f32, players: &[PlayerThreat]) -> bool {
        self.state != MobState::Dead
            && self.disabled_seconds <= 0.
            && self.cooldown <= dt
            && players.iter().any(|player| {
                player.visible
                    && distance(self.position, player.position) <= self.combat.range
                    && distance(self.definition.spawn, player.position) <= 15000.
            })
    }
    pub fn tick(&mut self, dt: f32, player: [f32; 3]) -> f32 {
        self.tick_visible(dt, player, true)
    }
    pub fn tick_visible(&mut self, dt: f32, player: [f32; 3], visible: bool) -> f32 {
        let before = self.position;
        let was_dead = self.state == MobState::Dead;
        let damage = self.advance_state(dt, player, visible);
        self.speed = if dt > 0. && !was_dead {
            distance(before, self.position) / dt
        } else {
            0.
        };
        self.synchronize_motion(before, player, visible, was_dead);
        damage
    }
    fn advance_state(&mut self, dt: f32, player: [f32; 3], visible: bool) -> f32 {
        if self.state == MobState::Dead {
            self.respawn_remaining = (self.respawn_remaining - dt).max(0.);
            if self.respawn_remaining <= 0. {
                self.health = self.max_health;
                self.shield = self.max_shield;
                self.position = self.definition.spawn;
                self.provoked = false;
                self.unowned_provoked = false;
                self.aggro.clear();
                self.killer = None;
                self.state = MobState::Idle;
            }
            return 0.;
        }
        if !visible || self.disabled_seconds > 0. {
            self.disabled_seconds = (self.disabled_seconds - dt).max(0.);
            self.state = MobState::Idle;
            return 0.;
        }
        self.cooldown = (self.cooldown - dt).max(0.);
        let separation = distance(self.position, player);
        let scan = 2500. + 150. * self.definition.level as f32;
        if !(self.provoked || self.definition.aggressive && separation <= scan)
            || distance(player, self.definition.spawn) > 15000.
        {
            self.state = MobState::Idle;
            self.provoked = false;
            self.unowned_provoked = false;
            self.aggro.clear();
            move_towards(&mut self.position, self.definition.spawn, 100. * dt);
            return 0.;
        }
        if separation > self.combat.range {
            self.state = MobState::Chasing;
            move_towards(&mut self.position, player, 150. * dt);
            return 0.;
        }
        self.state = MobState::Attacking;
        if self.cooldown == 0. {
            self.cooldown = self.combat.reload;
            let fraction = if self.combat.variance {
                let mut rng = crate::world_random::WorldRandom(self.random_state);
                let sum = rng.below(13) + rng.below(13) + rng.below(13) + 3;
                self.random_state = rng.0;
                sum as f32 / 30.
            } else {
                1.
            };
            return self.combat.dps * self.combat.reload * fraction;
        }
        0.
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn definition() -> MobDefinition {
        MobDefinition {
            source_id: 1,
            mob_type: 0,
            shield_modifier: 1.,
            damage_modifier: 1.,
            range_modifier: 1.,
            weapon: None,
            combat_override: Some(MobStats {
                hull: 100.,
                shield: 150.,
                dps: 2.5,
                reload: 2.,
                range: 1000.,
                effect: 35,
                damage_type: 3,
                variance: false,
            }),
            name: "Test".into(),
            level: 1,
            asset: 48,
            spawn: [0.; 3],
            respawn_seconds: 2.,
            aggressive: true,
            template_id: 1,
            spawn_group: 1,
            scale: 1.,
            hsv: [0.; 3],
        }
    }
    fn threat(id: u32, x: f32) -> PlayerThreat {
        PlayerThreat {
            id,
            session_epoch: 1,
            position: [x, 0., 0.],
            visible: true,
        }
    }

    #[test]
    fn multiplayer_hate_prefers_actual_attackers_and_decays_after_firing() {
        let mut def = definition();
        def.aggressive = false;
        let mut mob = Mob::new(def);
        let players = [threat(1, 1.), threat(2, 500.), threat(3, 900.)];
        assert_eq!(mob.select_target(&players), None);
        mob.damage_by(20., 2);
        assert_eq!(mob.select_target(&players), Some(1));
        mob.damage_by(20., 3);
        assert_eq!(mob.select_target(&players), Some(1)); // native slot-order tie
        mob.damage_by(1., 3);
        assert_eq!(mob.select_target(&players), Some(2));
        // The explicit scenario deals5 damage per hit, which truncates to no
        // hate decay. Raising DPS to100 gives200 damage and20 hate removed.
        mob.combat.dps = 100.;
        assert_eq!(mob.tick_target(2., &players[2]), 200.);
        assert_eq!(mob.select_target(&players), Some(1));
    }

    #[test]
    fn temporary_target_loss_retains_hate_but_cloak_and_departure_remove_it() {
        let mut def = definition();
        def.aggressive = false;
        let mut mob = Mob::new(def);
        mob.damage_by(10., 2);
        assert_eq!(mob.select_target(&[threat(1, 1.)]), None);
        assert_eq!(mob.select_target(&[threat(2, 500.)]), Some(0));
        let mut cloaked = threat(2, 500.);
        cloaked.visible = false;
        assert_eq!(mob.select_target(&[cloaked]), None);
        assert_eq!(mob.select_target(&[threat(2, 500.)]), None);
        mob.damage_by(10., 2);
        mob.remove_player_hate(2);
        assert_eq!(mob.select_target(&[threat(2, 500.)]), None);
        mob.damage(1.); // unowned scenario damage retains old nearest behavior
        assert_eq!(mob.select_target(&[threat(1, 1.)]), Some(0));
    }

    #[test]
    fn owned_target_beyond_leash_runs_the_existing_reset_and_returns_home() {
        let mut def = definition();
        def.aggressive = false;
        let mut mob = Mob::new(def);
        mob.position = [1000., 0., 0.];
        mob.damage_by(20., 2);
        let escaped = threat(2, 20_000.);
        assert_eq!(mob.select_target(&[escaped]), Some(0));
        assert_eq!(mob.tick_target(1., &escaped), 0.);
        assert_eq!(mob.state, MobState::Idle);
        assert_eq!(mob.position, [900., 0., 0.]);
        assert_eq!(mob.select_target(&[threat(2, 500.)]), None);
    }

    #[test]
    fn hate_checkpoint_roundtrip_defaults_and_corruption_are_checked() {
        let mut mob = Mob::new(definition());
        mob.damage_by(20., 7);
        mob.damage_by(40., 8);
        assert_eq!(
            mob.select_target(&[threat(7, 10.), threat(8, 20.)]),
            Some(1)
        );
        let value = serde_json::to_value(&mob).unwrap();
        let mut restored = Mob::new(definition());
        restored
            .restore_from(serde_json::from_value(value.clone()).unwrap())
            .unwrap();
        assert_eq!(
            restored.select_target(&[threat(7, 10.), threat(8, 20.)]),
            Some(1)
        );
        let mut old = value.clone();
        old.as_object_mut().unwrap().remove("aggro");
        old.as_object_mut().unwrap().remove("unowned_provoked");
        restored
            .restore_from(serde_json::from_value(old).unwrap())
            .unwrap();
        let mut corrupt = value;
        corrupt["aggro"]["entries"][1]["player"] = serde_json::json!(7);
        let previous_shield = restored.shield;
        assert!(restored
            .restore_from(serde_json::from_value(corrupt).unwrap())
            .is_err());
        assert_eq!(restored.shield, previous_shield);
    }

    #[test]
    fn pacify_damage_break_death_and_respawn_reset_hate_without_bystander_retaliation() {
        let mut def = definition();
        def.aggressive = false;
        let mut mob = Mob::new(def);
        let players = [threat(1, 1.), threat(2, 500.)];
        mob.drain_shield_by(10., 2);
        assert_eq!(mob.select_target(&players), Some(1));
        mob.pacify(30.);
        assert_eq!(mob.select_target(&players), None);
        mob.damage_by(1., 2);
        assert_eq!(mob.select_target(&players), Some(1));
        mob.damage_by(1000., 2);
        assert_eq!(mob.select_target(&players), None);
        mob.tick_visible(2., [0.; 3], false);
        assert_eq!(mob.state, MobState::Idle);
        assert_eq!(mob.select_target(&players), None);
    }
    #[test]
    fn shield_only_mobs_live_until_shields_empty_and_leech_records_the_killer() {
        let mut def = definition();
        def.combat_override = None;
        def.mob_type = 5;
        def.level = 30;
        let mut mob = Mob::new(def);
        assert_eq!(mob.health, 0.);
        let initial = mob.shield;
        mob.damage_by(1., 7);
        assert_ne!(mob.state, MobState::Dead);
        assert_eq!(mob.shield, initial - 1.);
        mob.drain_shield_by(initial, 7);
        assert_eq!(mob.state, MobState::Dead);
        assert_eq!(mob.killer, Some(7));
        assert_eq!(mob.death_generation, 1);
        mob.pacify(10.);
        assert_eq!(mob.state, MobState::Dead);
        mob.tick(2., [0.; 3]);
        assert_eq!(mob.state, MobState::Idle);
        assert_eq!(mob.shield, initial);
        assert_eq!(mob.health, 0.);
    }

    #[test]
    fn checkpoint_rebinds_public_stats_and_preserves_damage_rng_and_death_instead_of_old_caps() {
        let mut old = Mob::new(definition());
        old.shield = 75.;
        old.health = 50.;
        old.position = [100., 0., 0.];
        old.cooldown = 1.;
        let mut public = definition();
        public.combat_override = None;
        public.level = 66;
        public.mob_type = 1;
        let mut current = Mob::new(public.clone());
        let maximum = current.max_shield;
        current.restore_from(old).unwrap();
        assert_eq!(current.definition.level, 66);
        assert_eq!(current.shield, maximum * 0.5);
        assert_eq!(current.health, current.max_health * 0.5);
        assert_eq!(current.position, [100., 0., 0.]);
        assert_eq!(current.cooldown, 1.);
        let mut corrupt = current.clone();
        corrupt.shield = f32::NAN;
        assert!(current.restore_from(corrupt).is_err());
        assert_eq!(current.shield, maximum * 0.5);
        current.damage_by(100_000_000., 42);
        let mut restored = Mob::new(public);
        restored.restore_from(current).unwrap();
        assert_eq!(restored.state, MobState::Dead);
        assert_eq!(restored.killer, Some(42));
        assert_eq!(restored.shield, 0.);
        assert_eq!(restored.health, 0.);
    }

    #[test]
    fn archived_three_roll_damage_varies_within_bounds_and_rng_resumes_exactly() {
        let mut def = definition();
        def.combat_override = None;
        def.level = 60;
        let mut original = Mob::new(def.clone());
        let first = original.tick(4., [0.; 3]);
        let mut resumed = Mob::new(def);
        let snapshot: Mob =
            serde_json::from_slice(&serde_json::to_vec(&original).unwrap()).unwrap();
        resumed.restore_from(snapshot).unwrap();
        let mut distinct = std::collections::HashSet::new();
        distinct.insert(first.to_bits());
        for _ in 0..30 {
            let damage = original.tick(4., [0.; 3]);
            assert_eq!(resumed.tick(4., [0.; 3]), damage);
            assert!(damage >= original.combat.dps * original.combat.reload * 0.1);
            assert!(damage <= original.combat.dps * original.combat.reload * 1.301);
            distinct.insert(damage.to_bits());
        }
        assert!(distinct.len() > 10);
    }

    #[test]
    fn shields_absorb_damage_and_leech_never_damages_hull() {
        let mut mob = Mob::new(definition());
        mob.damage(50.);
        assert_eq!(mob.shield, 100.);
        assert_eq!(mob.health, 100.);
        assert_eq!(mob.drain_shield(200.), 100.);
        assert_eq!(mob.health, 100.);
        mob.damage(100.);
        assert_eq!(mob.state, MobState::Dead);
        mob.tick(2., [0.; 3]);
        assert_eq!(mob.shield, 150.);
        assert_eq!(mob.health, 100.);
    }
    #[test]
    fn cloak_and_contemplation_suppress_attacks_until_broken() {
        let mut mob = Mob::new(definition());
        assert_eq!(mob.tick_visible(1., [0.; 3], false), 0.);
        mob.pacify(3.);
        assert_eq!(mob.tick(1., [0.; 3]), 0.);
        mob.damage(1.);
        assert!(mob.tick(1., [0.; 3]) > 0.);
    }
    #[test]
    fn full_mob_lifecycle() {
        let mut mob = Mob::new(definition());
        mob.tick(0.1, [2000., 0., 0.]);
        assert_eq!(mob.state, MobState::Chasing);
        assert!(mob.tick(0.1, [0.; 3]) > 0.);
        assert_eq!(mob.state, MobState::Attacking);
        mob.damage(1000.);
        assert_eq!(mob.state, MobState::Dead);
        assert_eq!(mob.tick(1., [0.; 3]), 0.);
        assert_eq!(mob.state, MobState::Dead);
        mob.tick(1., [0.; 3]);
        assert_eq!(mob.state, MobState::Idle);
        assert_eq!(mob.health, mob.max_health);
    }
    #[test]
    fn leash_returns_to_spawn_and_never_overshoots() {
        let mut mob = Mob::new(definition());
        mob.damage(1.);
        mob.tick(1., [20000., 0., 0.]);
        assert_eq!(mob.state, MobState::Idle);
        let mut p = [0.; 3];
        move_towards(&mut p, [1.; 3], 1000.);
        assert_eq!(p, [1.; 3]);
    }
}
