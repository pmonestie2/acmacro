//! Shared server coordinator. Sessions own authentication, movement and wire
//! sequence state; sectors own one combat simulation. All mutation runs under
//! the server's existing mutex, and socket I/O remains outside that mutex.
use crate::accounts::AccountDirectory;
use crate::content::Sector;
use crate::events::ClientEvent;
use crate::groups::GroupManager;
use crate::loot_world::{lock_loot, LootWorld};
use crate::packets;
use crate::peers::PeerSnapshot;
use crate::player_session::{Outbound, PlayerSession};
use crate::protocol::{invalid, Datagram, Frame, Reader};
use crate::resources::{lock_resources, ResourceField};
use crate::sector_combat::{lock_combat, SectorCombat};
use crate::skills::SupportAction;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::io;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[path = "game_actions.rs"]
mod actions;
#[path = "dev_commands.rs"]
mod dev_commands;
#[cfg(test)]
#[path = "game_tests.rs"]
mod tests;

pub struct GameState {
    sessions: BTreeMap<u32, PlayerSession>,
    combats: HashMap<u32, Arc<Mutex<SectorCombat>>>,
    resource_fields: HashMap<u32, Arc<Mutex<ResourceField>>>,
    loot_worlds: HashMap<u32, Arc<Mutex<LootWorld>>>,
    groups: GroupManager,
    registrations: HashMap<IpAddr, Instant>,
    outgoing: Vec<Outbound>,
    root: PathBuf,
    bind: Ipv4Addr,
    initial_sector: Sector,
    connected: HashSet<u32>,
    groups_dirty: bool,
    last_deferred_error: Option<Instant>,
    player_sectors: HashMap<u32, u32>,
}

impl GameState {
    pub fn new(sector: Sector, bind: Ipv4Addr, root: PathBuf) -> io::Result<Self> {
        let mut state = Self {
            sessions: BTreeMap::new(),
            combats: HashMap::new(),
            resource_fields: HashMap::new(),
            loot_worlds: HashMap::new(),
            groups: GroupManager::default(),
            registrations: HashMap::new(),
            outgoing: Vec::new(),
            root,
            bind,
            initial_sector: sector,
            connected: HashSet::new(),
            groups_dirty: true,
            last_deferred_error: None,
            player_sectors: HashMap::new(),
        };
        state.load_accounts()?;
        let clock = state
            .sessions
            .values()
            .filter(|session| session.connected())
            .map(PlayerSession::tick_ms)
            .max()
            .unwrap_or(0);
        for session in state.sessions.values_mut() {
            session.synchronize_clock(clock);
        }
        state.connected = state
            .sessions
            .iter()
            .filter(|(_, session)| session.connected())
            .map(|(id, _)| *id)
            .collect();
        let path = state.root.join("groups.local.json");
        if path.exists() {
            state.groups = serde_json::from_slice(&std::fs::read(path)?)?;
        }
        state.groups.restore_index(&state.connected)?;
        // Prefer a resumed simulation over a fresh offline account's content.
        let mut ids: Vec<u32> = state.sessions.keys().copied().collect();
        ids.sort_by_key(|id| !state.sessions[id].logged_in);
        for id in ids {
            state.attach_session(id)?;
        }
        // A restart can occur before a departing player's cleanup tick. Clear
        // those account identities from each loaded sector's saved hate table.
        for (&sector, combat) in &state.combats {
            let mut world = lock_combat(combat)?;
            for (id, session) in &state.sessions {
                if !session.logged_in || session.sector.id != sector {
                    for mob in &mut world.actors {
                        mob.remove_player_hate(*id);
                    }
                }
            }
        }
        state.player_sectors = state.active_sector_membership();
        Ok(state)
    }

