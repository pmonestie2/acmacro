//! One authorized gate animation/acknowledgment, independent of process clocks.
use crate::content::Sector;
use serde::{Deserialize, Serialize};

/// SectorManager::Gate opens the model, then schedules camera state 4 at 5800ms.
pub const GATE_CAMERA_DELAY_MS: u64 = 5800;
pub const GATE_TIMEOUT_MS: u64 = 120_000;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct GateTransit {
    pub gate: u32,
    pub destination: u32,
    pub started_at_ms: u64,
    pub camera_sent: bool,
    /// Keep an early native completion instead of silently losing it. Its
    /// handoff still waits for the authorized opening interval to finish.
    pub completion_requested: bool,
}

impl GateTransit {
    pub fn valid_for(&self, sector: &Sector) -> bool {
        self.started_at_ms != 0
            && sector.station.is_none()
            && sector.navpoints.iter().any(|nav| {
                nav.id == self.gate
                    && nav.object_type == 11
                    && nav.gate_destination == Some(self.destination)
            })
    }
    pub fn camera_due(&self, now_ms: u64) -> bool {
        now_ms.saturating_sub(self.started_at_ms) >= GATE_CAMERA_DELAY_MS
    }
    pub fn expired(&self, now_ms: u64) -> bool {
        now_ms.saturating_sub(self.started_at_ms) > GATE_TIMEOUT_MS
    }
}
