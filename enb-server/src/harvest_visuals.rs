//! Shared mining/loot effects carry their origin so a sector handoff cannot
//! broadcast a final beam removal into the destination sector by mistake.
use super::*;

pub(crate) struct HarvestVisual {
    pub source: u32,
    pub sector: u32,
    pub position: [f32; 3],
    pub frame: Frame,
}

impl PlayerSession {
    pub(super) fn emit_harvest_visual(&mut self, frame: Frame) {
        self.pending.push(frame.clone());
        self.harvest_visuals.push(HarvestVisual {
            source: self.player_id(),
            sector: self.sector.id,
            position: self.position,
            frame,
        });
    }
    pub(crate) fn take_harvest_visuals(&mut self) -> Vec<HarvestVisual> {
        std::mem::take(&mut self.harvest_visuals)
    }
}