    fn load_accounts(&mut self) -> io::Result<()> {
        for stored in AccountDirectory::new(&self.root).load()? {
            let id = stored.account.character.game_id;
            if let Some(session) = self.sessions.get(&id) {
                if session.account.username != stored.account.username {
                    return Err(invalid(
                        "Account identity changed; restart with matching account files.",
                    ));
                }
                continue;
            }
            if self.sessions.len() >= 32 {
                return Err(invalid("Local server supports up to 32 accounts."));
            }
            let mut session = PlayerSession::with_account(
                self.initial_sector.clone(),
                self.bind,
                stored.directory,
                stored.account,
                self.root.clone(),
            )?;
            let clock = self
                .sessions
                .values()
                .map(PlayerSession::tick_ms)
                .max()
                .unwrap_or(0);
            if !session.connected() {
                session.synchronize_clock(clock);
            }
            for (&peer, at) in &self.registrations {
                if at.elapsed() < Duration::from_secs(600) {
                    session.register_peer(peer);
                }
            }
            for field in self.resource_fields.values() {
                session.register_resource_receipts(&mut *lock_resources(field)?)?;
            }
            for world in self.loot_worlds.values() {
                session.register_loot_receipts(&mut *lock_loot(world)?)?;
            }
            self.sessions.insert(id, session);
        }
        Ok(())
    }
    fn attach_session(&mut self, id: u32) -> io::Result<()> {
        let sector = self
            .sessions
            .get(&id)
            .ok_or_else(|| invalid("Unknown player."))?
            .sector
            .id;
        if !self.resource_fields.contains_key(&sector) {
            let items = crate::items::ItemCatalog::load()?;
            let mut field = ResourceField::load(
                &self.sessions[&id].sector,
                &items,
                self.root.join(format!("resources/{sector}.json")),
            )?;
            for session in self.sessions.values() {
                session.register_resource_receipts(&mut field)?;
            }
            self.resource_fields
                .insert(sector, Arc::new(Mutex::new(field)));
        }
        let resources = Arc::clone(&self.resource_fields[&sector]);
        if sector < 10000 && !self.loot_worlds.contains_key(&sector) {
            let mut world = LootWorld::load(
                self.root.join(format!("loot/{sector}.json")),
                sector,
                &crate::items::ItemCatalog::load()?,
            )?;
            for session in self.sessions.values() {
                session.register_loot_receipts(&mut world)?;
            }
            self.loot_worlds.insert(sector, Arc::new(Mutex::new(world)));
        }
        let loot = self.loot_worlds.get(&sector).map(Arc::clone);
        let session = self
            .sessions
            .get_mut(&id)
            .ok_or_else(|| invalid("Unknown player."))?;
        let shared = if let Some(shared) = self.combats.get(&sector) {
            Arc::clone(shared)
        } else {
            let initial = lock_combat(&session.combat)?;
            let world = SectorCombat::persistent(
                self.root.join(format!("combat/{sector}.json")),
                sector,
                &initial,
            )?;
            let shared = Arc::new(Mutex::new(world));
            self.combats.insert(sector, Arc::clone(&shared));
            shared
        };
        session.attach_resource_field(resources)?;
        let changed = !Arc::ptr_eq(&session.combat, &shared);
        session.attach_combat(shared)?;
        if let Err(error) = if changed {
            session.recover_weapon_launch()
        } else {
            Ok(())
        } {
            eprintln!(
                "Weapon journal recovery deferred: {}; {error}",
                session.account.character.name
            );
        }
        if let Some(loot) = loot {
            session.attach_loot_world(loot)?;
        }
        Ok(())
    }
    pub fn account_count(&self) -> usize {
        self.sessions.len()
    }
    pub fn has_player(&self, id: u32) -> bool {
        self.sessions.contains_key(&id)
    }
    pub fn selected_from(&self, id: u32, peer: IpAddr) -> bool {
        self.sessions
            .get(&id)
            .is_some_and(|session| session.selected_from(peer))
    }
    pub fn issue_ticket(
        &mut self,
        username: &str,
        password: &str,
        peer: IpAddr,
    ) -> io::Result<String> {
        if !self
            .sessions
            .values()
            .any(|session| session.account.username == username)
        {
            self.load_accounts()?;
        }
        self.sessions
            .values_mut()
            .find(|session| session.account.username == username)
            .ok_or_else(|| invalid("invalid local sandbox credentials"))?
            .issue_ticket(username, password, peer)
    }
    pub fn consume_ticket(&mut self, ticket: &str, peer: IpAddr) -> Option<u32> {
        self.sessions
            .iter_mut()
            .find_map(|(id, session)| session.consume_ticket(ticket, peer).then_some(*id))
    }
    pub fn authentication_account(
        &mut self,
        username: &str,
    ) -> io::Result<crate::account::Account> {
        if !self
            .sessions
            .values()
            .any(|session| session.account.username == username)
        {
            self.load_accounts()?;
        }
        self.sessions
            .values()
            .find(|session| session.account.username == username)
            .map(|session| session.account.clone())
            .ok_or_else(|| invalid("invalid local sandbox credentials"))
    }
    pub fn issue_verified_ticket(&mut self, id: u32, peer: IpAddr) -> io::Result<String> {
        self.sessions
            .get_mut(&id)
            .ok_or_else(|| invalid("Unknown account identity."))?
            .issue_verified_ticket(peer)
    }
    pub fn characters(&self, id: u32) -> io::Result<Frame> {
        let session = self
            .sessions
            .get(&id)
            .ok_or_else(|| invalid("Unknown account character."))?;
        Ok(packets::characters(
            &session.sector,
            &session.account.character,
        ))
    }
    pub fn select(
        &mut self,
        id: u32,
        slot: u32,
        peer: IpAddr,
        via_proxy: bool,
    ) -> io::Result<Frame> {
        self.sessions
            .get_mut(&id)
            .ok_or_else(|| invalid("Unknown account character."))?
            .select(slot, peer, via_proxy)
    }
    pub fn handle_input(&mut self, id: u32, opcode: u16, body: &[u8]) -> io::Result<()> {
        self.sessions
            .get_mut(&id)
            .ok_or_else(|| invalid("Unknown player."))?
            .handle_input(opcode, body)
    }
    pub fn handle_udp(&mut self, port: u16, peer: SocketAddr, packet: Datagram) -> io::Result<()> {
        if port == 3806 && packet.opcode == 0x1000 {
            if packet.body.len() != 4 {
                return Err(invalid("registration requires a version"));
            }
            let version = Reader::new(&packet.body).u32()?;
            if version >= 146 {
                if self.registrations.len() >= 64 && !self.registrations.contains_key(&peer.ip()) {
                    return Err(invalid("registration capacity reached"));
                }
                self.registrations.insert(peer.ip(), Instant::now());
                for session in self.sessions.values_mut() {
                    session.register_peer(peer.ip());
                }
            }
            self.outgoing.push(Outbound {
                port,
                peer,
                data: Datagram {
                    opcode: if version >= 146 { 0x1001 } else { 0x1009 },
                    player: 0,
                    sequence: 0,
                    body: if version >= 146 {
                        vec![0; 8]
                    } else {
                        246u32.to_le_bytes().to_vec()
                    },
                }
                .encode()?,
            });
            return Ok(());
        }
        self.sessions
            .get_mut(&packet.player)
            .ok_or_else(|| invalid("Unknown UDP player identity."))?
            .handle_udp(port, peer, packet)
    }

