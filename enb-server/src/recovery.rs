//! Native incap rescue dialog, towing, and a local command fallback. Neither
//! selecting a dialog option nor relogging should strand an incapacitated ship.
use super::*;

impl PlayerSession {
    pub(super) fn incap_request(&mut self, body: &[u8]) -> io::Result<()> {
        if body.len() != 4 {
            return Err(invalid("Invalid incap request length."));
        }
        self.recovery_owner(Reader::new(body).u32()?)?;
        if !self.logged_in || self.sector.station.is_some() || self.hull > 0. {
            return Err(invalid("The ship is not incapacitated in space."));
        }
        let mut dialog = b"Rescue control: do you need assistance?\0\0\x03".to_vec();
        for (choice, label) in [
            (0u32, "I need a tow"),
            (1, "Send distress call"),
            (2, "Wait for jumpstart"),
        ] {
            dialog.extend_from_slice(&choice.to_le_bytes());
            dialog.extend_from_slice(label.as_bytes());
            dialog.push(0);
        }
        self.pending.push(Frame::new(0x54, dialog));
        Ok(())
    }
    pub(super) fn rescue_choice(&mut self, body: &[u8]) -> io::Result<()> {
        if body.len() != 5 {
            return Err(invalid("Invalid rescue selection length."));
        }
        let mut reader = Reader::new(body);
        self.recovery_owner(reader.u32()?)?;
        if self.hull > 0. {
            return Err(invalid("No rescue dialog is active."));
        }
        match reader.take(1)?[0] {
            0 => self.tow_to_station(),
            choice @ (1 | 2 | 230) => {
                self.pending
                    .push(Frame::new(0x56, (-32i32).to_le_bytes().to_vec()));
                if choice == 1 {
                    self.client_events.push(ClientEvent::Chat {
                        channel: 4,
                        text: "Distress call: my ship needs a jumpstart.".into(),
                    });
                }
                Ok(())
            }
            _ => Err(invalid("Unknown rescue choice.")),
        }
    }
    fn recovery_owner(&self, owner: u32) -> io::Result<()> {
        if owner == self.player_id() || owner == self.player_id() & 0xffffff {
            Ok(())
        } else {
            Err(invalid("Recovery request owner mismatch."))
        }
    }
    pub(crate) fn tow_to_station(&mut self) -> io::Result<()> {
        if !self.logged_in || self.sector.station.is_some() || self.hull > 0. {
            return Err(invalid("Tow is available to incapacitated ships in space."));
        }
        let destination = self.sectors.sector(self.home_station)?;
        if destination.station.is_none() {
            return Err(invalid("Registered station is unavailable."));
        }
        self.enter_station(destination.id)?;
        self.pending
            .insert(0, Frame::new(0x56, (-32i32).to_le_bytes().to_vec()));
        Ok(())
    }
    pub(crate) fn self_jumpstart(&mut self) -> io::Result<()> {
        self.target = self.player_id();
        self.begin_ability(65)
    }
}
