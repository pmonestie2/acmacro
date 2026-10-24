//! Warp lifecycle. The C++ server sends distinct charge, travel, arrival and
//! recovery states; moving coordinates alone never starts the client's warp UI.
use crate::mob::distance;
use crate::motion::{Bounds, Motion};
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::io;

pub const WARP_SPEED: f32 = 3000.;
pub const CHARGE_SECONDS: f32 = 2.;
pub const RECOVERY_SECONDS: f32 = 2.;
const WARP_ACCELERATION: f32 = 6000.;
const ARRIVAL_DISTANCE: f32 = 100.;
fn default_speed() -> f32 {
    WARP_SPEED
}
fn default_recovery() -> f32 {
    RECOVERY_SECONDS
}
fn default_stand_off() -> f32 {
    ARRIVAL_DISTANCE
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum WarpPhase {
    Idle,
    Charging,
    Traveling,
    Recovering,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
pub struct Waypoint {
    pub id: u32,
    pub position: [f32; 3],
    #[serde(default = "default_stand_off")]
    pub stand_off: f32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FinishReason {
    Arrived,
    Cancelled,
    Boundary,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WarpEvent {
    Charging,
    Departed,
    Waypoint { index: u32, target: u32 },
    Finished(FinishReason),
    Ready,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Warp {
    #[serde(default = "default_speed")]
    pub speed: f32,
    #[serde(default = "default_recovery")]
    pub recovery_seconds: f32,
    pub phase: WarpPhase,
    waypoints: VecDeque<Waypoint>,
    index: u32,
    remaining: f32,
    free_flight: bool,
}

impl Default for Warp {
    fn default() -> Self {
        Self {
            speed: WARP_SPEED,
            recovery_seconds: RECOVERY_SECONDS,
            phase: WarpPhase::Idle,
            waypoints: VecDeque::new(),
            index: 0,
            remaining: 0.,
            free_flight: false,
        }
    }
}

impl Warp {
    pub fn draining_energy(&self) -> bool {
        self.phase == WarpPhase::Traveling && self.free_flight
    }
    pub fn active(&self) -> bool {
        matches!(self.phase, WarpPhase::Charging | WarpPhase::Traveling)
    }
    pub fn start(
        &mut self,
        waypoints: VecDeque<Waypoint>,
        position: [f32; 3],
        motion: &mut Motion,
    ) -> io::Result<WarpEvent> {
        if self.phase != WarpPhase::Idle {
            return Err(invalid("Warp drive is recovering."));
        }
        if self.speed <= 0. || !self.speed.is_finite() {
            return Err(invalid("Equip a working engine before warping."));
        }
        if waypoints.iter().any(|point| {
            !point.stand_off.is_finite()
                || point.stand_off < 0.
                || point
                    .position
                    .iter()
                    .any(|coordinate| !coordinate.is_finite())
        }) {
            return Err(invalid("Invalid warp destination geometry."));
        }
        if waypoints
            .back()
            .is_some_and(|point| distance(position, point.position) < 4000.)
        {
            return Err(invalid("Target too close for warp drive."));
        }
        motion.stop();
        if let Some(point) = waypoints.front() {
            let direction = std::array::from_fn(|i| point.position[i] - position[i]);
            if distance(position, point.position) > 0.001 {
                motion.face(direction)?;
            }
        }
        self.free_flight = waypoints.is_empty();
        self.waypoints = waypoints;
        self.index = 0;
        self.remaining = CHARGE_SECONDS;
        self.phase = WarpPhase::Charging;
        Ok(WarpEvent::Charging)
    }
    pub fn finish(&mut self, reason: FinishReason, motion: &mut Motion) -> WarpEvent {
        motion.stop();
        self.waypoints.clear();
        self.phase = WarpPhase::Recovering;
        self.remaining = self.recovery_seconds;
        WarpEvent::Finished(reason)
    }
    pub fn reset(&mut self, motion: &mut Motion) {
        let speed = self.speed;
        let recovery = self.recovery_seconds;
        *self = Self::default();
        self.speed = speed;
        self.recovery_seconds = recovery;
        motion.stop();
    }
    pub fn advance(
        &mut self,
        position: &mut [f32; 3],
        motion: &mut Motion,
        bounds: &Bounds,
        dt: f32,
    ) -> io::Result<Vec<WarpEvent>> {
        let mut events = Vec::new();
        if dt <= 0. || !dt.is_finite() {
            return Ok(events);
        }
        if matches!(self.phase, WarpPhase::Charging | WarpPhase::Recovering) {
            self.remaining -= dt;
            if self.remaining > 0. {
                return Ok(events);
            }
            if self.phase == WarpPhase::Recovering {
                self.phase = WarpPhase::Idle;
                events.push(WarpEvent::Ready);
            } else {
                self.phase = WarpPhase::Traveling;
                motion.set_speed = self.speed;
                events.push(WarpEvent::Departed);
                if let Some(point) = self.waypoints.front() {
                    events.push(WarpEvent::Waypoint {
                        index: 0,
                        target: point.id,
                    });
                }
            }
            return Ok(events);
        }
        if self.phase != WarpPhase::Traveling {
            return Ok(events);
        }
        let previous_speed = motion.speed;
        motion.speed = (motion.speed + WARP_ACCELERATION * dt).min(self.speed);
        motion.set_speed = self.speed;
        let mut travel = (previous_speed + motion.speed) * 0.5 * dt;
        if let Some(point) = self.waypoints.front().copied() {
            let length = distance(*position, point.position);
            if length > 0.001 {
                motion.face(std::array::from_fn(|i| point.position[i] - position[i]))?;
            }
            let stand_off = if self.waypoints.len() == 1 {
                point.stand_off
            } else {
                0.
            };
            travel = travel.min((length - stand_off).max(0.));
        }
        let heading = motion.heading();
        let next = std::array::from_fn(|i| position[i] + heading[i] * travel);
        if !bounds.contains(next) {
            for i in 0..3 {
                position[i] = next[i].clamp(bounds.min[i], bounds.max[i]);
            }
            events.push(self.finish(FinishReason::Boundary, motion));
            return Ok(events);
        }
        *position = next;
        if let Some(point) = self.waypoints.front() {
            let stand_off = if self.waypoints.len() == 1 {
                point.stand_off
            } else {
                0.
            };
            if distance(*position, point.position) <= stand_off + 0.1 {
                self.waypoints.pop_front();
                self.index += 1;
                if let Some(point) = self.waypoints.front() {
                    events.push(WarpEvent::Waypoint {
                        index: self.index,
                        target: point.id,
                    });
                } else {
                    events.push(self.finish(FinishReason::Arrived, motion));
                }
            }
        }
        Ok(events)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn route_charges_follows_waypoints_arrives_and_recovers() {
        let mut warp = Warp::default();
        let mut motion = Motion::default();
        let mut position = [0.; 3];
        let route = VecDeque::from([
            Waypoint {
                id: 1,
                position: [5000., 0., 0.],
                stand_off: ARRIVAL_DISTANCE,
            },
            Waypoint {
                id: 2,
                position: [5000., 5000., 0.],
                stand_off: ARRIVAL_DISTANCE,
            },
        ]);
        warp.start(route, position, &mut motion).unwrap();
        let mut events = Vec::new();
        for _ in 0..120 {
            events.extend(
                warp.advance(&mut position, &mut motion, &Bounds::default(), 0.1)
                    .unwrap(),
            );
        }
        assert!(events.contains(&WarpEvent::Departed));
        assert!(events.contains(&WarpEvent::Waypoint {
            index: 1,
            target: 2
        }));
        assert!(events.contains(&WarpEvent::Finished(FinishReason::Arrived)));
        assert!(events.contains(&WarpEvent::Ready));
        assert!((distance(position, [5000., 5000., 0.]) - ARRIVAL_DISTANCE).abs() < 0.1);
        assert_eq!(motion.speed, 0.);
    }
    #[test]
    fn free_warp_cancels_and_recovery_blocks_new_requests() {
        let mut warp = Warp::default();
        let mut motion = Motion::default();
        let mut position = [0.; 3];
        warp.start(VecDeque::new(), position, &mut motion).unwrap();
        for _ in 0..40 {
            warp.advance(&mut position, &mut motion, &Bounds::default(), 0.1)
                .unwrap();
        }
        assert!(position[0] > 4000.);
        warp.finish(FinishReason::Cancelled, &mut motion);
        assert!(!warp.active());
        assert_eq!(motion.speed, 0.);
        assert!(warp.start(VecDeque::new(), position, &mut motion).is_err());
        assert_eq!(warp.phase, WarpPhase::Recovering);
    }
}
