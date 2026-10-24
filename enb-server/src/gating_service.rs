//! Gate lifecycle: validate once, open the native model, delay the camera,
//! retain the client's completion, then use the ordinary sector handoff.
use super::*;
use crate::gating::GateTransit;
use crate::resources::unix_ms;

impl PlayerSession {
    pub(super) fn begin_gating(&mut self, target: u32) -> io::Result<()> {
        if !self.logged_in || self.hull <= 0. || self.sector.station.is_some() || self.warp.active()
        {
            return Err(invalid("Gate alive in space after leaving warp."));
        }
        if let Some(active) = &self.gate_transit {
            return if active.gate == target {
                Ok(())
            } else {
                Err(invalid("Another gate transition is in progress."))
            };
        }
        if self.target != target {
            return Err(invalid("Select this sector gate first."));
        }
        let gate = self
            .sector
            .navpoints
            .iter()
            .find(|nav| nav.id == target && nav.object_type == 11)
            .ok_or_else(|| invalid("Select a sector gate first."))?;
        if distance(self.position, gate.position) > 2500. {
            return Err(invalid("Move within 2500m of the gate first."));
        }
        let destination = gate
            .gate_destination
            .ok_or_else(|| invalid("Gate destination is unavailable."))?;
        if destination != self.sector.id && self.sectors.sector(destination)?.station.is_some() {
            return Err(invalid(
                "A sector gate cannot lead into a station interior.",
            ));
        }
        if self.skills.cast.is_some() {
            return Err(invalid("Finish the current ability before gating."));
        }
        self.cancel_mining("Gate activated.")?;
        self.cancel_looting("Gate activated.")?;
        self.detach_formation();
        self.stop_following();
        self.motion.stop();
        self.stop_weapons();
        self.gate_transit = Some(GateTransit {
            gate: target,
            destination,
            started_at_ms: unix_ms(),
            camera_sent: false,
            completion_requested: false,
        });
        self.pending.push(packets::gate_render_state(target, 1));
        self.refresh_target_verbs(true);
        println!(
            "Gate opening: {}; gate={target}; destination={destination}",
            self.account.character.name
        );
        Ok(())
    }

    pub(super) fn complete_gating(&mut self, target: u32) -> io::Result<()> {
        let active = self
            .gate_transit
            .as_mut()
            .ok_or_else(|| invalid("No gate transition is authorized."))?;
        // Native completion can omit the now-hidden target. It can never name
        // a different object or substitute a destination for this transaction.
        if target != active.gate && target != 0 && target != u32::MAX {
            return Err(invalid("Gate completion target mismatch."));
        }
        active.completion_requested = true;
        println!(
            "Gate completion received: {}; gate={}; camera_sent={}; elapsed_ms={}",
            self.account.character.name,
            active.gate,
            active.camera_sent,
            unix_ms().saturating_sub(active.started_at_ms)
        );
        self.advance_gating()
    }

    pub(super) fn advance_gating(&mut self) -> io::Result<()> {
        let Some(active) = self.gate_transit.clone() else {
            return Ok(());
        };
        let now = unix_ms();
        if self.hull <= 0.
            || self.warp.active()
            || active.expired(now)
            || !active.valid_for(&self.sector)
        {
            self.gate_transit = None;
            self.pending
                .push(packets::gate_render_state(active.gate, 3));
            self.pending.push(packets::gate_camera(0, self.player_id()));
            self.pending.push(packets::message(
                "Gate transition cancelled; activate the gate again after recovering.",
            ));
            self.refresh_target_verbs(true);
            return Ok(());
        }
        if active.camera_due(now) {
            if active.completion_requested {
                // The sector path checkpoints JoinData; a normal short
                // replacement resends that handoff without another activation.
                return self.transfer_sector(active.destination, true);
            }
            if !active.camera_sent {
                self.pending.push(packets::gate_camera(4, active.gate));
                self.gate_transit.as_mut().unwrap().camera_sent = true;
                println!(
                    "Gate camera started: {}; gate={}",
                    self.account.character.name, active.gate
                );
            }
        }
        Ok(())
    }
}
