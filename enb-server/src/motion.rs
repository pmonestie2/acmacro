//! Impulse flight and orientation, following Player::Move/CalcVelocity and
//! Object::Turn/Tilt. PlayerSession owns position; this object owns steering and speed.
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::io;

pub const MAX_SPEED: f32 = 250.;
pub const MIN_SPEED: f32 = -125.;
pub const ACCELERATION: f32 = 75.;
fn default_max_speed() -> f32 {
    MAX_SPEED
}
fn default_acceleration() -> f32 {
    ACCELERATION
}
fn default_turn_scale() -> f32 {
    1.
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Bounds {
    pub min: [f32; 3],
    pub max: [f32; 3],
}

impl Default for Bounds {
    fn default() -> Self {
        Self {
            min: [-200000., -200000., -50000.],
            max: [200000., 200000., 50000.],
        }
    }
}

impl Bounds {
    pub fn valid(&self) -> bool {
        (0..3).all(|i| {
            self.min[i].is_finite()
                && self.max[i].is_finite()
                && self.min[i] < self.max[i]
                && self.min[i].abs() <= 2_000_000.
                && self.max[i].abs() <= 2_000_000.
        })
    }
    pub fn contains(&self, position: [f32; 3]) -> bool {
        (0..3).all(|i| {
            position[i].is_finite() && position[i] >= self.min[i] && position[i] <= self.max[i]
        })
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Motion {
    #[serde(default = "default_max_speed")]
    pub max_speed: f32,
    #[serde(default = "default_acceleration")]
    pub acceleration: f32,
    #[serde(default = "default_turn_scale")]
    pub turn_scale: f32,
    pub orientation: [f32; 4],
    pub speed: f32,
    pub set_speed: f32,
    pub turn: f32,
    pub tilt: f32,
    pub movement_id: u32,
}

impl Default for Motion {
    fn default() -> Self {
        Self {
            max_speed: MAX_SPEED,
            acceleration: ACCELERATION,
            turn_scale: 1.,
            orientation: [0., 0., 0., 1.],
            speed: 0.,
            set_speed: 0.,
            turn: 0.,
            tilt: 0.,
            movement_id: 5,
        }
    }
}

fn multiply(a: [f32; 4], b: [f32; 4]) -> [f32; 4] {
    let [x, y, z, w] = a;
    let [u, v, s, t] = b;
    [
        w * u + x * t + y * s - z * v,
        w * v - x * s + y * t + z * u,
        w * s + x * v - y * u + z * t,
        w * t - x * u - y * v - z * s,
    ]
}

impl Motion {
    /// Rotate a model-local vector into sector space using the ship quaternion.
    pub fn rotate(&self, vector: [f32; 3]) -> [f32; 3] {
        let [x, y, z, w] = self.orientation;
        let cross = |a: [f32; 3], b: [f32; 3]| {
            [
                a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0],
            ]
        };
        let uv = cross([x, y, z], vector);
        let uuv = cross([x, y, z], uv);
        std::array::from_fn(|i| vector[i] + 2. * (w * uv[i] + uuv[i]))
    }
    pub fn thrust(&mut self, command: u8) -> io::Result<()> {
        self.set_speed = match command {
            0 | 2 => self.max_speed,
            1 | 3 => -self.max_speed / 2.,
            4 => 0.,
            5 => return Ok(()),
            _ => return Err(invalid("unsupported impulse movement command")),
        };
        self.movement_id = self.movement_id.wrapping_add(1);
        Ok(())
    }
    pub fn steer(&mut self, turning: bool, intensity: f32) -> io::Result<()> {
        if !intensity.is_finite() {
            return Err(invalid("nonfinite steering intensity"));
        }
        if turning {
            self.turn = intensity.clamp(-1., 1.);
        } else {
            self.tilt = intensity.clamp(-1., 1.);
        }
        self.movement_id = self.movement_id.wrapping_add(1);
        Ok(())
    }
    pub fn stop(&mut self) {
        self.speed = 0.;
        self.set_speed = 0.;
        self.turn = 0.;
        self.tilt = 0.;
        self.movement_id = self.movement_id.wrapping_add(1);
    }
    pub fn heading(&self) -> [f32; 3] {
        let [x, y, z, w] = self.orientation;
        // The client's model-forward vector is +X, not +Z.
        [
            1. - 2. * (y * y + z * z),
            2. * (x * y + z * w),
            2. * (x * z - y * w),
        ]
    }
    pub fn face(&mut self, direction: [f32; 3]) -> io::Result<()> {
        let length = direction.iter().map(|v| v * v).sum::<f32>().sqrt();
        if !length.is_finite() || length < 0.0001 {
            return Err(invalid("invalid motion heading"));
        }
        let yaw = direction[1].atan2(direction[0]) * 0.5;
        let pitch = -(direction[2] / length).clamp(-1., 1.).asin() * 0.5;
        self.orientation = multiply(
            [0., 0., yaw.sin(), yaw.cos()],
            [0., pitch.sin(), 0., pitch.cos()],
        );
        Ok(())
    }
    pub fn advance(&mut self, position: &mut [f32; 3], bounds: &Bounds, dt: f32) {
        if dt <= 0. || !dt.is_finite() {
            return;
        }
        // This is the archived quaternion increment, normalized after applying
        // local pitch and yaw. Packet angular velocities use radians/ms.
        let factor = dt * 0.42 / 883. * 1000. * self.turn_scale;
        let rotation = multiply(
            [0., 0., -self.turn * factor, 1.],
            [0., -self.tilt * factor, 0., 1.],
        );
        let orientation = multiply(self.orientation, rotation);
        let length = orientation.iter().map(|v| v * v).sum::<f32>().sqrt();
        self.orientation = orientation.map(|v| v / length);
        let difference = self.set_speed - self.speed;
        let time = (difference.abs() / self.acceleration.max(0.01)).min(dt);
        let acceleration = difference.signum() * self.acceleration.max(0.01);
        let end_speed = self.speed + acceleration * time;
        let distance =
            self.speed * time + 0.5 * acceleration * time * time + end_speed * (dt - time);
        self.speed = end_speed;
        let heading = self.heading();
        let mut boundary = false;
        for i in 0..3 {
            let next = position[i] + heading[i] * distance;
            position[i] = next.clamp(bounds.min[i], bounds.max[i]);
            boundary |= position[i] != next;
        }
        if boundary {
            self.stop();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rotates_formation_offset_using_ship_orientation() {
        let mut motion = Motion::default();
        motion.face([0., 1., 0.]).unwrap();
        let offset = motion.rotate([0., -200., 50.]);
        assert!((offset[0] - 200.).abs() < 0.001);
        assert!(offset[1].abs() < 0.001);
        assert!((offset[2] - 50.).abs() < 0.001);
    }
    #[test]
    fn accelerates_brakes_reverses_without_overshoot() {
        let mut motion = Motion::default();
        let mut p = [0.; 3];
        let bounds = Bounds::default();
        motion.thrust(2).unwrap();
        for _ in 0..40 {
            motion.advance(&mut p, &bounds, 0.1);
        }
        assert!((motion.speed - MAX_SPEED).abs() < 0.01);
        assert!((p[0] - (250. * 4. - 250. * 250. / (2. * ACCELERATION))).abs() < 0.1);
        motion.thrust(4).unwrap();
        for _ in 0..40 {
            motion.advance(&mut p, &bounds, 0.1);
        }
        assert!(motion.speed.abs() < 0.001);
        motion.thrust(3).unwrap();
        for _ in 0..40 {
            motion.advance(&mut p, &bounds, 0.1);
        }
        assert!((motion.speed - MIN_SPEED).abs() < 0.01);
    }
    #[test]
    fn steering_changes_heading_and_boundary_stops_flight() {
        let mut motion = Motion::default();
        let mut p = [0.; 3];
        motion.steer(true, 1.).unwrap();
        for _ in 0..10 {
            motion.advance(&mut p, &Bounds::default(), 0.1);
        }
        assert!(motion.heading()[1] < -0.5);
        motion.stop();
        motion.face([1., 0., 0.]).unwrap();
        motion.thrust(2).unwrap();
        let bounds = Bounds {
            min: [-10.; 3],
            max: [10.; 3],
        };
        motion.advance(&mut p, &bounds, 1.);
        assert_eq!(p[0], 10.);
        assert_eq!(motion.speed, 0.);
        assert!(motion.steer(false, f32::NAN).is_err());
    }
}
