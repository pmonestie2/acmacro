//! Frozen NPC shots. Sector storage retains due impacts until the player's
//! durable damage receipt has been committed and the coordinator acknowledges.
use crate::mob::MobDefinition;
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::io;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MobHit {
    pub sector: u32,
    pub sequence: u64,
    pub target: u32,
    pub target_epoch: u64,
    pub attacker_index: usize,
    pub attacker_source: u32,
    pub attacker_template: u32,
    pub attacker_generation: u64,
    pub damage: f32,
    pub damage_type: usize,
}

impl MobHit {
    pub fn valid(&self) -> bool {
        self.sector != 0
            && self.sequence != 0
            && (0x40000001..=0x4000ffff).contains(&self.target)
            && self.target_epoch != 0
            && self.attacker_index < 256
            && self.attacker_source != 0
            && self.damage.is_finite()
            && self.damage > 0.
            && self.damage_type < 9
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PendingNpcShot {
    pub hit: MobHit,
    pub effect: u16,
    pub emitted_ms: u64,
    pub due_ms: u64,
    pub duration_ms: u32,
}

impl PendingNpcShot {
    pub fn valid(&self) -> bool {
        self.hit.valid()
            && self.emitted_ms.checked_add(u64::from(self.duration_ms)) == Some(self.due_ms)
    }
}

#[derive(Clone, Debug)]
pub struct MobLaunchVisual {
    pub sector: u32,
    pub sequence: u64,
    pub target: u32,
    pub attacker_index: usize,
    pub effect: u16,
    pub duration_ms: u32,
}

/// MOBClass::FireWeapon uses a fixed one second for beams and range*3ms for
/// launchers. Fallback structural/cybernetic/energy attacks use the beam path;
/// rock/organic/other fallback types use the delayed path. This does not guess
/// an ammunition template or replace the archived NPC DPS/reload calculation.
pub fn travel_ms(definition: &MobDefinition, range: f32) -> io::Result<u32> {
    if !range.is_finite() || range < 0. {
        return Err(invalid("Invalid NPC weapon range."));
    }
    let delayed = match &definition.weapon {
        Some(weapon) => match weapon.item_type {
            14 => false,
            15 | 16 => true,
            _ => return Err(invalid("Invalid NPC weapon category.")),
        },
        None => !matches!(definition.mob_type, 0 | 1 | 5),
    };
    if !delayed {
        return Ok(1000);
    }
    let milliseconds = (f64::from(range) * 3.).trunc();
    if milliseconds > f64::from(u32::MAX) {
        return Err(invalid("NPC projectile travel time overflow."));
    }
    Ok(milliseconds as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_beams_launchers_and_fallback_types_use_the_archived_delay() {
        let mut definition: MobDefinition = serde_json::from_str(r#"{"source_id":1,"name":"Test","level":1,"asset":48,"spawn":[0,0,0],"respawn_seconds":20,"aggressive":true}"#).unwrap();
        for kind in [0, 1, 5] {
            definition.mob_type = kind;
            assert_eq!(travel_ms(&definition, 123.45).unwrap(), 1000);
        }
        for kind in [-1, 2, 3, 4, 6] {
            definition.mob_type = kind;
            assert_eq!(travel_ms(&definition, 123.45).unwrap(), 370);
        }
        definition.weapon = Some(crate::mob_stats::MobWeapon {
            item: 1,
            item_type: 14,
            damage: 1.,
            damage_type: 3,
            reload: 1.,
            effect: 35,
        });
        assert_eq!(travel_ms(&definition, 1000.).unwrap(), 1000);
        for kind in [15, 16] {
            definition.weapon.as_mut().unwrap().item_type = kind;
            assert_eq!(travel_ms(&definition, 1000.).unwrap(), 3000);
            assert_eq!(travel_ms(&definition, 0.).unwrap(), 0);
        }
        assert!(travel_ms(&definition, f32::NAN).is_err());
        assert!(travel_ms(&definition, -1.).is_err());
        assert!(travel_ms(&definition, f32::MAX).is_err());
    }
}
