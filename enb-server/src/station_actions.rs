//! Interior movement reports. They cannot overwrite a ship's space position or
//! move another avatar. Rendering/collision remain native-client responsibilities.
use super::*;

impl PlayerSession {
    pub(super) fn station_movement(&mut self, opcode: u16, body: &[u8]) -> io::Result<()> {
        let mut reader = Reader::new(body);
        let owner = reader.u32()?;
        let station = self
            .sector
            .station
            .as_ref()
            .filter(|_| self.logged_in)
            .ok_or_else(|| invalid("Enter a station before walking."))?;
        if owner != self.player_id() && owner != self.player_id() & 0xffffff {
            return Err(invalid("Station movement owner mismatch."));
        }
        let room = reader.u32()?;
        if room != u32::MAX && !station.rooms.iter().any(|entry| entry.index == room) {
            return Err(invalid("Unknown station room."));
        }
        match opcode {
            0x9f if body.len() == 12 => {
                reader.u32()?; // OldRoom is descriptive, never an authority.
                if room == u32::MAX
                    || self
                        .station_pose
                        .as_ref()
                        .is_some_and(|pose| pose.room != room)
                {
                    self.station_pose = None;
                }
                self.station_view_dirty = true;
            }
            0x9d if body.len() == 28 => {
                let orientation = reader.f32()?;
                let position = [reader.f32()?, reader.f32()?, reader.f32()?];
                let action = reader.u32()?;
                if position.iter().any(|value| value.abs() > 100_000.) {
                    return Err(invalid("Station avatar position exceeds supported bounds."));
                }
                self.station_pose = (room != u32::MAX).then_some(StationPose {
                    room,
                    orientation,
                    position,
                    action,
                });
                if action == 0x41 {
                    // Native clients request the room's avatars after entering
                    // or rebuilding the interior scene, including idle peers.
                    self.station_view_dirty = true;
                }
            }
            _ => return Err(invalid("Invalid station movement packet length.")),
        }
        Ok(())
    }
}