    pub fn advance(&mut self, dt: f32) -> io::Result<()> {
        self.advance_at(dt, crate::resources::unix_ms())
    }

    /// Explicit combat wall clock for deterministic flight-deadline tests.
    pub fn advance_at(&mut self, dt: f32, now_ms: u64) -> io::Result<()> {
        if !dt.is_finite() || dt < 0. {
            return Err(invalid("Invalid simulation duration."));
        }
        let mut first_error = None;
        for field in self.resource_fields.values() {
            lock_resources(field)?.advance()?;
        }
        let ids: Vec<u32> = self.sessions.keys().copied().collect();
        for id in &ids {
            let events = self.sessions.get_mut(id).unwrap().take_events();
            for event in events {
                if let Err(error) = self.handle_event(*id, event) {
                    self.sessions
                        .get_mut(id)
                        .unwrap()
                        .message(&error.to_string());
                }
            }
        }
        self.update_views();
        for session in self.sessions.values_mut() {
            if let Err(error) = session.advance_player(dt) {
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
        }
        self.advance_formations();
        for id in &ids {
            self.attach_session(*id)?;
        }
        self.deliver_support()?;
        for id in &ids {
            self.attach_session(*id)?;
        }
        self.synchronize_sector_hate()?;
        self.expire_group_cloak()?;
        let mut hits = Vec::new();
        for (&sector, combat) in &self.combats {
            if let Some(world) = self.loot_worlds.get(&sector) {
                lock_loot(world)?
                    .capture_deaths(&*lock_combat(combat)?, crate::resources::unix_ms())?;
            }
            let threats: Vec<_> = self
                .sessions
                .values()
                .filter(|session| session.sector.id == sector && session.logged_in)
                .map(PlayerSession::threat)
                .collect();
            match lock_combat(combat)?.advance_at(dt, &threats, now_ms) {
                Ok(resolved) => hits.extend(resolved),
                Err(error) => {
                    if first_error.is_none() {
                        first_error = Some(error);
                    }
                }
            }
        }
        for hit in hits {
            let reflection = match self.sessions.get_mut(&hit.target) {
                Some(session) => session.receive_mob_hit(&hit),
                None => Ok(0.),
            };
            let acknowledged = reflection.and_then(|reflection| {
                let combat = self
                    .combats
                    .get(&hit.sector)
                    .ok_or_else(|| invalid("NPC impact sector is no longer hosted."))?;
                lock_combat(combat)?.acknowledge_mob_hit(hit.sequence, reflection)
            });
            match acknowledged {
                Ok(()) => {
                    if let Some(session) = self.sessions.get_mut(&hit.target) {
                        session.forget_mob_hit_receipt(hit.sector, hit.sequence);
                    }
                }
                Err(error) => {
                    if first_error.is_none() {
                        first_error = Some(error);
                    }
                }
            }
        }
        for (&sector, combat) in &self.combats {
            if let Some(world) = self.loot_worlds.get(&sector) {
                lock_loot(world)?
                    .capture_deaths(&*lock_combat(combat)?, crate::resources::unix_ms())?;
            }
        }
        let connected: HashSet<u32> = self
            .sessions
            .iter()
            .filter(|(_, session)| session.connected())
            .map(|(id, _)| *id)
            .collect();
        for id in self.connected.difference(&connected) {
            self.groups.leave(*id);
            self.groups_dirty = true;
        }
        self.connected = connected;
        self.update_views();
        self.expire_group_cloak()?;
        if self.groups_dirty {
            self.publish_groups();
            self.save_groups()?;
            self.groups_dirty = false;
        }
        let mut damage_reports = Vec::new();
        let mut projectile_visuals = Vec::new();
        let mut mob_launches = Vec::new();
        for (&sector, combat) in &self.combats {
            mob_launches.extend(lock_combat(combat)?.take_mob_launch_visuals());
            for report in lock_combat(combat)?.take_damage_reports() {
                damage_reports.push((sector, report));
            }
            for visual in lock_combat(combat)?.take_projectile_visuals() {
                projectile_visuals.push((sector, visual));
            }
        }
        let peers = self.snapshots();
        let mut station_rooms: HashMap<(u32, u32), Vec<crate::peers::StationPeerSnapshot>> =
            HashMap::new();
        for avatar in self
            .sessions
            .values()
            .filter_map(PlayerSession::station_snapshot)
        {
            station_rooms
                .entry((avatar.station, avatar.pose.room))
                .or_default()
                .push(avatar);
        }
        let effects: Vec<_> = self
            .sessions
            .iter()
            .filter(|(_, session)| session.logged_in)
            .map(|(id, session)| (*id, session.sector.id, session.public_effects()))
            .collect();
        let harvest_visuals: Vec<_> = self
            .sessions
            .values_mut()
            .flat_map(PlayerSession::take_harvest_visuals)
            .collect();
        for (id, session) in self.sessions.iter_mut() {
            for (sector, visual) in &projectile_visuals {
                if session.sector.id == *sector {
                    session.publish_projectile_visual(visual);
                }
            }
            for (sector, report) in &damage_reports {
                if session.sector.id == *sector {
                    session.publish_weapon_damage(report);
                }
            }
            session.publish_mobs()?;
            // Publish newly visible NPCs before their first launch effect.
            // Player shot visuals above still precede target-death removals.
            for visual in &mob_launches {
                session.publish_mob_launch(visual);
            }
            session.publish_resources()?;
            session.publish_corpses()?;
            session.publish_peers(&peers);
            let avatars = session
                .station_location()
                .and_then(|room| station_rooms.get(&room));
            session.publish_station_peers(avatars.map_or(&[], Vec::as_slice));
            for visual in &harvest_visuals {
                if *id != visual.source
                    && session.logged_in
                    && session.sector.id == visual.sector
                    && crate::mob::distance(session.position, visual.position) <= 20_000.
                {
                    session.queue([visual.frame.clone()]);
                }
            }
            for (source, sector, frames) in &effects {
                if *source != *id && session.logged_in && session.sector.id == *sector {
                    session.queue(frames.iter().cloned());
                }
            }
            if let Err(error) = session.finish_tick() {
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
        }
        if let Some(error) = first_error {
            if self
                .last_deferred_error
                .is_none_or(|last| last.elapsed() >= Duration::from_secs(5))
            {
                eprintln!("Gameplay work deferred; independent sessions remain active: {error}");
                self.last_deferred_error = Some(Instant::now());
            }
        } else {
            self.last_deferred_error = None;
        }
        Ok(())
    }
    fn active_sector_membership(&self) -> HashMap<u32, u32> {
        self.sessions
            .iter()
            .filter(|(_, session)| session.logged_in)
            .map(|(id, session)| (*id, session.sector.id))
            .collect()
    }
    fn synchronize_sector_hate(&mut self) -> io::Result<()> {
        let current = self.active_sector_membership();
        for (player, sector) in &self.player_sectors {
            if current.get(player) != Some(sector) {
                if let Some(combat) = self.combats.get(sector) {
                    for mob in &mut lock_combat(combat)?.actors {
                        mob.remove_player_hate(*player);
                    }
                }
            }
        }
        self.player_sectors = current;
        Ok(())
    }

    fn snapshots(&self) -> Vec<PeerSnapshot> {
        self.sessions
            .values()
            .filter_map(PlayerSession::peer_snapshot)
            .collect()
    }
    fn update_views(&mut self) {
        let peers = self.snapshots();
        for (id, session) in self.sessions.iter_mut() {
            session.update_peer_view(&peers, &self.groups.members(*id));
        }
    }
    pub fn take_outgoing(&mut self) -> Vec<Outbound> {
        let mut packets = std::mem::take(&mut self.outgoing);
        for session in self.sessions.values_mut() {
            packets.extend(session.take_outgoing());
        }
        packets
    }
    pub fn save(&mut self) -> io::Result<()> {
        let mut first_error = None;
        for combat in self.combats.values() {
            if let Err(error) = lock_combat(combat).and_then(|mut world| world.persist()) {
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
        }
        for session in self.sessions.values_mut() {
            if let Err(error) = session.save() {
                eprintln!(
                    "Character save failed: {}; {error}",
                    session.account.character.name
                );
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
            if let Err(error) = session.checkpoint_session(true) {
                eprintln!(
                    "Character checkpoint failed: {}; {error}",
                    session.account.character.name
                );
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
        }
        if let Err(error) = self.save_groups() {
            if first_error.is_none() {
                first_error = Some(error);
            }
        }
        match first_error {
            Some(error) => Err(error),
            None => Ok(()),
        }
    }
    pub fn checkpoint_session(&mut self, force: bool) -> io::Result<()> {
        let mut first_error = None;
        if force {
            for combat in self.combats.values() {
                if let Err(error) = lock_combat(combat).and_then(|mut world| world.persist()) {
                    if first_error.is_none() {
                        first_error = Some(error);
                    }
                }
            }
        }
        for session in self.sessions.values_mut() {
            if let Err(error) = session.checkpoint_session(force) {
                eprintln!(
                    "Character checkpoint failed: {}; {error}",
                    session.account.character.name
                );
                if first_error.is_none() {
                    first_error = Some(error);
                }
            }
        }
        match first_error {
            Some(error) => Err(error),
            None => Ok(()),
        }
    }
}
