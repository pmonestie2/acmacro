//! Active MOB visibility and cloak checks from MOBClass.cpp. Sensor helpers
//! receive the NPC's edge range, as computed by Object::RangeFrom. Native MOB Scan
//! skill stays zero in the archived implementation; no unseen skill is inferred.
use crate::mob::distance;
use crate::mob_aggro::PlayerThreat;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const MOB_SIGNATURE: f32 = 3000.;
pub const SENSOR_HYSTERESIS: f32 = 200.;

fn within_sensor(radius: f64, range: f32, was_visible: bool) -> bool {
    if !radius.is_finite() || radius < 0. || !range.is_finite() || range < 0. {
        return false;
    }
    if was_visible {
        f64::from(range) <= radius + f64::from(SENSOR_HYSTERESIS)
    } else {
        f64::from(range) < radius
    }
}

pub fn player_sees_mob(scan: f32, range: f32, was_visible: bool) -> bool {
    scan.is_finite()
        && scan >= 0.
        && within_sensor(
            f64::from(scan) + f64::from(MOB_SIGNATURE),
            range,
            was_visible,
        )
}

pub fn mob_sees_player(level: u32, signature: f32, range: f32, was_visible: bool) -> bool {
    signature.is_finite()
        && signature >= 0.
        && within_sensor(
            2500. + 150. * f64::from(level) + f64::from(signature),
            range,
            was_visible,
        )
}

pub fn mob_detects_cloak(level: u32, stealth: Option<u32>) -> bool {
    stealth.is_none_or(|stealth| u64::from(level) * 3 >= u64::from(stealth))
}

/// Use trained Cloak skill level, not the activated ability variant's rank.
pub fn cloak_stealth(total_level: u32, trained_cloak: u32) -> u32 {
    total_level.saturating_add(trained_cloak.saturating_mul(5))
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub(crate) struct MobSensors {
    players: BTreeMap<u32, u64>,
}

impl MobSensors {
    pub fn validate(&self) -> std::io::Result<()> {
        if self.players.len() > 65535
            || self
                .players
                .iter()
                .any(|(id, epoch)| *id == 0 || *epoch == 0)
        {
            return Err(crate::protocol::invalid("Invalid MOB sensor checkpoint."));
        }
        Ok(())
    }

    /// Range membership is independent of cloak detection. A fresh login
    /// cannot inherit an old contact's extra 200 metres of retention range.
    pub fn refresh(
        &mut self,
        level: u32,
        position: [f32; 3],
        radius: f32,
        players: &[PlayerThreat],
    ) -> Vec<u32> {
        let mut next = BTreeMap::new();
        let mut replaced = Vec::new();
        for player in players {
            let range = distance(position, player.position) - radius;
            let range = if range < 0. { 0. } else { range };
            if self
                .players
                .get(&player.id)
                .is_some_and(|epoch| *epoch != player.session_epoch)
            {
                replaced.push(player.id);
            }
            if player.id != 0
                && player.session_epoch != 0
                && player.visible
                && mob_sees_player(level, player.signature, range, self.contains(player))
            {
                next.insert(player.id, player.session_epoch);
            }
        }
        self.players = next;
        replaced
    }

    pub fn contains(&self, player: &PlayerThreat) -> bool {
        self.players.get(&player.id) == Some(&player.session_epoch)
    }

    pub fn remove(&mut self, player: u32) {
        self.players.remove(&player);
    }

    pub fn clear(&mut self) {
        self.players.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scan_and_signature_have_strict_entry_and_two_hundred_metre_retention() {
        assert!(player_sees_mob(5000., 7999., false));
        assert!(!player_sees_mob(5000., 8000., false));
        assert!(player_sees_mob(5000., 8200., true));
        assert!(!player_sees_mob(5000., 8200.1, true));
        assert!(mob_sees_player(10, 500., 4499., false));
        assert!(!mob_sees_player(10, 500., 4500., false));
        assert!(mob_sees_player(10, 500., 4700., true));
        assert!(!mob_sees_player(10, 500., 4700.1, true));
        assert!(!player_sees_mob(f32::NAN, 1., false));
        assert!(!mob_sees_player(10, -1., 1., false));
    }

    #[test]
    fn native_cloak_uses_trained_level_and_equality_is_detectable() {
        let je = cloak_stealth(150, 7);
        assert_eq!(je, 185);
        assert!(!mob_detects_cloak(61, Some(je)));
        assert!(mob_detects_cloak(62, Some(je)));
        assert!(mob_detects_cloak(60, Some(180)));
        assert!(mob_detects_cloak(1, None));
    }
}
