//! Native impulse type 12: Face/FollowTarget speed tiers from PlayerClass.cpp.
//! Visibility and ownership belong to the session, not this movement policy.
use crate::mob::distance;
use crate::motion::{Bounds, Motion};
use crate::protocol::invalid;
use std::io;

pub const FOLLOW_VISIBILITY: f32 = 20_000.;
pub const PLAYER_RADIUS: f32 = 245.;

#[derive(Clone, Copy, Debug)]
pub struct FollowContact {
    pub position: [f32; 3],
    pub speed: f32,
    pub radius: f32,
}

pub fn follow_speed(range: f32, target_speed: f32, maximum: f32) -> f32 {
    let target = target_speed.clamp(-maximum / 2., maximum);
    if range < 10. {
        0.
    } else if range < 200. {
        target * 0.25
    } else if range > 1500. {
        maximum
    } else {
        target
    }
}

impl FollowContact {
    pub fn range_to(self, position: [f32; 3]) -> f32 {
        (distance(position, self.position) - self.radius).max(0.)
    }
    pub fn advance(
        self,
        motion: &mut Motion,
        position: &mut [f32; 3],
        bounds: &Bounds,
        dt: f32,
        maximum: f32,
    ) -> io::Result<()> {
        if !self.speed.is_finite()
            || !self.radius.is_finite()
            || !(0. ..=2_000_000.).contains(&self.radius)
            || !bounds.contains(self.position)
            || !maximum.is_finite()
            || maximum < 0.
        {
            return Err(invalid("Invalid follow target motion."));
        }
        let range = self.range_to(*position);
        if distance(*position, self.position) > 0.001 {
            motion.face(std::array::from_fn(|axis| {
                self.position[axis] - position[axis]
            }))?;
        }
        let speed = follow_speed(range, self.speed, maximum.min(motion.max_speed));
        // The archived follow assigns velocity directly, without a thrust
        // acceleration ramp. Limit a positive step so it cannot pass its target.
        let speed = if dt > 0. && speed > 0. {
            speed.min((range - 10.).max(0.) / dt)
        } else {
            speed
        };
        motion.speed = speed;
        motion.set_speed = speed;
        motion.turn = 0.;
        motion.tilt = 0.;
        motion.advance(position, bounds, dt);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_speed_tiers_honor_engine_limits_and_match_reverse_targets() {
        assert_eq!(follow_speed(5., 200., 100.), 0.);
        assert_eq!(follow_speed(100., 200., 100.), 25.);
        assert_eq!(follow_speed(1000., 60., 100.), 60.);
        assert_eq!(follow_speed(2000., 0., 100.), 100.);
        assert_eq!(follow_speed(1000., -200., 100.), -50.);
    }
    #[test]
    fn follows_the_surface_without_crossing_and_rejects_invalid_geometry() {
        let mut motion = Motion::default();
        let mut position = [0.; 3];
        let mut contact = FollowContact {
            position: [2000., 0., 0.],
            speed: 100.,
            radius: 500.,
        };
        contact
            .advance(&mut motion, &mut position, &Bounds::default(), 100., 250.)
            .unwrap();
        assert_eq!(position, [1490., 0., 0.]);
        assert_eq!(contact.range_to(position), 10.);
        contact.radius = -1.;
        assert!(contact
            .advance(&mut motion, &mut position, &Bounds::default(), 1., 250.)
            .is_err());
    }
    #[test]
    fn following_faces_in_three_dimensions_and_never_crosses_a_target_on_a_large_tick() {
        let mut motion = Motion::default();
        let mut position = [0.; 3];
        FollowContact {
            position: [2000., 2000., 2000.],
            speed: 0.,
            radius: 0.,
        }
        .advance(&mut motion, &mut position, &Bounds::default(), 100., 250.)
        .unwrap();
        assert!((distance(position, [2000.; 3]) - 10.).abs() < 0.1);
        let heading = motion.heading();
        assert!((heading[0] - 1. / 3f32.sqrt()).abs() < 0.001);
        let before = position;
        FollowContact {
            position,
            speed: 100.,
            radius: 0.,
        }
        .advance(&mut motion, &mut position, &Bounds::default(), 1., 250.)
        .unwrap();
        assert_eq!(position, before);
        assert_eq!(motion.speed, 0.);
    }
}
