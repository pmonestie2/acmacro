//! Write-ahead handoff journal. Its atomic rename is the transition commit:
//! location and transport mirrors may then be retried, but cannot disagree on
//! recovery. No handoff reaches the client until both mirrors are installed.
use super::*;
use std::io::Write;

const JOURNAL: &str = "sector-transition.local.json";

#[derive(Serialize, Deserialize)]
pub(super) struct TransitionRecord {
    version: u32,
    username: String,
    bind: Ipv4Addr,
    player: SavedPlayer,
    checkpoint: Option<session_checkpoint::Checkpoint>,
}

fn write_atomic(directory: &std::path::Path, name: &str, bytes: &[u8]) -> io::Result<()> {
    fs::create_dir_all(directory)?;
    let temporary = directory.join(format!("{name}.next"));
    let mut file = fs::File::create(&temporary)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    fs::rename(temporary, directory.join(name))
}

impl TransitionRecord {
    fn validate(
        &self,
        account: &Account,
        bind: Ipv4Addr,
        sectors: &SectorCatalog,
        initial_sector: &Sector,
    ) -> io::Result<()> {
        let destination = if self.player.sector == initial_sector.id {
            initial_sector.clone()
        } else {
            sectors.sector(self.player.sector)?
        };
        if self.version != 1
            || self.username != account.username
            || self.bind != bind
            || self.player.version != 1
            || !destination.bounds.contains(self.player.position)
            || sectors.sector(self.player.home_station)?.station.is_none()
            || self.checkpoint.as_ref().is_some_and(|checkpoint| {
                !checkpoint.validates_transition(account, bind, &self.player)
            })
        {
            return Err(invalid(
                "Invalid sector transition journal; existing state was not overwritten.",
            ));
        }
        Ok(())
    }

    fn materialize(&self, directory: &std::path::Path) -> io::Result<()> {
        // The journal stays present across failure at either write or removal.
        // Startup replays the same complete record before reading either file.
        write_atomic(
            directory,
            "player.json",
            &serde_json::to_vec_pretty(&self.player)?,
        )?;
        if let Some(checkpoint) = &self.checkpoint {
            write_atomic(
                directory,
                "session.local.json",
                &serde_json::to_vec(checkpoint)?,
            )?;
        } else {
            match fs::remove_file(directory.join("session.local.json")) {
                Ok(()) => {}
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => return Err(error),
            }
        }
        fs::remove_file(directory.join(JOURNAL))
    }
}

pub(super) fn recover(
    directory: &std::path::Path,
    account: &Account,
    bind: Ipv4Addr,
    sectors: &SectorCatalog,
    initial_sector: &Sector,
) -> io::Result<()> {
    let bytes = match fs::read(directory.join(JOURNAL)) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    let record: TransitionRecord = serde_json::from_slice(&bytes)?;
    record.validate(account, bind, sectors, initial_sector)?;
    record.materialize(directory)?;
    println!(
        "Sector transition recovered: {}; sector={}",
        account.character.name, record.player.sector
    );
    Ok(())
}

impl PlayerSession {
    pub(super) fn prepare_transition(
        &self,
        destination: &Sector,
        position: [f32; 3],
        join: &[u8],
    ) -> io::Result<TransitionRecord> {
        if self.state_dir.join(JOURNAL).try_exists()? {
            return Err(invalid(
                "An unresolved sector transition journal already exists.",
            ));
        }
        let record = TransitionRecord {
            version: 1,
            username: self.account.username.clone(),
            bind: self.bind,
            player: SavedPlayer {
                version: 1,
                sector: destination.id,
                position,
                home_station: self.home_station,
            },
            checkpoint: self.transition_checkpoint(destination, position, join)?,
        };
        // Use the actual resolved map, including a configured standalone map.
        if !destination.bounds.contains(position) {
            return Err(invalid("Invalid transition position."));
        }
        write_atomic(&self.state_dir, JOURNAL, &serde_json::to_vec(&record)?)?;
        Ok(record)
    }

    pub(super) fn finish_transition(&mut self) -> io::Result<()> {
        let Some(record) = &self.transition_pending else {
            return Ok(());
        };
        self.transition_retry = Instant::now();
        record.materialize(&self.state_dir)?;
        self.transition_pending = None;
        self.last_saved = Instant::now();
        self.last_checkpoint = Instant::now();
        self.dirty = false;
        if let Some(join) = &self.handoff {
            self.pending.push(Frame::new(0x3a, join.clone()));
        }
        println!(
            "Sector handoff published: {}; sector={}",
            self.account.character.name, self.sector.id
        );
        Ok(())
    }
}
