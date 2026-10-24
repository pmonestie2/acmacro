//! Ordinary SQL spawn groups use the active MOBSpawn/MOBClass DRIFT and CURIOUS
//! defaults. No authored route, freeform `ai` text or field-guardian behavior is
//! inferred. Random state and the active-simulation decision clock are saved.
use super::*;
use crate::mob_targeting::mob_detects_cloak;
use crate::motion::Bounds;
use crate::world_random::WorldRandom;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SpawnRegion {
    pub center: [f32; 3],
    pub radius: f32,
}

impl SpawnRegion {
    pub fn valid(&self) -> bool {
        self.radius.is_finite()
            && self.radius >= 0.
            && self
                .center
                .iter()
                .all(|value| value.is_finite() && value.abs() + self.radius <= 2_000_000.)
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
enum IdleBehavior {
    #[default]
    Drift,
    Curious {
        player: u32,
        epoch: u64,
    },
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub(super) struct RoamState {
    initialized: bool,
    random_state: u64,
    default_speed: f32,
    default_turn: f32,
    decision_seconds: f32,
    behavior: IdleBehavior,
}

impl RoamState {
    pub fn reset_interest(&mut self) {
        self.behavior = IdleBehavior::Drift;
    }

    pub fn forget_player(&mut self, player: u32) {
        if matches!(self.behavior, IdleBehavior::Curious { player: target, .. } if target == player)
        {
            self.reset_interest();
        }
    }

    pub fn validate(&self) -> std::io::Result<()> {
        if !self.default_speed.is_finite()
            || !self.default_turn.is_finite()
            || !self.decision_seconds.is_finite()
            || !(0. ..=10.).contains(&self.decision_seconds)
            || self.initialized
                && (!(130. ..=229.).contains(&self.default_speed)
                    || !(-0.05..=0.05).contains(&self.default_turn)
                    || self.default_turn == 0.)
            || matches!(
                self.behavior,
                IdleBehavior::Curious { player: 0, .. } | IdleBehavior::Curious { epoch: 0, .. }
            )
        {
            return Err(crate::protocol::invalid("Invalid MOB roaming checkpoint."));
        }
        Ok(())
    }

    fn below(&mut self, limit: u32) -> u32 {
        let mut random = WorldRandom(self.random_state);
        let value = random.below(limit);
        self.random_state = random.0;
        value
    }

    fn random_movement(&mut self) {
        self.default_speed = (130 + self.below(100)) as f32;
        self.default_turn = (self.below(11) as i32 - 5) as f32 * 0.01;
        if self.default_turn == 0. {
            self.default_turn = 0.02;
        }
    }

    fn initialize(&mut self, source: u32, template: u32) {
        if !self.initialized {
            // A separate stream prevents idle scenery movement from rerolling
            // the existing combat-dispersion stream or already frozen shots.
            self.random_state =
                u64::from(source) ^ (u64::from(template) << 32) ^ 0x726f_616d_696e_6701;
            self.random_movement();
            self.decision_seconds = 10.;
            self.initialized = true;
        }
    }
}

/// Native PURSUE uses a one-second movement horizon. Its 400..750m holding
/// band and reverse below400 apply independently of weapon range/reload.
fn pursuit_speed(range: f32, default: f32) -> f32 {
    if range < default || range < 750. {
        if range < 400. {
            -50.
        } else if range < 750. {
            0.
        } else {
            (range - 650.).min(default * 1.2)
        }
    } else {
        default
    }
}

/// CURIOUS retains the archived five-second default movement horizon, including
/// its condition ordering rather than inventing a new approach route.
fn curious_speed(range: f32, default: f32) -> f32 {
    if range < default * 5. {
        if range < 1000. {
            0.
        } else {
            ((range - 900.) / 5.).min(default * 0.9)
        }
    } else {
        default
    }
}

impl Mob {
    pub(super) fn native_roaming(&self) -> bool {
        self.definition
            .spawn_region
            .as_ref()
            .is_some_and(|region| region.radius > 0.)
    }

    pub(super) fn finish_native_motion(&mut self, before: [f32; 3], previous: &Motion) {
        self.speed = self.motion.speed.abs();
        if before != self.position
            || previous.orientation != self.motion.orientation
            || previous.speed != self.motion.speed
            || previous.turn != self.motion.turn
        {
            self.motion.movement_id = previous.movement_id.wrapping_add(5);
        }
    }

    fn face_point(&mut self, point: [f32; 3]) {
        if distance(self.position, point) > 0.0001 {
            let _ = self.motion.face(std::array::from_fn(|axis| {
                point[axis] - self.position[axis]
            }));
        }
    }

    fn integrate_native_motion(&mut self, dt: f32, speed: f32, turn: f32) {
        self.motion.speed = speed;
        self.motion.set_speed = speed;
        self.motion.turn = turn;
        self.motion.tilt = 0.;
        self.motion.acceleration = 0.;
        if dt > 0. && dt < 5. && dt.is_finite() {
            // Match the archived no-catch-up guard after long suspension. The
            // absolute content bounds also protect the checkpoint invariant.
            let bounds = Bounds {
                min: [-2_000_000.; 3],
                max: [2_000_000.; 3],
            };
            self.motion.advance(&mut self.position, &bounds, dt);
        }
    }

    fn advance_roam_clock(&mut self, dt: f32) -> bool {
        self.roaming.decision_seconds -= dt;
        if self.roaming.decision_seconds <= 0. {
            self.roaming.decision_seconds += 10.;
            true
        } else {
            false
        }
    }

    pub(super) fn advance_native_pursuit(&mut self, dt: f32, target: [f32; 3]) {
        self.roaming
            .initialize(self.definition.source_id, self.definition.template_id);
        self.roaming.behavior = IdleBehavior::Drift;
        self.face_point(target);
        let speed = pursuit_speed(self.range_to(target), self.roaming.default_speed);
        self.integrate_native_motion(dt, speed, 0.);
        if dt.is_finite() && dt > 0. && dt < 5. && self.advance_roam_clock(dt) {
            // Native HandleMovementChanges draws these rolls even in PURSUE,
            // but only DRIFT acts on them.
            self.roaming.below(50);
            self.roaming.below(50);
        }
    }

    fn choose_idle_change(&mut self, players: &[PlayerThreat]) {
        let roll = self.roaming.below(50) + self.roaming.below(50);
        if !matches!(self.roaming.behavior, IdleBehavior::Drift) {
            return;
        }
        let region = self
            .definition
            .spawn_region
            .as_ref()
            .expect("native roaming has region");
        // Spawn-marker Object::Radius is separate from this MOB's radius.
        // That marker geometry is not imported yet; retain the center bound.
        if distance(self.position, region.center) > region.radius {
            self.face_point(region.center);
            self.roaming.random_movement();
            self.motion.speed = self.roaming.default_speed;
            self.motion.set_speed = self.roaming.default_speed;
            self.motion.turn = self.roaming.default_turn;
        } else if roll > 75 {
            let mut candidates: Vec<_> = players
                .iter()
                .filter(|player| {
                    player.visible
                        && self.sensors.contains(player)
                        && mob_detects_cloak(self.definition.level, player.stealth)
                })
                .collect();
            candidates.sort_by_key(|player| (player.id, player.session_epoch));
            if !candidates.is_empty() {
                let player = candidates[self.roaming.below(candidates.len() as u32) as usize];
                self.roaming.behavior = IdleBehavior::Curious {
                    player: player.id,
                    epoch: player.session_epoch,
                };
                self.face_point(player.position);
                self.motion.turn = 0.;
            }
        } else if roll < 5 {
            self.roaming.random_movement();
            self.motion.speed = self.roaming.default_speed;
            self.motion.set_speed = self.roaming.default_speed;
            self.motion.turn = self.roaming.default_turn;
        }
    }

    fn idle_velocity(&mut self, players: &[PlayerThreat]) -> (f32, f32) {
        match self.roaming.behavior {
            IdleBehavior::Drift => (self.roaming.default_speed, self.roaming.default_turn),
            IdleBehavior::Curious { player, epoch } => {
                if let Some(player) = players
                    .iter()
                    .find(|p| p.id == player && p.session_epoch == epoch && p.visible)
                {
                    self.face_point(player.position);
                    let speed =
                        curious_speed(self.range_to(player.position), self.roaming.default_speed);
                    if speed == 0. {
                        self.roaming.behavior = IdleBehavior::Drift;
                    }
                    (speed, 0.)
                } else {
                    self.roaming.behavior = IdleBehavior::Drift;
                    (self.roaming.default_speed, self.roaming.default_turn)
                }
            }
        }
    }

    pub(super) fn advance_roaming(&mut self, dt: f32, players: &[PlayerThreat]) {
        self.roaming
            .initialize(self.definition.source_id, self.definition.template_id);
        let before = self.position;
        let previous = self.motion.clone();
        // As in Object::CalcNewPosition, an outage is not simulated as a large
        // catch-up jump. Active frames use bounded half-second native steps.
        if dt.is_finite() && dt > 0. && dt < 5. {
            let mut remaining = dt;
            while remaining > 0. {
                let step = remaining.min(0.5);
                let (speed, turn) = self.idle_velocity(players);
                self.integrate_native_motion(step, speed, turn);
                if self.advance_roam_clock(step) {
                    self.choose_idle_change(players);
                }
                remaining = (remaining - step).max(0.);
            }
        }
        self.finish_native_motion(before, &previous);
    }
}

#[cfg(test)]
#[path = "mob_roaming_tests.rs"]
mod tests;
