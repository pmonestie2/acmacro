//! Session-side developer operations reuse normal scene transitions and keep
//! orientation/ship-state access inside the character that owns those fields.
use super::*;

impl PlayerSession {
    pub(crate) fn developer_spawn_positions(&self, count: usize) -> io::Result<Vec<[f32; 3]>> {
        if !self.logged_in
            || self.sector.station.is_some()
            || self.handoff.is_some()
            || self.transition_pending.is_some()
            || self.warp.active()
            || self.gate_transit.is_some()
        {
            return Err(invalid(
                "Use /d_spawn in space outside warp and sector transitions.",
            ));
        }
        if !(1..=32).contains(&count) {
            return Err(invalid("Developer spawn count must be between 1 and 32."));
        }
        let mut positions = Vec::with_capacity(count);
        for index in 0..count {
            // A short row across the ship's local right axis keeps every actor
            // directly in front, with separated meshes for a multi-spawn.
            let lateral = (index as f32 - (count - 1) as f32 / 2.) * 150.;
            let offset = self.motion.rotate([1000., lateral, 0.]);
            let position = std::array::from_fn(|axis| self.position[axis] + offset[axis]);
            if !self.sector.bounds.contains(position) {
                return Err(invalid(
                    "Spawn formation would cross the sector boundary; move toward its center.",
                ));
            }
            positions.push(position);
        }
        Ok(positions)
    }

    pub(crate) fn developer_jump(&mut self, destination: u32) -> io::Result<()> {
        if !self.logged_in || self.handoff.is_some() || self.transition_pending.is_some() {
            return Err(invalid(
                "Enter the game and finish the current transition before /d_jump.",
            ));
        }
        // This is a dev transport, with no class/skill/energy/range requirement.
        // transfer_sector still protects pending weapon journals and production
        // escrow and commits the same recoverable handoff as a gate/wormhole.
        self.transfer_sector(destination, false)?;
        self.message(&format!(
            "Dev jump: {} ({}).",
            self.sector.name, self.sector.id
        ));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn spawn_formation_follows_ship_orientation_and_rejects_bad_scene_or_boundaries() {
        let mut ship = super::super::tests::session();
        ship.logged_in = true;
        ship.position = [0.; 3];
        assert_eq!(
            ship.developer_spawn_positions(1).unwrap(),
            [[1000., 0., 0.]]
        );
        ship.motion.face([0., 1., 0.]).unwrap();
        let points = ship.developer_spawn_positions(3).unwrap();
        assert!((points[1][0]).abs() < 0.001);
        assert!((points[1][1] - 1000.).abs() < 0.001);
        assert!((distance(points[0], points[1]) - 150.).abs() < 0.001);
        assert!(ship.developer_spawn_positions(0).is_err());
        assert!(ship.developer_spawn_positions(33).is_err());
        ship.position = ship.sector.bounds.max;
        assert!(ship.developer_spawn_positions(1).is_err());
        ship.logged_in = false;
        assert!(ship.developer_spawn_positions(1).is_err());
    }

    #[test]
    fn developer_jump_uses_durable_handoff_without_energy_or_class_requirements() {
        let mut ship = super::super::tests::journal_ship();
        ship.energy = 0.;
        ship.hull = 0.;
        ship.developer_jump(3605).unwrap();
        assert_eq!(ship.sector.id, 3605);
        assert!(!ship.logged_in);
        assert!(ship.pending.iter().any(|frame| frame.opcode == 0x3a));
        let saved: serde_json::Value =
            serde_json::from_slice(&fs::read(ship.state_dir.join("player.json")).unwrap()).unwrap();
        assert_eq!(saved["sector"], 3605);
        assert_eq!(ship.energy, 0.);
        assert_eq!(ship.hull, 0.);
        assert!(ship.developer_jump(1076).is_err());
    }
}
