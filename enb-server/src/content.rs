//! Public static sector content and validation, independent of player sessions.
use crate::mob::MobDefinition;
use crate::motion::Bounds;
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::io;

fn one() -> f32 {
    1.
}
fn orientation() -> [f32; 4] {
    [0., 0., 0., 1.]
}
fn signature() -> f32 {
    5000.
}
fn visible() -> bool {
    true
}
fn navigation_type() -> u8 {
    1
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NavPoint {
    pub id: u32,
    pub name: String,
    pub object_type: u8,
    pub asset: u16,
    #[serde(default)]
    pub radius: f32,
    pub position: [f32; 3],
    #[serde(default = "one")]
    pub scale: f32,
    #[serde(default = "orientation")]
    pub orientation: [f32; 4],
    #[serde(default)]
    pub hsv: [f32; 3],
    #[serde(default = "signature")]
    pub signature: f32,
    #[serde(default = "visible")]
    pub navigation_present: bool,
    #[serde(default = "navigation_type")]
    pub navigation_type: u8,
    #[serde(default)]
    pub huge: bool,
    #[serde(default)]
    pub gate_destination: Option<u32>,
}

impl NavPoint {
    /// Native RangeFrom's unscaled receiver radius. Facing and explicitly
    /// absolute travel/animation distances still use the object center.
    pub fn range_to(&self, position: [f32; 3]) -> f32 {
        let range = crate::mob::distance(self.position, position) - self.radius;
        if range < 0. {
            0.
        } else {
            range
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Sector {
    pub id: u32,
    pub name: String,
    pub system: String,
    pub backdrop_asset: u16,
    #[serde(default)]
    pub bounds: Bounds,
    pub navpoints: Vec<NavPoint>,
    pub mobs: Vec<MobDefinition>,
    /// Deterministic field instances; ore metadata also remains in the export.
    #[serde(default)]
    pub resources: Vec<ResourceNode>,
    #[serde(default)]
    pub station: Option<Station>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Station {
    pub id: u32,
    pub parent: u32,
    pub exterior: u32,
    pub name: String,
    pub kind: u32,
    pub rooms: Vec<StationRoom>,
    pub terminals: Vec<StationTerminal>,
    #[serde(default)]
    pub npcs: Vec<StationNpc>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResourceOre {
    pub item_id: u32,
    pub frequency: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResourceNode {
    #[serde(flatten)]
    pub object: NavPoint,
    pub field_id: u32,
    pub level: u16,
    pub resource_type: u8,
    pub ore_items: Vec<ResourceOre>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StationNpc {
    pub id: u32,
    pub room: u32,
    pub location: u32,
    pub booth: i32,
    pub avatar: Vec<u8>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StationRoom {
    pub index: u32,
    pub style: u32,
    pub fog_near: f32,
    pub fog_far: f32,
    pub fog_color: [u32; 3],
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StationTerminal {
    pub room: u32,
    pub location: u32,
    pub kind: u32,
}
impl Sector {
    pub fn validate(&self) -> io::Result<()> {
        if self.id == 0
            || self.backdrop_asset == 0
            || !self.bounds.valid()
            || (self.id >= 10000) != self.station.is_some()
            || self.navpoints.len() > 1000
            || self.mobs.len() > 256
            || self.resources.len() > 8192
            || self.name.len() > 60
            || !self.name.is_ascii()
        {
            return Err(invalid("invalid sector configuration"));
        }
        if let Some(station) = &self.station {
            if station.id != self.id
                || station.parent == 0
                || station.parent >= 10000
                || station.rooms.is_empty()
                || station.rooms.len() > 5
                || station.terminals.len() > 15
                || station.npcs.len() > 30
                || station.npcs.iter().any(|npc| {
                    npc.id == 0
                        || npc.avatar.len() != 241
                        || !station.rooms.iter().any(|room| room.index == npc.room)
                })
                || station
                    .rooms
                    .iter()
                    .any(|room| !room.fog_near.is_finite() || !room.fog_far.is_finite())
                || !self.mobs.is_empty()
                || !self.navpoints.is_empty()
                || !self.resources.is_empty()
            {
                return Err(invalid("invalid station layout"));
            }
        }
        let mut ids = std::collections::HashSet::new();
        for resource in &self.resources {
            if !(1..=9).contains(&resource.level)
                || !(1..=10).contains(&resource.resource_type)
                || resource.ore_items.len() > 1024
                || resource.ore_items.iter().any(|ore| {
                    ore.item_id == 0 || !ore.frequency.is_finite() || ore.frequency <= 0.
                })
            {
                return Err(invalid("invalid resource content definition"));
            }
        }
        for point in self
            .navpoints
            .iter()
            .chain(self.resources.iter().map(|node| &node.object))
        {
            if !ids.insert(point.id)
                || point.id == 0
                || point.name.len() > 120
                || !point.name.is_ascii()
                || !valid_position(point.position)
                || !point.scale.is_finite()
                // Signed scale is present in archived content (mirrored meshes).
                // Zero is normalized to 1 by the importer, as Object::SetScale did.
                || point.scale == 0.
                || !point.radius.is_finite()
                || !(0. ..=2_000_000.).contains(&point.radius)
                || point
                    .orientation
                    .iter()
                    .chain(&point.hsv)
                    .any(|v| !v.is_finite())
                || !point.signature.is_finite()
                || point.signature < 0.
                || point.navigation_type > 2
            {
                return Err(invalid("invalid or duplicate navigation object"));
            }
        }
        let mut mob_ids = std::collections::HashSet::new();
        for mob in &self.mobs {
            mob.validate()?;
            if !mob_ids.insert(mob.source_id) {
                return Err(invalid("Duplicate MOB source identity."));
            }
        }
        Ok(())
    }
}
fn valid_position(position: [f32; 3]) -> bool {
    position
        .iter()
        .all(|p| p.is_finite() && p.abs() <= 2_000_000.)
}
