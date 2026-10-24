//! Public actor state sent to nearby players. Account credentials and private
//! inventories are deliberately absent from this record.
use crate::account::CharacterProfile;
use crate::equipment_stats::ShipStats;
use crate::motion::Motion;
use serde::{Deserialize, Serialize};

/// Current public state needed for native actions on a visible player target.
#[derive(Clone, Copy)]
pub(crate) struct PeerContact {
    pub position: [f32; 3],
    pub incapacitated: bool,
}

/// Interior avatar coordinates are independent of the ship's sector position.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StationPose {
    pub room: u32,
    pub orientation: f32,
    pub position: [f32; 3],
    pub action: u32,
}

impl StationPose {
    pub fn valid_for(&self, station: &crate::content::Station) -> bool {
        station.rooms.iter().any(|room| room.index == self.room)
            && self.orientation.is_finite()
            && self
                .position
                .iter()
                .all(|value| value.is_finite() && value.abs() <= 100_000.)
    }
}

#[derive(Clone)]
pub struct StationPeerSnapshot {
    pub profile: CharacterProfile,
    pub station: u32,
    pub pose: StationPose,
}

#[derive(Clone)]
pub struct PeerSnapshot {
    pub stats: ShipStats,
    pub profile: CharacterProfile,
    pub sector: u32,
    pub position: [f32; 3],
    pub motion: Motion,
    pub shield: f32,
    pub hull: f32,
    pub cloaked: bool,
}
