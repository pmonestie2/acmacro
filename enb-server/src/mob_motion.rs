//! Encode the movement the existing MOB AI actually performed. Archived
//! MOB::SendPosition sends quaternion, speed and movement identity through the
//! same PositionInformation layout as players; no second motion solver runs.
use super::*;

impl Mob {
    pub(super) fn validate_motion(&self) -> std::io::Result<()> {
        let motion = &self.motion;
        let norm: f32 = motion.orientation.iter().map(|value| value * value).sum();
        if motion.orientation.iter().any(|value| !value.is_finite())
            || !norm.is_finite()
            || (norm - 1.).abs() > 0.01
            || [motion.acceleration, motion.max_speed, motion.turn_scale]
                .iter()
                .any(|value| !value.is_finite() || *value < 0.)
            || !motion.speed.is_finite()
            || motion.speed < -50.
            || !motion.set_speed.is_finite()
            || motion.set_speed < -50.
            || !motion.turn.is_finite()
            || !motion.tilt.is_finite()
        {
            return Err(crate::protocol::invalid("Invalid MOB motion checkpoint."));
        }
        Ok(())
    }

    pub(super) fn stop_motion(&mut self) {
        self.speed = 0.;
        if self.motion.speed != 0. || self.motion.set_speed != 0. {
            self.motion.movement_id = self.motion.movement_id.wrapping_add(5);
        }
        self.motion.speed = 0.;
        self.motion.set_speed = 0.;
        self.motion.turn = 0.;
        self.motion.tilt = 0.;
    }

    pub(super) fn synchronize_motion(
        &mut self,
        before: [f32; 3],
        target: [f32; 3],
        visible: bool,
        was_dead: bool,
    ) {
        let old_orientation = self.motion.orientation;
        let old_speed = self.motion.speed;
        let displacement = std::array::from_fn(|axis| self.position[axis] - before[axis]);
        let moved = !was_dead && distance(before, self.position) > 0.0001;
        let facing = if moved {
            Some(displacement)
        } else if visible && self.state == MobState::Attacking {
            Some(std::array::from_fn(|axis| {
                target[axis] - self.position[axis]
            }))
        } else {
            None
        };
        if let Some(direction) = facing {
            // Coincident/invalid positions have no meaningful direction. Keep
            // the previous heading instead of replacing it with a default.
            if direction.iter().all(|value| value.is_finite())
                && direction.iter().map(|value| value * value).sum::<f32>() >= 0.00000001
            {
                let _ = self.motion.face(direction);
            }
        }
        self.motion.speed = self.speed;
        self.motion.set_speed = self.speed;
        // AI displacement already includes the chase/return rate. Constant
        // velocity prediction must not add the player's default acceleration.
        self.motion.acceleration = 0.;
        self.motion.turn = 0.;
        self.motion.tilt = 0.;
        if before != self.position
            || old_speed != self.motion.speed
            || old_orientation != self.motion.orientation
        {
            self.motion.movement_id = self.motion.movement_id.wrapping_add(5);
        }
    }
}

#[cfg(test)]
#[path = "mob_motion_tests.rs"]
mod tests;
