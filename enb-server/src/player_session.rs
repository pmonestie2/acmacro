//! One authenticated character session: transport identity, movement, skills and
//! per-client visibility. GameState owns shared sectors and multiplayer rules.
use crate::account::Account;
#[cfg(test)]
use crate::account::FIRST_PLAYER_ID;
use crate::content::Sector;
use crate::equipment_stats::EquipmentStats;
use crate::events::ClientEvent;
use crate::inventory::{Inventory, ItemTransfer};
use crate::items::{ItemCatalog, ItemTemplate};
use crate::loot_world::{lock_loot, LootWorld};
use crate::mob::{distance, Mob, MobState};
use crate::motion::Motion;
use crate::npcs::NpcCatalog;
use crate::packets;
use crate::peers::StationPose;
use crate::protocol::SECTOR_PORT;
use crate::protocol::{invalid, split_frames, Datagram, Frame, Reader};
use crate::resources::{lock_resources, ResourceField};
use crate::sector_combat::{lock_combat, PlayerThreat, SectorCombat, MOB_ID};
use crate::sectors::SectorCatalog;
use crate::skills::{GroupContact, SkillCatalog, SkillState, SupportAction};
use crate::warp::{FinishReason, Warp, WarpEvent, WarpPhase, Waypoint};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::collections::{HashMap, VecDeque};
use std::fs;
use std::io;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
#[path = "ability_actions.rs"]
mod ability_actions;
#[path = "dev_session.rs"]
mod dev_session;
#[path = "follow_service.rs"]
mod follow_service;
#[path = "gating_service.rs"]
mod gating_service;
#[path = "harvest_visuals.rs"]
mod harvest_visuals;
#[path = "looting.rs"]
mod looting;
#[path = "mining.rs"]
mod mining;
#[path = "mob_damage.rs"]
mod mob_damage;
#[path = "native_jumpstart.rs"]
mod native_jumpstart;
#[path = "npc_services.rs"]
mod npc_services;
#[path = "production_service.rs"]
mod production_service;
#[path = "recovery.rs"]
mod recovery;
#[path = "reputation_service.rs"]
mod reputation_service;
#[path = "sector_transfer.rs"]
mod sector_transfer;
#[path = "sector_transition.rs"]
mod sector_transition;
#[path = "session_checkpoint.rs"]
mod session_checkpoint;
#[path = "session_equipment.rs"]
mod session_equipment;
#[path = "session_peers.rs"]
mod session_peers;
#[path = "session_resources.rs"]
mod session_resources;
#[path = "session_tick.rs"]
mod session_tick;
#[path = "station_actions.rs"]
mod station_actions;
#[path = "station_peers.rs"]
mod station_peers;
#[cfg(test)]
#[path = "session_takeover_tests.rs"]
mod takeover_tests;
#[path = "target_verbs.rs"]
mod target_verbs;
#[path = "weapon_launch.rs"]
mod weapon_launch;
use std::time::{Duration, Instant};

#[derive(Serialize, Deserialize)]
struct SavedPlayer {
    version: u32,
    sector: u32,
    position: [f32; 3],
    #[serde(default = "default_home_station")]
    home_station: u32,
}
fn default_home_station() -> u32 {
    10761
}

pub struct Outbound {
    pub port: u16,
    pub peer: SocketAddr,
    pub data: Vec<u8>,
}

struct Ticket {
    peer: IpAddr,
    expires: Instant,
}

pub struct PlayerSession {
    pub account: Account,
    sectors: SectorCatalog,
    items: ItemCatalog,
    npcs: NpcCatalog,
    factions: crate::factions::FactionCatalog,
    reputation: Arc<crate::factions::FactionReactions>,
    inventory: Inventory,
    workbench: Option<production_service::WorkbenchWindow>,
    session_epoch: u64,
    workbench_sync: f32,
    workbench_lab_ready: bool,
    equipment: EquipmentStats,
    resource_field: Arc<Mutex<ResourceField>>,
    loot_world: Arc<Mutex<LootWorld>>,
    loot_cast: Option<looting::LootCast>,
    loot_window: Option<u32>,
    visible_corpses: HashMap<u32, [f32; 3]>,
    loot_revision: Option<(u32, Vec<u32>)>,
    last_loot_publish: Option<Instant>,
    world_root: PathBuf,
    mining: Option<mining::MiningCast>,
    harvest_visuals: Vec<harvest_visuals::HarvestVisual>,
    resource_window: Option<u32>,
    resource_revision: Option<(u32, u64, Vec<u32>)>,
    pub sector: Sector,
    pub bind: Ipv4Addr,
    pub position: [f32; 3],
    pub logged_in: bool,
    home_station: u32,
    motion: Motion,
    selected_peer: Option<IpAddr>,
    pub endpoint: Option<SocketAddr>,
    comms: Option<SocketAddr>,
    nonce: [u8; 20],
    tickets: HashMap<String, Ticket>,
    registrations: HashMap<IpAddr, Instant>,
    sequence: u32,
    replay: VecDeque<Datagram>,
    inputs: VecDeque<u32>,
    pending: Vec<Frame>,
    outgoing: Vec<Outbound>,
    warp: Warp,
    warp_effects: Vec<u32>,
    next_effect: u32,
    last_motion_report: Option<Instant>,
    last_launch: u64,
    mob_damage_receipts: Vec<mob_damage::MobDamageReceipt>,
    following: bool,
    follow_peer: Option<crate::following::FollowContact>,
    target: u32,
    last_verbs: Option<Frame>,
    gate_transit: Option<crate::gating::GateTransit>,
    docking_target: Option<u32>,
    handoff: Option<Vec<u8>>,
    transition_pending: Option<sector_transition::TransitionRecord>,
    transition_retry: Instant,
    station_pose: Option<StationPose>,
    station_peers: HashMap<u32, StationPose>,
    station_view_dirty: bool,
    last_station_broadcast: Instant,
    npc_interaction: Option<npc_services::NpcInteraction>,
    pub(crate) combat: Arc<Mutex<SectorCombat>>,
    mob_states: Vec<MobState>,
    mob_visible: Vec<bool>,
    mob_relationships: Vec<Option<u32>>,
    player_firing: bool,
    weapon_cooldowns: [f32; 20],
    weapon_firing: [bool; 20],
    shield: f32,
    hull: f32,
    energy: f32,
    skill_catalog: SkillCatalog,
    skills: SkillState,
    group_contacts: Vec<GroupContact>,
    support_actions: Vec<SupportAction>,
    resource_sync: f32,
    last_seen: Instant,
    last_broadcast: Instant,
    last_saved: Instant,
    pub state_dir: PathBuf,
    dirty: bool,
    started: Instant,
    tick_offset: u32,
    last_checkpoint: Instant,
    last_ack_request: Instant,
    peer_ids: HashSet<u32>,
    peer_contacts: HashMap<u32, crate::peers::PeerContact>,
    visible_peers: HashSet<u32>,
    peer_stats: HashMap<u32, crate::equipment_stats::ShipStats>,
    visible_resources: HashSet<u32>,
    resource_fractions: HashMap<u32, f32>,
    last_resource_visibility: Option<Instant>,
    last_peer_broadcast: Instant,
    client_events: Vec<ClientEvent>,
    formation_leader: Option<u32>,
    last_formation_broadcast: Instant,
}

impl PlayerSession {
    pub fn new(sector: Sector, bind: Ipv4Addr, state_dir: PathBuf) -> io::Result<Self> {
        let account = Account::load(&state_dir)?;
        Self::with_account(sector, bind, state_dir.clone(), account, state_dir)
    }
    pub(crate) fn with_account(
        mut sector: Sector,
        bind: Ipv4Addr,
        state_dir: PathBuf,
        account: Account,
        world_root: PathBuf,
    ) -> io::Result<Self> {
        sector.validate()?;
        let sectors = SectorCatalog::load()?;
        sector_transition::recover(&state_dir, &account, bind, &sectors, &sector)?;
        let items = ItemCatalog::load()?;
        let npcs = NpcCatalog::load()?;
        let inventory_path = state_dir.join("inventory.json");
        let inventory = if inventory_path.exists() {
            serde_json::from_slice(&fs::read(inventory_path)?)?
        } else {
            Inventory::starter_for(&account.character, &items)?
        };
        let skill_catalog = SkillCatalog::for_profile(&account.character)?;
        let granted = crate::sandbox_loadout::provision(
            &world_root,
            &state_dir,
            &inventory,
            &account.character,
            &items,
            &skill_catalog,
        )?;
        let grant_applied = granted.is_some();
        let inventory = granted.unwrap_or(inventory);
        inventory.validate_for_profile(&account.character, &items, &skill_catalog)?;
        let equipment = EquipmentStats::derive(&inventory, &items, &skill_catalog)?;
        let factions = crate::factions::FactionCatalog::load()?;
        let reputation =
            factions.reactions(account.character.class(), &inventory.faction_standings)?;
        let stats = equipment.ship;
        let path = state_dir.join("player.json");
        let mut position = [0.; 3];
        let mut home_station = default_home_station();
        if path.exists() {
            let saved: SavedPlayer = serde_json::from_slice(&fs::read(path)?)
                .map_err(|_| invalid("invalid saved player JSON"))?;
            if saved.sector != sector.id {
                sector = sectors.sector(saved.sector)?;
            }
            if saved.version != 1 || !sector.bounds.contains(saved.position) {
                return Err(invalid("invalid saved player position/sector"));
            }
            position = saved.position;
            home_station = saved.home_station;
        }
        let mobs = sector
            .mobs
            .iter()
            .cloned()
            .map(Mob::new)
            .collect::<Vec<_>>();
        let mob_states = vec![MobState::Idle; mobs.len()];
        let mob_visible = vec![false; mobs.len()];
        let mob_relationships = vec![None; mobs.len()];
        let now = Instant::now();
        let next_effect = 1_000_001 + (account.character.game_id & 0xffff) * 1_000_000;
        let mut resource_field = ResourceField::load(
            &sector,
            &items,
            world_root.join(format!("resources/{}.json", sector.id)),
        )?;
        resource_field.register_receipts(account.character.game_id, &inventory.harvests)?;
        let mut loot_world = LootWorld::load(
            world_root.join(format!("loot/{}.json", sector.id)),
            sector.id,
            &items,
        )?;
        loot_world.register_receipts(account.character.game_id, &inventory.loot_receipts)?;
        let mut session = Self {
            account,
            sectors,
            items,
            npcs,
            factions,
            reputation,
            inventory,
            workbench: None,
            session_epoch: rand::random::<u64>().max(1),
            workbench_sync: 0.,
            workbench_lab_ready: false,
            equipment,
            resource_field: Arc::new(Mutex::new(resource_field)),
            loot_world: Arc::new(Mutex::new(loot_world)),
            loot_cast: None,
            loot_window: None,
            visible_corpses: HashMap::new(),
            loot_revision: None,
            last_loot_publish: None,
            world_root,
            mining: None,
            harvest_visuals: Vec::new(),
            resource_window: None,
            resource_revision: None,
            sector,
            bind,
            position,
            logged_in: false,
            home_station,
            motion: Motion::default(),
            selected_peer: None,
            endpoint: None,
            comms: None,
            nonce: [0; 20],
            tickets: HashMap::new(),
            registrations: HashMap::new(),
            sequence: 0,
            replay: VecDeque::new(),
            inputs: VecDeque::new(),
            pending: Vec::new(),
            outgoing: Vec::new(),
            warp: Warp::default(),
            warp_effects: Vec::new(),
            next_effect,
            last_motion_report: None,
            last_launch: 0,
            mob_damage_receipts: Vec::new(),
            following: false,
            follow_peer: None,
            target: u32::MAX,
            last_verbs: None,
            gate_transit: None,
            docking_target: None,
            handoff: None,
            transition_pending: None,
            transition_retry: now,
            station_pose: None,
            station_peers: HashMap::new(),
            station_view_dirty: false,
            last_station_broadcast: now,
            npc_interaction: None,
            combat: Arc::new(Mutex::new(SectorCombat::from_actors(mobs))),
            mob_states,
            mob_visible,
            mob_relationships,
            player_firing: false,
            weapon_cooldowns: [0.; 20],
            weapon_firing: [false; 20],
            shield: stats.shield_capacity,
            hull: stats.hull_capacity,
            energy: stats.energy_capacity,
            skill_catalog,
            skills: SkillState::default(),
            group_contacts: Vec::new(),
            support_actions: Vec::new(),
            resource_sync: 0.,
            last_seen: now,
            last_broadcast: now,
            last_saved: now,
            state_dir,
            dirty: false,
            started: now,
            tick_offset: 0,
            last_checkpoint: now,
            last_ack_request: now,
            peer_ids: HashSet::new(),
            peer_contacts: HashMap::new(),
            visible_peers: HashSet::new(),
            peer_stats: HashMap::new(),
            visible_resources: HashSet::new(),
            resource_fractions: HashMap::new(),
            last_resource_visibility: None,
            last_peer_broadcast: now,
            client_events: Vec::new(),
            formation_leader: None,
            last_formation_broadcast: now,
        };
        lock_loot(&session.loot_world)?.reconcile_combat(
            &mut *lock_combat(&session.combat)?,
            crate::resources::unix_ms(),
        );
        if let Err(error) = session.restore_session() {
            eprintln!("Session recovery skipped: {error}");
            session.cancel_mining("Session recovery could not complete.")?;
            session.cancel_looting("Session recovery could not complete.")?;
            session.logged_in = false;
            session.selected_peer = None;
            session.endpoint = None;
            session.comms = None;
            session.handoff = None;
            session.pending.clear();
            session.forget_session()?;
        }
        session.refresh_equipment()?;
        if grant_applied {
            session.shield = session.equipment.ship.shield_capacity;
            session.energy = session.equipment.ship.energy_capacity;
            session.pending.push(packets::ship_resources(
                session.player_id(),
                &session.equipment.ship,
                Some(session.energy),
                Some(session.shield),
                Some(session.hull),
            ));
        }
        Ok(session)
    }
    pub fn player_id(&self) -> u32 {
        self.account.character.game_id
    }

    pub fn tick_ms(&self) -> u32 {
        self.tick_offset
            .wrapping_add(self.started.elapsed().as_millis() as u32)
    }

    pub fn issue_ticket(
        &mut self,
        username: &str,
        password: &str,
        peer: IpAddr,
    ) -> io::Result<String> {
        // A deliberately separate demo account. Production credentials are never
        // accepted, saved, or forwarded by this sandbox.
        if !self.account.verify(username, password) {
            return Err(invalid("invalid local sandbox credentials"));
        }
        self.issue_verified_ticket(peer)
    }

    pub(crate) fn issue_verified_ticket(&mut self, peer: IpAddr) -> io::Result<String> {
        if !self
            .registrations
            .get(&peer)
            .is_some_and(|at| at.elapsed() < Duration::from_secs(600))
        {
            return Err(invalid("register UDP before account login"));
        }
        self.tickets
            .retain(|_, ticket| ticket.expires > Instant::now());
        if self.tickets.len() >= 32 {
            return Err(invalid("too many pending login tickets"));
        }
        let mut random = [0u8; 16];
        rand::thread_rng().fill_bytes(&mut random);
        let ticket = format!(
            "{}-{}",
            self.account.username,
            random
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect::<String>()
        );
        self.tickets.insert(
            ticket.clone(),
            Ticket {
                peer,
                expires: Instant::now() + Duration::from_secs(60),
            },
        );
        Ok(ticket)
    }
    pub fn consume_ticket(&mut self, ticket: &str, peer: IpAddr) -> bool {
        // Check peer ownership BEFORE removing: another connection cannot burn
        // somebody else's one-use ticket by guessing its value.
        let valid = self
            .tickets
            .get(ticket)
            .is_some_and(|t| t.peer == peer && t.expires > Instant::now());
        if valid {
            self.tickets.remove(ticket);
        }
        valid
    }
    pub fn select(&mut self, slot: u32, peer: IpAddr, via_proxy: bool) -> io::Result<Frame> {
        self.recover_weapon_launch()?;
        if self.transition_pending.is_some() {
            return Err(invalid(
                "Character transition is waiting for storage recovery.",
            ));
        }
        if slot != 0 {
            return Err(invalid("only character slot 0 exists in this sandbox"));
        }
        if self.selected_peer.is_some() {
            // Selection follows successful account authentication. A fresh
            // login may replace a stale native/proxy connection immediately.
            // Retire its claims, transport and casts before issuing a ticket;
            // old endpoint/comms logout packets then fail the normal pin checks.
            println!(
                "Authenticated session takeover: {}",
                self.account.character.name
            );
            self.logout()?;
            self.outgoing.clear();
            self.support_actions.clear();
            self.client_events.clear();
            self.inputs.clear();
            self.replay.clear();
        }
        self.stop_following();
        self.selected_peer = Some(peer);
        self.session_epoch = rand::random::<u64>().max(1);
        self.gate_transit = None;
        self.endpoint = None;
        self.comms = None;
        self.logged_in = false;
        rand::thread_rng().fill_bytes(&mut self.nonce);
        if via_proxy {
            // Net7Proxy::SendGlobalTicket rebuilds the join structure rather
            // than forwarding our ticket. Its fixed text is zero-padded to
            // twenty bytes. Character selection is already authenticated;
            // sector input remains pinned to the selected IP and handoff port.
            self.nonce = [0; 20];
            self.nonce[..16].copy_from_slice(b"MY_Avatar_Ticket");
        }
        self.last_seen = Instant::now();
        println!(
            "Character selected: {}, sector={}",
            self.account.character.name, self.sector.id
        );
        Ok(packets::character_ticket(
            self.sector.id,
            &self.nonce,
            self.player_id(),
        ))
    }
    fn reply(
        &mut self,
        port: u16,
        peer: SocketAddr,
        opcode: u16,
        player: u32,
        body: Vec<u8>,
    ) -> io::Result<()> {
        self.outgoing.push(Outbound {
            port,
            peer,
            data: Datagram {
                opcode,
                player,
                sequence: 0,
                body,
            }
            .encode()?,
        });
        Ok(())
    }
    pub fn handle_udp(&mut self, port: u16, peer: SocketAddr, packet: Datagram) -> io::Result<()> {
        if port == 3806 {
            match packet.opcode {
                0x1000 => {
                    if packet.body.len() != 4 {
                        return Err(invalid("registration requires a version"));
                    }
                    let version = Reader::new(&packet.body).u32()?;
                    if version < 146 {
                        return self.reply(port, peer, 0x1009, 0, 246u32.to_le_bytes().to_vec());
                    }
                    if self.registrations.len() >= 64
                        && !self.registrations.contains_key(&peer.ip())
                    {
                        return Err(invalid("registration capacity reached"));
                    }
                    self.registrations.insert(peer.ip(), Instant::now());
                    self.reply(port, peer, 0x1001, 0, vec![0; 8])?;
                }
                0x200f
                    if packet.player == self.player_id()
                        && self.selected_peer == Some(peer.ip()) =>
                {
                    self.comms = Some(peer);
                }
                0x1008
                    if self.logged_in
                        && packet.player == self.player_id()
                        && self.comms == Some(peer) =>
                {
                    self.logout()?;
                }
                0x3005 if self.comms == Some(peer) => {
                    self.last_seen = Instant::now();
                }
                0x1004
                    if packet.player == self.player_id()
                        && self.comms == Some(peer)
                        && self.logged_in =>
                {
                    self.last_seen = Instant::now();
                    self.apply_motion_report(&packet.body)?;
                }
                _ => {}
            }
            return Ok(());
        }
        if self.selected_peer != Some(peer.ip()) || packet.player != self.player_id() {
            return Err(invalid("packet does not belong to the selected character"));
        }
        if self.transition_pending.is_some() {
            return Err(invalid(
                "Sector transition is waiting for storage recovery.",
            ));
        }
        if port == 3808 {
            if packet.opcode != 0x2008
                || packet.body.len() < 4
                || Reader::new(&packet.body).u32()? != self.sector.id
            {
                return Err(invalid("invalid sector handoff request"));
            }
            self.endpoint = Some(peer);
            self.sequence = 0;
            self.replay.clear();
            self.inputs.clear();
            self.session_epoch = rand::random::<u64>().max(1);
            self.pending.clear();
            let mut body = Vec::from(self.bind.octets());
            body.extend_from_slice(&SECTOR_PORT.to_le_bytes());
            body.extend_from_slice(&self.player_id().to_le_bytes());
            return self.reply(port, peer, 0x2009, self.player_id(), body);
        }
        if port != SECTOR_PORT || self.endpoint != Some(peer) {
            return Err(invalid("sector endpoint mismatch"));
        }
        self.last_seen = Instant::now();
        if self.inputs.contains(&packet.sequence) {
            return Ok(());
        }
        self.inputs.push_back(packet.sequence);
        if self.inputs.len() > 256 {
            self.inputs.pop_front();
        }
        self.handle_input(packet.opcode, &packet.body)
    }

    pub fn handle_input(&mut self, opcode: u16, body: &[u8]) -> io::Result<()> {
        self.recover_weapon_launch()?;
        if self.transition_pending.is_some() {
            return Err(invalid(
                "Sector transition is waiting for storage recovery.",
            ));
        }
        if self.gate_transit.is_some() && matches!(opcode, 0x12..=0x14 | 0x9b) {
            return Err(invalid("Wait for the gate transition."));
        }
        if self.sector.station.is_some() && matches!(opcode, 0x12..=0x14 | 0x58 | 0x9b) {
            return Err(invalid("Launch into space before controlling the ship."));
        }
        let mut reader = Reader::new(body);
        match opcode {
            2 => {
                if body.len() != 137 {
                    return Err(invalid("login requires the 137-byte Win32 Login structure"));
                }
                let mut join = Reader::new(&body[16..]);
                if join.be_u32()? != self.player_id()
                    || join.be_u32()? != self.sector.id
                    || body[44..64] != self.nonce
                {
                    return Err(invalid("invalid character login ticket"));
                }
                self.logged_in = true;
                self.workbench = None;
                self.cancel_looting("New sector login.")?;
                self.loot_window = None;
                self.visible_corpses.clear();
                self.loot_revision = None;
                self.last_loot_publish = None;
                self.npc_interaction = None;
                self.station_peers.clear();
                self.station_view_dirty = true;
                self.peer_ids.clear();
                self.peer_contacts.clear();
                self.visible_peers.clear();
                self.peer_stats.clear();
                self.visible_resources.clear();
                self.resource_fractions.clear();
                self.resource_window = None;
                self.resource_revision = None;
                self.last_resource_visibility = None;
                self.handoff = None;
                self.gate_transit = None;
                self.warp.reset(&mut self.motion);
                self.warp_effects.clear();
                self.last_motion_report = None;
                self.stop_weapons();
                self.motion.stop();
                let time = u32::from_le_bytes(
                    body[64..68]
                        .try_into()
                        .map_err(|_| invalid("missing login time"))?,
                );
                self.pending
                    .push(packets::client_time(time, self.tick_ms()));
                self.pending.extend(packets::login_world(
                    &self.sector,
                    self.position,
                    self.tick_ms(),
                    &self.account.character,
                    &self.motion,
                    &packets::ShipInitialization {
                        catalog: &self.items,
                        item_templates: self.inventory.templates(&self.items)?,
                        inventory: &self.inventory,
                        stats: &self.equipment.ship,
                        resources: self.ship_resources(),
                    },
                ));
                self.publish_reputation();
                let shared = Arc::clone(&self.combat);
                let combat = lock_combat(&shared)?;
                self.mob_visible.fill(false);
                self.mob_relationships.resize(combat.actors.len(), None);
                for (index, mob) in combat.actors.iter().enumerate() {
                    self.mob_visible[index] = self.mob_visible_in_scan(index, mob);
                    if self.mob_visible[index] {
                        self.mob_relationships[index] = Some(self.mob_relationship(mob));
                        self.pending.extend(packets::mob_spawn(
                            MOB_ID + index as u32,
                            mob,
                            self.tick_ms(),
                            self.mob_relationship(mob),
                        ));
                    }
                }
                println!(
                    "Sector login complete: {}, navpoints={}, mobs={}",
                    self.account.character.name,
                    self.sector.navpoints.len(),
                    self.mob_states.len()
                );
                self.publish_weapon_states();
                self.initialise_workbench_lab(true)?;
            }
            6 | 0x3004 | 0x3008 => {}
            0x8d => self.incap_request(body)?,
            0x79 | 0x7a | 0x7b | 0x7c | 0x7e | 0x80 => {
                if let Err(error) = self.workbench_input(opcode, body) {
                    self.pending.push(packets::message(&error.to_string()));
                    self.refresh_workbench(true)?;
                }
            }
            0x27 => self.move_inventory(body)?,
            0x5d => self.activate_equipped_item(body)?,
            0x55 => self.talk_choice(body)?,
            0x9d | 0x9f => self.station_movement(opcode, body)?,
            0x44 => {
                let client_sent = reader.u32()?;
                self.pending
                    .push(packets::client_time(client_sent, self.tick_ms()));
            }
            0x12..=0x14 => {
                if self.hull <= 0. {
                    return Err(invalid("An incapacitated ship cannot fly."));
                }
                let owner = reader.u32()?;
                if !self.logged_in
                    || (owner != self.player_id() && owner != self.player_id() & 0xffffff)
                {
                    return Err(invalid("movement owner mismatch or sector not started"));
                }
                if opcode == 0x14 && body.get(4) == Some(&12) {
                    if let Err(error) = self.begin_following() {
                        self.pending.push(packets::message(&error.to_string()));
                    }
                    return Ok(());
                }
                self.stop_following();
                // Steering or thrust is an explicit request to fly independently.
                self.detach_formation();
                if opcode == 0x14 && body.get(4) == Some(&4) && self.warp.active() {
                    let event = self.warp.finish(FinishReason::Cancelled, &mut self.motion);
                    self.emit_warp_event(event);
                } else if !self.warp.active() {
                    if opcode == 0x14 {
                        let command = reader.take(1)?[0];
                        self.motion.thrust(command)?;
                        if self.skills.cloak_rank == 1 {
                            self.motion.set_speed *= 0.5;
                        }
                    } else {
                        self.motion.steer(opcode == 0x12, reader.f32()?)?;
                    }
                    self.pending.push(packets::player_movement(
                        self.player_id(),
                        self.position,
                        self.tick_ms(),
                        &self.motion,
                    ));
                }
            }
            0x2017 => {
                let first = reader.u32()?;
                let count = reader.u32()?;
                if count > 128 {
                    return Err(invalid("resend window exceeds cache limit"));
                }
                if let Some(peer) = self.endpoint {
                    for p in &self.replay {
                        if p.sequence >= first && p.sequence < first.saturating_add(count) {
                            self.outgoing.push(Outbound {
                                port: SECTOR_PORT,
                                peer,
                                data: p.encode()?,
                            });
                        }
                    }
                }
            }
            0x2021 => {
                let ack = reader.u32()?;
                while self.replay.front().is_some_and(|p| p.sequence < ack) {
                    self.replay.pop_front();
                }
            }
            0x17 => {
                if reader.u32()? != self.player_id() {
                    return Err(invalid("target owner mismatch"));
                }
                let target = reader.u32()?;
                if self.sector.navpoints.iter().any(|n| n.id == target)
                    || self.visible_resources.contains(&target)
                    || self.visible_corpses.contains_key(&target)
                    || target == self.player_id()
                    || self.peer_ids.contains(&target)
                    || self.mob_target_in_scan(target)?
                {
                    if self.target != target {
                        self.cancel_mining("Target changed.")?;
                        self.cancel_looting("Target changed.")?;
                        if self.loot_window.take().is_some() {
                            self.pending.push(packets::resource_interface(false));
                        }
                        self.loot_revision = None;
                        if self.resource_window.take().is_some() {
                            self.pending.push(packets::resource_interface(false));
                        }
                        self.resource_revision = None;
                    }
                    self.target = target;
                    let mut reply = Vec::from(target.to_le_bytes());
                    reply.extend_from_slice(&u32::MAX.to_le_bytes());
                    self.pending.push(Frame::new(0x19, reply));
                    self.pending
                        .push(packets::ship_target(self.player_id(), target));
                    println!(
                        "Target selected: {}; object={target}",
                        self.account.character.name
                    );
                    self.refresh_target_verbs(true);
                    self.refresh_loot_target(true)?;
                    self.refresh_resource_target(true)?;
                }
            }
            0x5a => {
                let owner = reader.be_u32()?;
                let target = reader.be_u32()?;
                if owner == self.player_id() && target == self.target {
                    self.refresh_target_verbs(true);
                }
            }
            0x9b => {
                let owner = reader.u32()?;
                if !self.logged_in
                    || (owner != self.player_id() & 0xffffff && owner != self.player_id())
                {
                    return Err(invalid("warp owner mismatch"));
                }
                let count = reader.u16()? as usize;
                println!(
                    "Warp request: {}; waypoints={count}; phase={:?}",
                    self.account.character.name, self.warp.phase
                );
                if count > 64 || reader.rest().len() != count * 4 {
                    return Err(invalid("invalid warp waypoint count"));
                }
                self.detach_formation();
                if self.warp.active() {
                    let event = self.warp.finish(FinishReason::Cancelled, &mut self.motion);
                    self.emit_warp_event(event);
                    return Ok(());
                }
                if self.hull <= 0. {
                    return Err(invalid("Cannot warp while incapacitated."));
                }
                if self.energy <= 0. {
                    self.pending
                        .push(packets::message("The reactor has no energy for warp."));
                    return Ok(());
                }
                if self.skills.cast.is_some() {
                    return Err(invalid("An ability is charging."));
                }
                if self.skills.cloak_rank > 0 && self.skills.cloak_rank < 6 {
                    self.stop_cloak();
                }
                let mut route = VecDeque::new();
                for _ in 0..count {
                    let id = reader.u32()?;
                    route.push_back(self.warp_waypoint(id)?);
                }
                self.stop_following();
                match self.warp.start(route, self.position, &mut self.motion) {
                    Ok(event) => self.emit_warp_event(event),
                    Err(error) => self.pending.push(packets::message(&error.to_string())),
                }
            }
            0x58 => {
                let owner = reader.u32()?;
                let _action = reader.u32()?;
                let ability = reader.u32()?;
                if owner != self.player_id() && owner != self.player_id() & 0xffffff
                    || !reader.rest().is_empty()
                {
                    return Err(invalid("invalid ability owner or packet length"));
                }
                if let Err(error) = self.begin_ability(ability) {
                    self.pending.push(packets::message(&error.to_string()));
                }
            }
            0x1004 => {
                self.apply_motion_report(body)?;
            }
            0x4e => {
                let owner = reader.u32()?;
                let station = reader.u32()?;
                let action = reader.take(1)?[0];
                if body.len() != 9
                    || (owner != self.player_id() && owner != self.player_id() & 0xffffff)
                {
                    return Err(invalid("invalid station request owner or length"));
                }
                self.station_request(station, action)?;
            }
            0x51 => {
                let owner = reader.u32()?;
                let _unknown = reader.u32()?;
                if owner != self.player_id() && owner != self.player_id() & 0xffffff
                    || !reader.rest().is_empty()
                {
                    return Err(invalid("Invalid loot permission request."));
                }
                self.client_events.push(ClientEvent::LootOpen {
                    target: self.target,
                    toggle: false,
                });
            }
            0x2c => {
                if reader.u32()? != self.player_id() {
                    return Err(invalid("action owner mismatch"));
                }
                let action = reader.u32()?;
                let target = reader.u32()?;
                let optional = reader.u32()?;
                if optional != 0 {
                    return Err(invalid("unsupported action option"));
                }
                match action {
                    1 if self.visible_corpses.contains_key(&self.target) => {
                        self.client_events.push(ClientEvent::LootOpen {
                            target: self.target,
                            toggle: true,
                        });
                    }
                    17 => self.open_resource_window(target)?,
                    18 => self.begin_gating(target)?,
                    19 => self.complete_gating(target)?,
                    26 => {
                        if let Err(error) = self.begin_jumpstart(target) {
                            self.pending.push(packets::message(&error.to_string()));
                        }
                    }
                    9 => {
                        if self.skills.cloak_rank > 0 {
                            self.stop_cloak();
                        }
                        self.toggle_all_weapons();
                    }
                    10..=15 => self
                        .client_events
                        .push(ClientEvent::Group { action, target }),
                    28 => self.begin_docking(target)?,
                    7 => self.complete_docking(target)?,
                    8 => self
                        .pending
                        .push(packets::message("Planet landing is not implemented yet.")),
                    _ => {}
                }
            }
            0x33 => {
                if reader.u32()? != self.player_id() {
                    return Err(invalid("chat owner mismatch"));
                }
                let channel = reader.take(1)?[0];
                let size = reader.u16()? as usize;
                if size == 0 || size > 1024 || channel > 4 {
                    return Err(invalid("invalid chat length or channel"));
                }
                let text = String::from_utf8_lossy(reader.take(size)?)
                    .trim_end_matches('\0')
                    .to_owned();
                self.client_events.push(ClientEvent::Chat { channel, text });
            }
            0xbc => {
                if reader.u32()? != self.player_id() {
                    return Err(invalid("formation owner mismatch"));
                }
                let target = reader.u32()?;
                let action = reader.u32()?;
                if !reader.rest().is_empty() {
                    return Err(invalid("invalid formation packet length"));
                }
                self.client_events
                    .push(ClientEvent::Formation { action, target });
            }
            3 | 0xb9 => {
                self.cancel_mining("Logged out.")?;
                self.cancel_looting("Logged out.")?;
                self.pending.push(Frame::new(0xba, vec![]));
                self.logged_in = false;
                self.npc_interaction = None;
                self.selected_peer = None;
                self.save()?;
                self.forget_session()?;
                self.skills = SkillState::default();
            }
            0x98 | 0xa3 | 0x1a | 0x2025 | 0x3005 => {}
            _ => {
                eprintln!(
                    "Unimplemented client opcode {opcode:#06x}, bytes={}",
                    body.len()
                );
            }
        }
        if self.pending.len() > 4096 {
            return Err(invalid("outgoing frame queue exceeded limit"));
        }
        Ok(())
    }
    fn move_inventory(&mut self, body: &[u8]) -> io::Result<()> {
        if !self.logged_in || self.hull <= 0. || self.warp.active() || body.len() != 24 {
            return Err(invalid(
                "inventory transfer unavailable in the current ship state",
            ));
        }
        let mut reader = Reader::new(body);
        let owner = reader.be_u32()?;
        if owner != self.player_id() && owner != self.player_id() & 0xffffff {
            return Err(invalid("inventory owner mismatch"));
        }
        let transfer = ItemTransfer {
            source: reader.be_u32()?,
            source_slot: reader.be_u32()? as usize,
            destination: reader.be_u32()?,
            destination_slot: reader.be_u32()? as usize,
            quantity: reader.be_u32()?,
        };
        if (transfer.source == 6 || transfer.source == 18)
            && self.visible_corpses.contains_key(&self.target)
        {
            self.client_events.push(ClientEvent::LootBegin {
                target: self.target,
                slot: transfer.source_slot,
            });
            return Ok(());
        }
        if transfer.source == 18 {
            if let Err(error) = self.begin_mining(transfer) {
                self.pending.push(packets::message(&error.to_string()));
                self.refresh_resource_target(true)?;
            }
            return Ok(());
        }
        self.transfer_inventory(transfer)
    }

    pub(super) fn transfer_inventory(&mut self, transfer: ItemTransfer) -> io::Result<()> {
        if !self.logged_in || self.hull <= 0. || self.warp.active() {
            return Err(invalid(
                "Inventory transfer unavailable in the current ship state.",
            ));
        }
        let candidate = match self.inventory_candidate(transfer) {
            Ok(candidate) => candidate,
            Err(error) => {
                // A valid request can fail gameplay rules. Explain it and
                // restore the client's authoritative slots without changing
                // disk state. Malformed/foreign-owner packets fail above.
                self.pending.push(packets::message(&format!(
                    "Inventory move rejected: {error}"
                )));
                self.pending.push(packets::inventory_update(
                    self.player_id(),
                    &self.inventory,
                    &self.items,
                    &self.equipment.ship,
                ));
                self.pending
                    .push(packets::vault_update(&self.inventory, &self.items));
                self.publish_weapon_states();
                return Ok(());
            }
        };
        self.commit_inventory(candidate)?;
        if transfer.source == 12 || transfer.destination == 12 {
            self.refresh_workbench_target();
        }
        self.refresh_workbench(true)?;
        println!(
            "Inventory move accepted: {}; transfer={transfer:?}",
            self.account.character.name
        );
        Ok(())
    }

    /// All inventory operations share this durable publication boundary. The
    /// candidate includes both cargo and credits, so they cannot commit apart.
    fn commit_inventory(&mut self, candidate: Inventory) -> io::Result<()> {
        candidate.validate(&self.items)?;
        let equipment = EquipmentStats::derive(&candidate, &self.items, &self.skill_catalog)?;
        let prices = self.vendor_prices(&candidate)?;
        let reputation = if candidate.faction_standings == self.inventory.faction_standings
            && self.reputation.class_faction == self.account.character.class().faction()
        {
            Arc::clone(&self.reputation)
        } else {
            self.factions
                .reactions(self.account.character.class(), &candidate.faction_standings)?
        };
        let reputation_changed = self.reputation != reputation;
        let previous_templates: std::collections::BTreeSet<u32> = self
            .inventory
            .templates(&self.items)?
            .into_iter()
            .map(|item| item.id)
            .collect();
        let new_metadata: Vec<Frame> = candidate
            .templates(&self.items)?
            .into_iter()
            .filter(|item| !previous_templates.contains(&item.id))
            .map(|item| item.packet())
            .collect();
        let previous_credits = self.inventory.credits;
        // Publish only after durable replacement. A disk failure leaves the
        // authoritative inventory unchanged and sends no success snapshot.
        fs::create_dir_all(&self.state_dir)?;
        let temporary = self.state_dir.join("inventory.json.tmp");
        crate::durable_json::replace_staged(
            &self.state_dir.join("inventory.json"),
            &temporary,
            &candidate,
        )?;
        self.inventory = candidate;
        self.reputation = reputation;
        self.equipment = equipment;
        self.apply_equipment_parameters();
        if reputation_changed {
            self.publish_reputation();
        }
        // A restored production job can create a template the client has never
        // seen. Cache metadata before publishing the new owned-item reference.
        self.pending.extend(new_metadata);
        self.pending.push(packets::inventory_update(
            self.player_id(),
            &self.inventory,
            &self.items,
            &self.equipment.ship,
        ));
        self.publish_weapon_states();
        self.pending
            .push(packets::vault_update(&self.inventory, &self.items));
        self.pending.extend(packets::inventory_prices(
            self.player_id(),
            &self.inventory,
            &prices,
        ));
        if let Some(active) = self.npc_interaction.as_ref().filter(|active| active.vendor) {
            println!("Vendor inventory committed: {}; npc={}; credits_before={previous_credits}; credits_after={}", self.account.character.name, active.npc, self.inventory.credits);
        }
        Ok(())
    }

    fn inventory_candidate(&self, transfer: ItemTransfer) -> io::Result<Inventory> {
        self.check_workbench_transfer(transfer)?;
        if transfer.source == 4 || transfer.destination == 4 {
            return self.vendor_transaction(transfer);
        }
        let candidate = self.inventory.moved(transfer, &self.items)?;
        candidate.validate_for_profile(
            &self.account.character,
            &self.items,
            &self.skill_catalog,
        )?;
        Ok(candidate)
    }

    fn warp_waypoint(&self, id: u32) -> io::Result<Waypoint> {
        if let Some(position) = self.visible_corpses.get(&id) {
            return Ok(Waypoint {
                id,
                position: *position,
                stand_off: 1500.,
            });
        }
        if let Some(node) = self.sector.resources.iter().find(|node| {
            node.object.id == id && distance(self.position, node.object.position) <= 20_000.
        }) {
            return Ok(Waypoint {
                id,
                position: node.object.position,
                // Mining models can engulf the ship/camera at the old 100m
                // stopping distance. Remain within even rank-one prospect range.
                stand_off: 900.,
            });
        }
        if let Some(nav) = self.sector.navpoints.iter().find(|nav| {
            nav.id == id && nav.navigation_type > 0 && self.sector.bounds.contains(nav.position)
        }) {
            return Ok(Waypoint {
                id,
                position: nav.position,
                stand_off: match nav.object_type {
                    11 => 1500.,
                    12 => 4500.,
                    _ => 100.,
                },
            });
        }
        let combat = lock_combat(&self.combat)?;
        if let Some(index) = id.checked_sub(MOB_ID).map(|index| index as usize) {
            if let Some(mob) = combat
                .actors
                .get(index)
                .filter(|mob| self.mob_visible_in_scan(index, mob))
            {
                return Ok(Waypoint {
                    id,
                    position: mob.position,
                    stand_off: 800.,
                });
            }
        }
        Err(invalid("Warp target is not a known object in this sector."))
    }
    fn apply_motion_report(&mut self, body: &[u8]) -> io::Result<()> {
        if !matches!(body.len(), 12 | 16 | 24 | 28) {
            return Err(invalid("invalid proxy motion report length"));
        }
        let mut reader = Reader::new(body);
        let position = [reader.f32()?, reader.f32()?, reader.f32()?];
        if !self.logged_in
            || self.sector.station.is_some()
            || self.warp.phase != WarpPhase::Idle
            || self.following
            || self.formation_leader.is_some()
            || self.gate_transit.is_some()
        {
            return Ok(());
        }
        if !self.sector.bounds.contains(position) {
            return Err(invalid("motion report outside sector bounds"));
        }
        // The proxy samples the graphical client's predicted position. Keep
        // it in sync, but do not accept a stale report as a teleport after warp.
        if distance(self.position, position) > 2000. {
            return Ok(());
        }
        // Proxy 2.46 appends a four-byte timestamp to either legacy report.
        if body.len() >= 24 {
            let heading = [reader.f32()?, reader.f32()?, reader.f32()?];
            if heading.iter().map(|v| v * v).sum::<f32>() > 0.0001 {
                self.motion.face(heading)?;
            }
        }
        self.position = position;
        self.last_motion_report = Some(Instant::now());
        self.dirty = true;
        Ok(())
    }
    fn add_warp_effect(&mut self, asset: u16) {
        let id = self.next_effect;
        self.next_effect = self.next_effect.wrapping_add(1);
        self.warp_effects.push(id);
        self.pending.push(packets::object_effect(
            self.player_id(),
            id,
            asset,
            self.tick_ms(),
        ));
    }

    fn emit_warp_event(&mut self, event: WarpEvent) {
        let tick = self.tick_ms();
        match event {
            WarpEvent::Charging => {
                self.pending.push(packets::warp_state(
                    self.player_id(),
                    WarpPhase::Charging,
                    None,
                    tick,
                ));
                self.add_warp_effect(443);
                println!("Warp charging: {}", self.account.character.name);
            }
            WarpEvent::Departed => {
                self.add_warp_effect(23);
                self.pending.push(packets::warp_state(
                    self.player_id(),
                    WarpPhase::Traveling,
                    None,
                    tick,
                ));
            }
            WarpEvent::Waypoint { index, target } => {
                self.target = target;
                self.pending
                    .push(Frame::new(0x9c, index.to_le_bytes().to_vec()));
                let mut selected = Vec::from(target.to_le_bytes());
                selected.extend_from_slice(&u32::MAX.to_le_bytes());
                self.pending.push(Frame::new(0x19, selected));
                self.pending
                    .push(packets::ship_target(self.player_id(), target));
                self.refresh_target_verbs(true);
            }
            WarpEvent::Finished(reason) => {
                for id in self.warp_effects.drain(..) {
                    self.pending
                        .push(Frame::new(0xf, id.to_le_bytes().to_vec()));
                }
                self.pending.push(packets::warp_state(
                    self.player_id(),
                    WarpPhase::Recovering,
                    Some(reason),
                    tick.wrapping_add((self.warp.recovery_seconds * 1000.) as u32),
                ));
                self.pending
                    .push(Frame::new(0x9c, (-1i32).to_le_bytes().to_vec()));
                self.pending.push(packets::message(match reason {
                    FinishReason::Arrived => "Warp destination reached.",
                    FinishReason::Cancelled => "Warp cancelled.",
                    FinishReason::Boundary => "Warp stopped at the sector boundary.",
                }));
                self.dirty = true;
                println!(
                    "Warp stopped: {}; reason={reason:?}",
                    self.account.character.name
                );
            }
            WarpEvent::Ready => self.pending.push(packets::warp_state(
                self.player_id(),
                WarpPhase::Idle,
                None,
                tick,
            )),
        }
        self.pending.push(packets::player_movement(
            self.player_id(),
            self.position,
            tick,
            &self.motion,
        ));
    }
    fn flush(&mut self) -> io::Result<()> {
        let Some(peer) = self.endpoint else {
            self.pending.clear();
            return Ok(());
        };
        if self.pending.is_empty() {
            return Ok(());
        }
        // ACK requests are regular inner frames, not an extra UDP framing layer.
        if self.last_ack_request.elapsed() >= Duration::from_secs(1) {
            self.pending
                .push(Frame::new(0x2020, self.sequence.to_le_bytes().to_vec()));
            self.last_ack_request = Instant::now();
        }
        let frames = std::mem::take(&mut self.pending);
        for packet in split_frames(self.player_id(), &mut self.sequence, &frames)? {
            self.outgoing.push(Outbound {
                port: SECTOR_PORT,
                peer,
                data: packet.encode()?,
            });
            self.replay.push_back(packet);
            if self.replay.len() > 128 {
                self.replay.pop_front();
            }
        }
        Ok(())
    }
    pub fn take_outgoing(&mut self) -> Vec<Outbound> {
        std::mem::take(&mut self.outgoing)
    }
    pub fn logout(&mut self) -> io::Result<()> {
        self.recover_weapon_launch()?;
        self.stop_following();
        self.finish_transition()?;
        self.cancel_looting("Logged out.")?;
        self.cancel_mining("Logged out.")?;
        self.resource_window = None;
        self.npc_interaction = None;
        self.save()?;
        self.logged_in = false;
        self.handoff = None;
        self.docking_target = None;
        self.selected_peer = None;
        self.gate_transit = None;
        self.endpoint = None;
        self.comms = None;
        self.warp.reset(&mut self.motion);
        self.warp_effects.clear();
        self.last_motion_report = None;
        self.pending.clear();
        self.stop_weapons();
        self.forget_session()?;
        self.skills = SkillState::default();
        println!("Local character logged out");
        Ok(())
    }
    pub fn save(&mut self) -> io::Result<()> {
        self.finish_transition()?;
        fs::create_dir_all(&self.state_dir)?;
        let bytes = serde_json::to_vec_pretty(&SavedPlayer {
            version: 1,
            sector: self.sector.id,
            position: self.position,
            home_station: self.home_station,
        })?;
        let temporary = self.state_dir.join("player.json.tmp");
        fs::write(&temporary, bytes)?;
        fs::rename(temporary, self.state_dir.join("player.json"))?;
        self.dirty = false;
        self.last_saved = Instant::now();
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    pub(super) fn journal_ship() -> PlayerSession {
        let mut ship = gate_ship();
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22343".parse().unwrap());
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        ship.pending.clear();
        ship
    }

    #[test]
    fn legacy_hull_checkpoints_keep_health_fraction_when_native_class_caps_replace_the_prototype() {
        let mut ship = session();
        ship.logged_in = true;
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22343".parse().unwrap());
        ship.hull = 500.;
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let path = ship.state_dir.join("session.local.json");
        let mut legacy: serde_json::Value =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        legacy.as_object_mut().unwrap().remove("hull_capacity");
        fs::write(&path, serde_json::to_vec(&legacy).unwrap()).unwrap();
        let mut resumed =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(resumed.logged_in);
        assert_eq!(resumed.equipment.ship.hull_capacity, 42000.);
        assert_eq!(resumed.hull, 21000.);
        resumed.checkpoint_session(true).unwrap();
        let again =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(again.logged_in);
        assert_eq!(again.hull, 21000.);
    }

    #[test]
    fn handoff_journal_write_failure_keeps_source_location_transport_and_packets_unchanged() {
        let mut ship = journal_ship();
        let position = ship.position;
        let player = fs::read(ship.state_dir.join("player.json")).unwrap();
        let checkpoint = fs::read(ship.state_dir.join("session.local.json")).unwrap();
        let blocked = ship.state_dir.join("sector-transition.local.json.next");
        fs::create_dir(&blocked).unwrap();
        assert!(ship.transfer_sector(1071, true).is_err());
        assert_eq!(ship.sector.id, 1076);
        assert_eq!(ship.position, position);
        assert!(ship.logged_in);
        assert!(ship.handoff.is_none());
        assert!(ship.transition_pending.is_none());
        assert!(!ship.pending.iter().any(|frame| frame.opcode == 0x3a));
        assert_eq!(
            fs::read(ship.state_dir.join("player.json")).unwrap(),
            player
        );
        assert_eq!(
            fs::read(ship.state_dir.join("session.local.json")).unwrap(),
            checkpoint
        );
        fs::remove_dir(blocked).unwrap();
        let journal = ship.state_dir.join("sector-transition.local.json");
        fs::write(&journal, b"unresolved prior transaction").unwrap();
        assert!(ship.transfer_sector(1071, true).is_err());
        assert_eq!(fs::read(&journal).unwrap(), b"unresolved prior transaction");
        assert_eq!(ship.sector.id, 1076);
        assert!(ship.handoff.is_none());
        fs::remove_file(journal).unwrap();
        ship.transfer_sector(1071, true).unwrap();
        assert!(ship.pending.iter().any(|frame| frame.opcode == 0x3a));
        assert!(ship.transition_pending.is_none());
    }

    #[test]
    fn committed_handoff_holds_output_and_input_until_both_mirrors_can_be_installed() {
        let mut ship = journal_ship();
        let old_checkpoint = fs::read(ship.state_dir.join("session.local.json")).unwrap();
        let blocked = ship.state_dir.join("session.local.json.next");
        fs::create_dir(&blocked).unwrap();
        ship.transfer_sector(1071, true).unwrap();
        assert_eq!(ship.sector.id, 1071);
        assert!(!ship.logged_in);
        assert!(ship.transition_pending.is_some());
        assert!(ship
            .state_dir
            .join("sector-transition.local.json")
            .is_file());
        let player: SavedPlayer =
            serde_json::from_slice(&fs::read(ship.state_dir.join("player.json")).unwrap()).unwrap();
        assert_eq!(player.sector, 1071);
        assert_eq!(
            fs::read(ship.state_dir.join("session.local.json")).unwrap(),
            old_checkpoint
        );
        assert!(!ship.pending.iter().any(|frame| frame.opcode == 0x3a));
        assert!(ship.handle_input(2, &[]).is_err());
        assert!(ship.select(0, "127.0.0.1".parse().unwrap(), true).is_err());
        ship.transition_retry = Instant::now() - Duration::from_millis(300);
        ship.finish_tick().unwrap();
        assert!(ship.take_outgoing().is_empty());
        assert!(ship.transition_pending.is_some());
        fs::remove_dir(blocked).unwrap();
        ship.finish_transition().unwrap();
        assert!(ship.transition_pending.is_none());
        assert!(!ship.state_dir.join("sector-transition.local.json").exists());
        assert_eq!(
            ship.pending
                .iter()
                .filter(|frame| frame.opcode == 0x3a)
                .count(),
            1
        );
        ship.finish_transition().unwrap();
        assert_eq!(
            ship.pending
                .iter()
                .filter(|frame| frame.opcode == 0x3a)
                .count(),
            1
        );
    }

    #[test]
    fn journal_replays_a_committed_transition_before_loading_old_or_half_updated_files() {
        for mirror in ["player.json.next", "session.local.json.next"] {
            let mut ship = journal_ship();
            let blocked = ship.state_dir.join(mirror);
            fs::create_dir(&blocked).unwrap();
            ship.transfer_sector(1071, true).unwrap();
            let handoff = ship.handoff.clone();
            let sequence = ship.sequence;
            assert!(ship.transition_pending.is_some());
            fs::remove_dir(blocked).unwrap();
            let mut restored =
                PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
            assert_eq!(restored.sector.id, 1071);
            assert_eq!(restored.handoff, handoff);
            assert_eq!(restored.sequence, sequence);
            assert!(!restored.logged_in);
            assert!(restored.pending.iter().any(|frame| frame.opcode == 0x3a));
            assert!(!restored
                .state_dir
                .join("sector-transition.local.json")
                .exists());
            restored.checkpoint_session(true).unwrap();
            let again = PlayerSession::new(
                restored.sector.clone(),
                restored.bind,
                restored.state_dir.clone(),
            )
            .unwrap();
            assert_eq!(again.handoff, handoff);
            assert_eq!(again.sector.id, 1071);
        }
    }

    #[test]
    fn corrupt_or_foreign_transition_journal_never_overwrites_existing_character_files() {
        let mut ship = journal_ship();
        let blocked = ship.state_dir.join("player.json.next");
        fs::create_dir(&blocked).unwrap();
        let player = fs::read(ship.state_dir.join("player.json")).unwrap();
        let checkpoint = fs::read(ship.state_dir.join("session.local.json")).unwrap();
        ship.transfer_sector(1071, true).unwrap();
        fs::remove_dir(blocked).unwrap();
        let journal = ship.state_dir.join("sector-transition.local.json");
        let mut invalid: serde_json::Value =
            serde_json::from_slice(&fs::read(&journal).unwrap()).unwrap();
        invalid["username"] = "AnotherAccount".into();
        fs::write(&journal, serde_json::to_vec(&invalid).unwrap()).unwrap();
        assert!(
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).is_err()
        );
        assert_eq!(
            fs::read(ship.state_dir.join("player.json")).unwrap(),
            player
        );
        assert_eq!(
            fs::read(ship.state_dir.join("session.local.json")).unwrap(),
            checkpoint
        );
        assert!(journal.exists());
    }

    #[test]
    fn failed_dock_or_tow_keeps_clearance_home_station_and_original_health() {
        let mut ship = journal_ship();
        ship.sector = ship.sectors.sector(1076).unwrap();
        ship.position = ship
            .sector
            .navpoints
            .iter()
            .find(|nav| nav.id == 100002)
            .unwrap()
            .position;
        ship.target = 100002;
        ship.home_station = 17051;
        ship.hull = 500.;
        ship.shield = 12.;
        ship.energy = 77.;
        ship.skills.cloak_rank = 1;
        ship.begin_docking(100002).unwrap();
        fs::create_dir(ship.state_dir.join("sector-transition.local.json.next")).unwrap();
        assert!(ship.complete_docking(100002).is_err());
        assert_eq!(ship.docking_target, Some(100002));
        assert_eq!(ship.home_station, 17051);
        assert_eq!((ship.hull, ship.shield, ship.energy), (500., 12., 77.));
        assert_eq!(ship.skills.cloak_rank, 1);
        assert!(ship.logged_in);
        assert!(!ship.pending.iter().any(|frame| frame.opcode == 0x3a));
        ship.hull = 0.;
        ship.shield = 0.;
        ship.energy = 0.;
        assert!(ship.tow_to_station().is_err());
        assert_eq!((ship.hull, ship.shield, ship.energy), (0., 0., 0.));
        assert_eq!(ship.home_station, 17051);
        assert!(ship.logged_in);
        assert!(ship.handoff.is_none());
    }

    #[test]
    fn transition_recovery_uses_configured_map_bounds_for_a_standalone_sector() {
        let mut ship = journal_ship();
        ship.sector.bounds.max[0] = 1_999_999.;
        ship.position = [1_900_000., 0., 0.];
        ship.sector.validate().unwrap();
        let blocked = ship.state_dir.join("session.local.json.next");
        fs::create_dir(&blocked).unwrap();
        ship.transfer_sector(ship.sector.id, false).unwrap();
        fs::remove_dir(blocked).unwrap();
        let restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert_eq!(restored.position, ship.position);
        assert!(restored.handoff.is_some());
    }

    fn gate_ship() -> PlayerSession {
        let mut ship = session();
        ship.logged_in = true;
        ship.position = [18_500., 0., 0.];
        ship.target = 100000;
        ship
    }
    fn make_gate_camera_due(ship: &mut PlayerSession) {
        ship.gate_transit.as_mut().unwrap().started_at_ms =
            crate::resources::unix_ms() - crate::gating::GATE_CAMERA_DELAY_MS;
    }

    #[test]
    fn gate_opening_defers_camera_and_keeps_early_completion_instead_of_stranding_client() {
        let mut ship = gate_ship();
        ship.pending.clear();
        ship.begin_gating(100000).unwrap();
        assert!(ship
            .pending
            .contains(&packets::gate_render_state(100000, 1)));
        assert!(!ship.pending.iter().any(|frame| frame.opcode == 0x92));
        let started = ship.gate_transit.as_ref().unwrap().started_at_ms;
        ship.begin_gating(100000).unwrap();
        assert_eq!(ship.gate_transit.as_ref().unwrap().started_at_ms, started);
        assert!(ship.complete_gating(12345).is_err());
        ship.complete_gating(100000).unwrap();
        assert!(ship.logged_in);
        assert!(ship.gate_transit.as_ref().unwrap().completion_requested);
        make_gate_camera_due(&mut ship);
        ship.advance_gating().unwrap();
        assert!(!ship.logged_in);
        assert!(ship.gate_transit.is_none());
        assert!(ship.handoff.is_some());
        assert!(ship.pending.iter().any(|frame| frame.opcode == 0x3a));
        assert!(ship.complete_gating(100000).is_err());
    }

    #[test]
    fn gate_camera_and_ack_survive_replacement_without_duplicate_camera_or_lost_handoff() {
        let mut ship = gate_ship();
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22343".parse().unwrap());
        ship.begin_gating(100000).unwrap();
        make_gate_camera_due(&mut ship);
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert_eq!(restored.gate_transit, ship.gate_transit);
        restored.pending.clear();
        restored.advance_gating().unwrap();
        assert_eq!(
            restored
                .pending
                .iter()
                .filter(|frame| **frame == packets::gate_camera(4, 100000))
                .count(),
            1
        );
        restored.advance_gating().unwrap();
        assert_eq!(
            restored
                .pending
                .iter()
                .filter(|frame| **frame == packets::gate_camera(4, 100000))
                .count(),
            1
        );
        restored.save().unwrap();
        restored.checkpoint_session(true).unwrap();
        let mut again = PlayerSession::new(
            restored.sector.clone(),
            restored.bind,
            restored.state_dir.clone(),
        )
        .unwrap();
        again.pending.clear();
        again.advance_gating().unwrap();
        assert!(!again.pending.iter().any(|frame| frame.opcode == 0x92));
        again.complete_gating(0).unwrap();
        assert!(again.handoff.is_some());
        again.checkpoint_session(true).unwrap();
        let handoff =
            PlayerSession::new(again.sector.clone(), again.bind, again.state_dir.clone()).unwrap();
        assert_eq!(handoff.handoff, again.handoff);
        assert!(handoff.gate_transit.is_none());
        assert!(handoff.pending.iter().any(|frame| frame.opcode == 0x3a));
    }

    #[test]
    fn gate_rejects_incap_range_wrong_target_and_warp_then_expiry_recovers_session() {
        let mut ship = gate_ship();
        ship.hull = 0.;
        assert!(ship.begin_gating(100000).is_err());
        ship.hull = 1000.;
        ship.position = [0.; 3];
        assert!(ship.begin_gating(100000).is_err());
        ship.position = [18_500., 0., 0.];
        ship.target = 100001;
        assert!(ship.begin_gating(100000).is_err());
        ship.target = 100000;
        ship.begin_gating(100000).unwrap();
        assert!(!ship.can_form_up());
        let before = ship.position;
        let report = [
            18_600f32.to_le_bytes(),
            0f32.to_le_bytes(),
            0f32.to_le_bytes(),
        ]
        .concat();
        ship.handle_input(0x1004, &report).unwrap();
        assert_eq!(ship.position, before);
        assert!(ship.handle_input(0x9b, &[]).is_err());
        assert!(ship.begin_ability(29).is_err());
        ship.gate_transit.as_mut().unwrap().started_at_ms =
            crate::resources::unix_ms() - crate::gating::GATE_TIMEOUT_MS - 1;
        ship.advance_gating().unwrap();
        assert!(ship.gate_transit.is_none());
        assert!(ship.logged_in);
        assert!(ship
            .pending
            .contains(&packets::gate_render_state(100000, 3)));
        assert!(ship
            .pending
            .contains(&packets::gate_camera(0, ship.player_id())));
        assert!(ship.complete_gating(100000).is_err());
    }

    fn refinery_ship() -> PlayerSession {
        let mut ship = session();
        ship.sector = ship.sectors.sector(10761).unwrap();
        ship.position = [0.; 3];
        ship.logged_in = true;
        ship.inventory.cargo[0] = Some(crate::inventory::ItemInstance {
            template: 2845,
            quantity: 15,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        });
        ship.initialise_workbench_lab(true).unwrap();
        ship.workbench_input(
            0x79,
            &[ship.player_id().to_be_bytes(), 4u32.to_be_bytes()].concat(),
        )
        .unwrap();
        ship.workbench_input(
            0x7c,
            &[ship.player_id().to_be_bytes(), 3381u32.to_be_bytes()].concat(),
        )
        .unwrap();
        ship
    }

    fn production_packet(
        ship: &mut PlayerSession,
        opcode: u16,
        value: u32,
        sequence: u32,
    ) -> io::Result<()> {
        ship.inputs.push_back(sequence);
        ship.workbench_input(
            opcode,
            &[ship.player_id().to_be_bytes(), value.to_be_bytes()].concat(),
        )
    }

    fn crafting_ship() -> PlayerSession {
        let mut ship = refinery_ship();
        let mut inventory = ship.inventory.clone();
        inventory.cargo.fill(None);
        inventory.cargo[0] = Some(crate::inventory::ItemInstance {
            template: 1758,
            quantity: 1,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        });
        inventory.credits = 1_000_000;
        ship.commit_inventory(inventory).unwrap();
        production_packet(&mut ship, 0x79, 2, 1).unwrap();
        ship.transfer_inventory(ItemTransfer {
            source: 1,
            source_slot: 0,
            destination: 12,
            destination_slot: 0,
            quantity: 1,
        })
        .unwrap();
        ship
    }

    fn make_craft_due(ship: &mut PlayerSession) {
        let mut inventory = ship.inventory.clone();
        inventory.craft_job.as_mut().unwrap().due_ms = 1;
        ship.commit_inventory(inventory).unwrap();
    }

    #[test]
    fn native_dismantle_recovery_is_frozen_and_survives_disk_failure_and_restart() {
        let mut ship = crafting_ship();
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22092".parse().unwrap());
        let mut inventory = ship.inventory.clone();
        inventory.production_target[0]
            .as_mut()
            .unwrap()
            .builder_name = "OldBuilder".into();
        ship.commit_inventory(inventory).unwrap();
        production_packet(&mut ship, 0x79, 3, 8).unwrap();
        production_packet(&mut ship, 0x7e, 2, 9).unwrap();
        let recipe = crate::crafting::CraftRecipe::resolve(1758, &ship.items).unwrap();
        assert_eq!(
            ship.inventory
                .craft_job
                .as_ref()
                .unwrap()
                .recovered_components,
            recipe.components
        );
        make_craft_due(&mut ship);
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        let before = restored.inventory.clone();
        let directory = restored.state_dir.clone();
        let blocked = directory.join("blocked");
        fs::write(&blocked, b"not a directory").unwrap();
        restored.state_dir = blocked;
        assert!(restored.advance_production(0.1).is_err());
        assert_eq!(restored.inventory, before);
        restored.state_dir = directory;
        restored.advance_production(0.1).unwrap();
        assert_eq!(
            restored
                .inventory
                .cargo
                .iter()
                .flatten()
                .map(|i| i.template)
                .collect::<Vec<_>>(),
            recipe.components
        );
        assert!(restored.inventory.production_target[0].is_none());
        assert!(restored.inventory.known_formulas.is_empty());
        assert_eq!(restored.inventory.credits, before.credits - recipe.cost);
        assert_eq!(restored.inventory.trade_xp, before.trade_xp);
        let complete = restored.inventory.clone();
        restored.advance_production(0.1).unwrap();
        assert_eq!(restored.inventory, complete);
        assert!(production_packet(&mut restored, 0x7e, 2, 9).is_err());
    }

    #[test]
    fn new_owned_template_metadata_precedes_inventory_publication_after_commit() {
        let mut ship = session();
        let candidate = ship
            .inventory
            .received(1758, 1, u32::MAX as usize, &ship.items)
            .unwrap();
        ship.pending.clear();
        ship.commit_inventory(candidate).unwrap();
        let metadata = ship
            .pending
            .iter()
            .position(|frame| frame.opcode == 0x25 && frame.body[..4] == 1758u32.to_be_bytes())
            .unwrap();
        let inventory = ship
            .pending
            .iter()
            .position(|frame| frame.opcode == 0x1b)
            .unwrap();
        assert!(metadata < inventory);
    }

    #[test]
    fn native_analysis_escrow_learns_recipe_then_manufactures_with_real_components() {
        let mut ship = crafting_ship();
        let before = ship.inventory.clone();
        assert!(production_packet(&mut ship, 0x79, 1, 2).is_err());
        assert!(production_packet(&mut ship, 0x79, 4, 3).is_err());
        assert_eq!(ship.inventory, before);
        assert!(ship.launch_from_station().is_err());
        production_packet(&mut ship, 0x7e, 2, 10).unwrap();
        assert_eq!(ship.inventory.credits, before.credits);
        assert_eq!(ship.inventory.production_target, before.production_target);
        assert!(ship.inventory.known_formulas.is_empty());
        assert!(production_packet(&mut ship, 0x79, 4, 11).is_err());
        assert!(ship
            .inventory_candidate(ItemTransfer {
                source: 12,
                source_slot: 0,
                destination: 1,
                destination_slot: 0,
                quantity: 1
            })
            .is_err());
        make_craft_due(&mut ship);
        ship.advance_production(0.1).unwrap();
        assert!(ship.inventory.known_formulas.contains(&1758));
        assert!(ship.inventory.production_target[0].is_none());
        let recipe = crate::crafting::CraftRecipe::resolve(1758, &ship.items).unwrap();
        assert_eq!(ship.inventory.credits, before.credits - recipe.cost);
        production_packet(&mut ship, 0x79, 1, 12).unwrap();
        production_packet(&mut ship, 0x7b, 1758, 13).unwrap();
        assert!(production_packet(&mut ship, 0x7e, 2, 14).is_err());
        let mut inventory = ship.inventory.clone();
        for (index, component) in recipe.components.iter().enumerate() {
            inventory.cargo[index] = Some(crate::inventory::ItemInstance {
                template: *component,
                quantity: 1,
                quality: 1.,
                structure: 1.,
                builder_name: String::new(),
            });
        }
        ship.commit_inventory(inventory).unwrap();
        production_packet(&mut ship, 0x7e, 2, 15).unwrap();
        make_craft_due(&mut ship);
        ship.advance_production(0.1).unwrap();
        assert_eq!(
            ship.inventory
                .cargo
                .iter()
                .flatten()
                .map(|item| item.template)
                .collect::<Vec<_>>(),
            vec![1758]
        );
        assert_eq!(ship.inventory.credits, before.credits - 2 * recipe.cost);
        assert_eq!(ship.inventory.trade_xp, 50);
        assert!(production_packet(&mut ship, 0x7e, 2, 15).is_err());
    }

    #[test]
    fn analysis_restart_cancellation_and_failed_disk_write_preserve_owned_sample() {
        let mut ship = crafting_ship();
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22092".parse().unwrap());
        production_packet(&mut ship, 0x7e, 2, 100).unwrap();
        make_craft_due(&mut ship);
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert_eq!(restored.inventory, ship.inventory);
        let before = restored.inventory.clone();
        let directory = restored.state_dir.clone();
        let blocked = directory.join("blocked");
        fs::write(&blocked, b"not a directory").unwrap();
        restored.state_dir = blocked;
        assert!(restored.advance_production(0.1).is_err());
        assert_eq!(restored.inventory, before);
        restored.state_dir = directory;
        restored.advance_production(0.1).unwrap();
        let complete = restored.inventory.clone();
        restored.advance_production(0.1).unwrap();
        assert_eq!(restored.inventory, complete);
        assert!(complete.known_formulas.contains(&1758));
        let mut cancelled = crafting_ship();
        let sample = cancelled.inventory.production_target.clone();
        let credits = cancelled.inventory.credits;
        production_packet(&mut cancelled, 0x7e, 2, 20).unwrap();
        production_packet(&mut cancelled, 0x7e, 0, 21).unwrap();
        assert!(cancelled.inventory.craft_job.is_none());
        assert_eq!(cancelled.inventory.production_target, sample);
        assert_eq!(cancelled.inventory.credits, credits);
        // Closing leaves the sample owned; it can be returned without reopening.
        cancelled
            .transfer_inventory(ItemTransfer {
                source: 12,
                source_slot: 0,
                destination: 1,
                destination_slot: 0,
                quantity: 1,
            })
            .unwrap();
        assert!(cancelled.inventory.production_target[0].is_none());
        assert_eq!(
            cancelled.inventory.cargo[0].as_ref().unwrap().template,
            1758
        );
        // Native terminal-reset and action-leave take the same cancel path.
        let mut reset = crafting_ship();
        production_packet(&mut reset, 0x7e, 2, 30).unwrap();
        production_packet(&mut reset, 0x79, 0, 31).unwrap();
        assert!(reset.inventory.craft_job.is_none());
        assert!(reset.inventory.production_target[0].is_some());
        assert_eq!(reset.inventory.credits, 1_000_000);
    }

    fn start_refine(ship: &mut PlayerSession, sequence: u32, stack: bool) {
        ship.inputs.push_back(sequence);
        ship.workbench_input(
            0x7e,
            &[
                ship.player_id().to_be_bytes(),
                (if stack { 3u32 } else { 2u32 }).to_be_bytes(),
            ]
            .concat(),
        )
        .unwrap();
    }

    fn make_refine_due(ship: &mut PlayerSession) {
        let mut candidate = ship.inventory.clone();
        candidate.refine_job.as_mut().unwrap().due_ms = 1;
        ship.commit_inventory(candidate).unwrap();
    }

    #[test]
    fn native_refinery_selection_stack_timer_and_atomic_result() {
        let mut ship = refinery_ship();
        let credits = ship.inventory.credits;
        assert!(ship.pending.iter().any(
            |frame| frame.opcode == 0x7f && frame.body == ship.workbench_lab_id().to_be_bytes()
        ));
        start_refine(&mut ship, 100, true);
        assert_eq!(ship.inventory.refine_job.as_ref().unwrap().iterations, 3);
        assert_eq!(ship.inventory.cargo[0].as_ref().unwrap().quantity, 15);
        assert_eq!(ship.inventory.credits, credits);
        assert!(ship.launch_from_station().is_err());
        make_refine_due(&mut ship);
        ship.advance_production(0.1).unwrap();
        assert!(ship.inventory.refine_job.is_none());
        assert_eq!(ship.inventory.cargo[0].as_ref().unwrap().template, 3381);
        assert_eq!(ship.inventory.cargo[0].as_ref().unwrap().quantity, 3);
        assert_eq!(ship.inventory.credits, credits - 450);
        assert_eq!(ship.inventory.trade_xp, 150);
        let after = ship.inventory.clone();
        ship.advance_production(0.1).unwrap();
        assert_eq!(ship.inventory, after);
    }

    #[test]
    fn refinery_wire_uses_private_lab_and_rejects_foreign_owner_space_and_malformed_requests() {
        let mut ship = refinery_ship();
        let lab = ship.workbench_lab_id();
        let create = ship
            .pending
            .iter()
            .find(|frame| frame.opcode == 4 && frame.body[..4] == lab.to_le_bytes())
            .unwrap();
        assert_eq!(&create.body[8..11], &[0xff, 0xff, 0x27]);
        let frame = ship
            .pending
            .iter()
            .rev()
            .find(|frame| {
                frame.opcode == 0x1b
                    && frame.body[..4] == lab.to_le_bytes()
                    && frame.body[7] & 4 != 0
            })
            .unwrap();
        // Native ManufacturingIndex has 21 two-bit fields: six flag bytes.
        let flags_size = (4usize + 21 * 2).div_ceil(8);
        let mut reader = Reader::new(&frame.body[7 + flags_size..]);
        let length = reader.u16().unwrap() as usize;
        assert_eq!(reader.take(length).unwrap(), b"Manufacturing Lab");
        assert_eq!(reader.u32().unwrap(), 4); // workbench mode
        assert_eq!(reader.u32().unwrap(), 13); // valid ready operation
        let original = ship.inventory.clone();
        assert!(ship
            .workbench_input(
                0x7e,
                &[(ship.player_id() + 1).to_be_bytes(), 2u32.to_be_bytes()].concat()
            )
            .is_err());
        assert!(ship.workbench_input(0x79, &[0; 7]).is_err());
        assert!(ship
            .workbench_input(
                0x80,
                &[
                    ship.player_id().to_be_bytes().as_slice(),
                    &[1],
                    &0x200u32.to_be_bytes()
                ]
                .concat()
            )
            .is_err());
        ship.sector = ship.sectors.sector(1076).unwrap();
        assert!(ship
            .workbench_input(
                0x79,
                &[ship.player_id().to_be_bytes(), 4u32.to_be_bytes()].concat()
            )
            .is_err());
        assert_eq!(ship.inventory, original);
    }

    #[test]
    fn refinery_restart_uses_inventory_job_and_rejects_replayed_request_after_completion() {
        let mut ship = refinery_ship();
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22092".parse().unwrap());
        start_refine(&mut ship, 100, false);
        make_refine_due(&mut ship);
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert_eq!(restored.session_epoch, ship.session_epoch);
        restored.advance_production(0.1).unwrap();
        let after = restored.inventory.clone();
        let mut repeated =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(repeated.inventory.refine_job.is_none());
        assert_eq!(repeated.inventory, after);
        repeated.inputs.push_back(100);
        assert!(repeated
            .workbench_input(
                0x7e,
                &[repeated.player_id().to_be_bytes(), 2u32.to_be_bytes()].concat()
            )
            .is_err());
        assert!(repeated.inventory.refine_job.is_none());
        start_refine(&mut repeated, 101, false);
        assert!(repeated.inventory.refine_job.is_some());
    }

    #[test]
    fn refinery_cancellation_missing_materials_and_disk_failure_do_not_consume_or_duplicate() {
        let mut ship = refinery_ship();
        let original = ship.inventory.clone();
        start_refine(&mut ship, 1, false);
        ship.workbench_input(
            0x7e,
            &[ship.player_id().to_be_bytes(), 0u32.to_be_bytes()].concat(),
        )
        .unwrap();
        assert!(ship.inventory.refine_job.is_none());
        assert_eq!(ship.inventory.cargo, original.cargo);
        assert_eq!(ship.inventory.credits, original.credits);
        ship.workbench_input(
            0x79,
            &[ship.player_id().to_be_bytes(), 4u32.to_be_bytes()].concat(),
        )
        .unwrap();
        ship.workbench_input(
            0x7c,
            &[ship.player_id().to_be_bytes(), 3381u32.to_be_bytes()].concat(),
        )
        .unwrap();
        start_refine(&mut ship, 2, false);
        make_refine_due(&mut ship);
        let before = ship.inventory.clone();
        let directory = ship.state_dir.clone();
        let blocked = directory.join("blocked");
        fs::write(&blocked, b"file not directory").unwrap();
        ship.state_dir = blocked;
        assert!(ship.advance_production(0.1).is_err());
        assert_eq!(ship.inventory, before);
        ship.state_dir = directory;
        ship.advance_production(0.1).unwrap();
        assert_eq!(ship.inventory.cargo[0].as_ref().unwrap().quantity, 10);
        start_refine(&mut ship, 3, false);
        make_refine_due(&mut ship);
        ship.inventory.cargo[0] = None; // Simulate a separate allowed cargo mutation.
        let credits = ship.inventory.credits;
        ship.advance_production(0.1).unwrap();
        assert!(ship.inventory.refine_job.is_none());
        assert_eq!(ship.inventory.credits, credits);
        let mut candidate = ship.inventory.clone();
        candidate.cargo[0] = Some(crate::inventory::ItemInstance {
            template: 2845,
            quantity: 5,
            quality: 1.,
            structure: 1.,
            builder_name: String::new(),
        });
        ship.commit_inventory(candidate).unwrap();
        start_refine(&mut ship, 4, false);
        make_refine_due(&mut ship);
        let mut candidate = ship.inventory.clone();
        candidate.refine_job.as_mut().unwrap().product = 769; // Item exists, recipe was removed/changed.
        candidate.refine_job.as_mut().unwrap().recipe = None;
        ship.commit_inventory(candidate).unwrap();
        ship.advance_production(0.1).unwrap();
        assert!(ship.inventory.refine_job.is_none());
        assert_eq!(ship.inventory.cargo[0].as_ref().unwrap().quantity, 5);
        assert_eq!(ship.inventory.credits, credits);
        start_refine(&mut ship, 5, false);
        make_refine_due(&mut ship);
        let mut candidate = ship.inventory.clone();
        candidate
            .refine_job
            .as_mut()
            .unwrap()
            .recipe
            .as_mut()
            .unwrap()
            .input_quantity = 6;
        ship.commit_inventory(candidate).unwrap();
        ship.advance_production(0.1).unwrap();
        assert!(ship.inventory.refine_job.is_none());
        assert_eq!(ship.inventory.cargo[0].as_ref().unwrap().quantity, 5);
        assert_eq!(ship.inventory.credits, credits);
    }

    fn looting_ship() -> PlayerSession {
        let mut ship = session();
        ship.logged_in = true;
        let catalog = crate::loot::LootCatalog::from_mobs(
            vec![crate::loot::MobDrops {
                id: 99,
                kind: Some(1),
                drops: vec![crate::loot::DropEntry {
                    source_id: 1,
                    item: 2837,
                    chance_percent: 100.,
                    quantity: 1,
                    kind: 0,
                    usage_chance: 0,
                }],
            }],
            &ship.items,
        )
        .unwrap();
        let id = lock_loot(&ship.loot_world)
            .unwrap()
            .spawn(
                crate::loot_world::CorpseBirth {
                    death: crate::loot_world::MobDeath {
                        spawn: 999,
                        generation: 1,
                    },
                    template: 99,
                    name: "Test drone".into(),
                    level: 1,
                    killer: ship.player_id(),
                    position: [ship.position[0] + 1000., ship.position[1], ship.position[2]],
                    now_ms: crate::resources::unix_ms(),
                },
                &catalog,
                &ship.items,
            )
            .unwrap()
            .unwrap();
        ship.publish_corpses().unwrap();
        ship.target = id;
        ship
    }

    #[test]
    fn corpse_credit_permission_tractor_and_depletion_commit_once() {
        let mut ship = looting_ship();
        let corpse = ship.target;
        let credits = ship.inventory.credits;
        ship.open_loot().unwrap();
        assert!(ship.inventory.credits >= credits + 20);
        let after_open = ship.inventory.clone();
        ship.open_loot().unwrap();
        assert_eq!(ship.inventory, after_open);
        assert!(ship
            .pending
            .iter()
            .any(|frame| frame.opcode == 0x8c && frame.body == corpse.to_be_bytes()));
        ship.begin_looting(0).unwrap();
        assert!(ship.pending.iter().any(|frame| frame.opcode == 0x2014));
        assert!(ship.inventory.cargo.iter().all(Option::is_none));
        ship.advance_looting(100.).unwrap();
        assert!(ship.loot_cast.is_none());
        assert_eq!(ship.inventory.cargo[0].as_ref().unwrap().template, 2837);
        assert_eq!(ship.inventory.cargo[0].as_ref().unwrap().quantity, 1);
        ship.last_loot_publish = None;
        ship.publish_corpses().unwrap();
        assert!(!ship.visible_corpses.contains_key(&corpse));
        assert_eq!(ship.loot_window, None);
        assert!(ship.begin_looting(0).is_err());
    }

    #[test]
    fn loot_claim_is_shared_and_private_open_checks_current_killer() {
        let mut ship = looting_ship();
        let mut other = session();
        other.account.character.game_id += 1;
        other.logged_in = true;
        other
            .attach_loot_world(Arc::clone(&ship.loot_world))
            .unwrap();
        other.publish_corpses().unwrap();
        other.target = ship.target;
        assert!(other.open_loot().is_err());
        ship.open_loot().unwrap(); // The archived opening releases private ownership.
        other.open_loot().unwrap();
        ship.begin_looting(0).unwrap();
        assert!(other.begin_looting(0).is_err());
        ship.cancel_looting("Test cancellation.").unwrap();
        other.begin_looting(0).unwrap();
        other.advance_looting(100.).unwrap();
        assert!(ship.inventory.cargo.iter().all(Option::is_none));
        assert_eq!(other.inventory.cargo[0].as_ref().unwrap().quantity, 1);
    }

    #[test]
    fn loot_restart_resumes_and_discards_already_committed_cast_without_duplicate_award() {
        let mut ship = looting_ship();
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22091".parse().unwrap());
        ship.open_loot().unwrap();
        ship.begin_looting(0).unwrap();
        ship.advance_looting(0.1).unwrap();
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(restored.loot_cast.is_some());
        restored.advance_looting(100.).unwrap();
        let inventory = restored.inventory.clone();
        let repeated =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(repeated.loot_cast.is_none());
        assert_eq!(repeated.inventory, inventory);
        assert!(!lock_loot(&repeated.loot_world)
            .unwrap()
            .get(ship.target)
            .unwrap()
            .available(crate::resources::unix_ms())
            .unwrap());
    }

    #[test]
    fn loot_cancel_and_inventory_write_failure_preserve_stock_and_release_claim() {
        let mut ship = looting_ship();
        ship.open_loot().unwrap();
        let before = ship.inventory.clone();
        ship.begin_looting(0).unwrap();
        ship.position[0] += 10_000.;
        ship.advance_looting(0.1).unwrap();
        assert!(ship.loot_cast.is_none());
        ship.position[0] -= 10_000.;
        ship.begin_looting(0).unwrap();
        let blocked = ship.state_dir.join("blocked");
        fs::write(&blocked, b"file, not directory").unwrap();
        ship.state_dir = blocked;
        ship.advance_looting(100.).unwrap();
        assert_eq!(ship.inventory, before);
        assert!(ship.loot_cast.is_none());
        assert!(lock_loot(&ship.loot_world)
            .unwrap()
            .get(ship.target)
            .unwrap()
            .remaining()
            .unwrap()
            .items[0]
            .is_some());
        ship.begin_looting(0).unwrap();
        ship.cancel_looting("Cleanup.").unwrap();
    }

    fn mining_ship() -> PlayerSession {
        let mut ship = session();
        let mut object = ship.sector.navpoints[0].clone();
        object.id = 0x20000001;
        object.object_type = 38;
        object.position = [100., 0., 0.];
        object.name = "Asteroid (L1)".into();
        object.asset = 1822;
        ship.sector.resources = vec![crate::content::ResourceNode {
            object,
            field_id: 1,
            level: 1,
            resource_type: 2,
            ore_items: vec![crate::content::ResourceOre {
                item_id: 2837,
                frequency: 1.,
            }],
        }];
        ship.resource_field = Arc::new(Mutex::new(
            ResourceField::load(
                &ship.sector,
                &ship.items,
                ship.state_dir.join("resources/1076.json"),
            )
            .unwrap(),
        ));
        ship.logged_in = true;
        ship.target = 0x20000001;
        ship.open_resource_window(ship.target).unwrap();
        ship
    }

    fn mine_slot(ship: &mut PlayerSession, slot: usize) -> io::Result<()> {
        ship.begin_mining(ItemTransfer {
            source: 18,
            source_slot: slot,
            destination: 0,
            destination_slot: 0,
            quantity: 0,
        })
    }

    #[test]
    fn mining_drains_energy_claims_exclusively_and_commits_cargo_with_depletion_and_xp() {
        let mut ship = mining_ship();
        let mut other = mining_ship();
        other.account.character.game_id += 1;
        other
            .attach_resource_field(Arc::clone(&ship.resource_field))
            .unwrap();
        let quantity = lock_resources(&ship.resource_field)
            .unwrap()
            .get(ship.target)
            .unwrap()
            .contents[0]
            .as_ref()
            .unwrap()
            .quantity;
        let initial_energy = ship.energy;
        mine_slot(&mut ship, 0).unwrap();
        assert!(mine_slot(&mut other, 0).is_err());
        ship.advance_mining(0.1).unwrap();
        assert!(ship.energy < initial_energy);
        for _ in 0..100 {
            ship.advance_mining(0.25).unwrap();
        }
        assert!(ship.mining.is_none());
        assert_eq!(
            ship.inventory
                .cargo
                .iter()
                .flatten()
                .filter(|item| item.template == 2837)
                .map(|item| item.quantity)
                .sum::<u32>(),
            quantity
        );
        assert_eq!(
            ship.inventory.harvests[&ship.target].quantities[0],
            quantity
        );
        assert!(ship.inventory.explore_xp > 0);
        assert!(lock_resources(&ship.resource_field)
            .unwrap()
            .get(ship.target)
            .unwrap()
            .contents[0]
            .is_none());
        let saved: Inventory =
            serde_json::from_slice(&fs::read(ship.state_dir.join("inventory.json")).unwrap())
                .unwrap();
        assert_eq!(saved, ship.inventory);
        assert!(mine_slot(&mut other, 0).is_err()); // no double award from another client
    }

    #[test]
    fn mining_restart_resumes_then_rejects_a_stale_cast_after_durable_collection() {
        let mut ship = mining_ship();
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22090".parse().unwrap());
        mine_slot(&mut ship, 0).unwrap();
        ship.advance_mining(0.1).unwrap();
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(restored.mining.is_some());
        for _ in 0..100 {
            restored.advance_mining(0.25).unwrap();
        }
        let collected = restored.inventory.clone();
        assert!(collected.harvests[&ship.target].quantities[0] > 0);
        // Keep the OLD transport checkpoint, simulating a crash immediately
        // after inventory replacement but before the next session checkpoint.
        let repeated =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(repeated.mining.is_none());
        assert_eq!(repeated.inventory, collected);
        assert!(lock_resources(&repeated.resource_field)
            .unwrap()
            .get(ship.target)
            .unwrap()
            .contents[0]
            .is_none());
    }

    #[test]
    fn mining_cancellation_and_disk_failure_leave_ore_unawarded_and_release_claims() {
        let mut ship = mining_ship();
        let before = ship.inventory.clone();
        mine_slot(&mut ship, 0).unwrap();
        ship.motion.set_speed = 10.;
        ship.advance_mining(0.1).unwrap();
        assert!(ship.mining.is_none());
        assert_eq!(ship.inventory, before);
        ship.motion.set_speed = 0.;
        mine_slot(&mut ship, 0).unwrap();
        ship.advance_mining(100.).unwrap(); // enter tractor phase
        fs::create_dir_all(&ship.state_dir).unwrap();
        let blocked = ship.state_dir.join("blocked");
        fs::write(&blocked, b"file, not directory").unwrap();
        ship.state_dir = blocked;
        ship.advance_mining(100.).unwrap();
        assert!(ship.mining.is_none());
        assert_eq!(ship.inventory, before);
        assert!(lock_resources(&ship.resource_field)
            .unwrap()
            .get(ship.target)
            .unwrap()
            .contents[0]
            .is_some());
        mine_slot(&mut ship, 0).unwrap(); // failed collection released its claim
        ship.cancel_mining("Test cleanup.").unwrap();
    }

    #[test]
    fn native_gate_and_dock_verb_ids_enable_in_range_and_refresh_without_selection() {
        let mut ship = session();
        ship.logged_in = true;
        let gate = ship
            .sector
            .navpoints
            .iter()
            .find(|nav| nav.object_type == 11)
            .unwrap()
            .clone();
        ship.target = gate.id;
        ship.position = [
            gate.position[0] - 10_000.,
            gate.position[1],
            gate.position[2],
        ];
        ship.refresh_target_verbs(true);
        assert_eq!(
            ship.pending.last().unwrap(),
            &packets::target_verbs(gate.id, &[(10, 2)])
        );
        ship.position = gate.position;
        ship.advance_player(0.).unwrap();
        assert_eq!(
            ship.pending.last().unwrap(),
            &packets::target_verbs(gate.id, &[(10, 0)])
        );
        ship.pending.clear();
        ship.advance_player(0.).unwrap();
        assert!(!ship.pending.iter().any(|frame| frame.opcode == 0x5c));
        ship.sector = ship.sectors.sector(1076).unwrap();
        let station = ship
            .sector
            .navpoints
            .iter()
            .find(|nav| nav.object_type == 12)
            .unwrap()
            .clone();
        ship.target = station.id;
        ship.position = station.position;
        ship.refresh_target_verbs(true);
        assert_eq!(
            ship.pending.last().unwrap(),
            &packets::target_verbs(station.id, &[(8, 0)])
        );
    }

    #[test]
    fn station_peers_spawn_once_move_on_change_leave_rooms_and_resume_without_duplicates() {
        let mut viewer = session();
        let mut peer = session();
        viewer.sector = viewer.sectors.sector(10761).unwrap();
        peer.sector = viewer.sector.clone();
        viewer.logged_in = true;
        peer.logged_in = true;
        peer.account.character.game_id += 1;
        let pose = StationPose {
            room: 0,
            orientation: 0.5,
            position: [10., 20., 0.],
            action: 1,
        };
        viewer.station_pose = Some(pose.clone());
        peer.station_pose = Some(pose.clone());
        let snapshot = peer.station_snapshot().unwrap();
        viewer.pending.clear();
        viewer.publish_station_peers(std::slice::from_ref(&snapshot));
        assert_eq!(
            viewer
                .pending
                .iter()
                .map(|frame| frame.opcode)
                .collect::<Vec<_>>(),
            [0x61, 0xa0, 0x9e]
        );
        assert_eq!(viewer.station_peers.get(&peer.player_id()), Some(&pose));
        viewer.pending.clear();
        viewer.publish_station_peers(std::slice::from_ref(&snapshot));
        assert!(viewer.pending.is_empty());
        peer.station_pose.as_mut().unwrap().position[0] = 15.;
        let changed = peer.station_snapshot().unwrap();
        viewer.last_station_broadcast = Instant::now() - Duration::from_secs(1);
        viewer.publish_station_peers(std::slice::from_ref(&changed));
        assert_eq!(
            viewer
                .pending
                .iter()
                .map(|frame| frame.opcode)
                .collect::<Vec<_>>(),
            [0x9e]
        );
        viewer.pending.clear();
        viewer.selected_peer = Some("127.0.0.1".parse().unwrap());
        viewer.endpoint = Some("127.0.0.1:22050".parse().unwrap());
        viewer.save().unwrap();
        viewer.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(viewer.sector.clone(), viewer.bind, viewer.state_dir.clone())
                .unwrap();
        restored.pending.clear();
        restored.publish_station_peers(std::slice::from_ref(&changed));
        assert!(restored.pending.is_empty());
        peer.station_pose.as_mut().unwrap().room = 1;
        restored.publish_station_peers(&[peer.station_snapshot().unwrap()]);
        assert_eq!(restored.pending.len(), 1);
        assert_eq!(
            restored.pending[0],
            packets::station_avatar_room(peer.player_id(), u32::MAX, 0)
        );
        assert!(restored.station_peers.is_empty());
        restored.pending.clear();
        restored.station_view_dirty = true;
        restored.publish_station_peers(&[changed]);
        assert_eq!(
            restored
                .pending
                .iter()
                .map(|frame| frame.opcode)
                .collect::<Vec<_>>(),
            [0x61, 0xa0, 0x9e]
        );
        let old_ship_position = restored.position;
        restored
            .handle_input(
                0x9f,
                &[
                    restored.player_id().to_le_bytes(),
                    1u32.to_le_bytes(),
                    0u32.to_le_bytes(),
                ]
                .concat(),
            )
            .unwrap();
        assert!(restored.station_pose.is_none()); // await new-room coordinates
        restored.pending.clear();
        restored.publish_station_peers(&[]);
        assert!(restored.station_peers.is_empty());
        assert_eq!(restored.position, old_ship_position);
    }

    fn npc_choice(ship: &mut PlayerSession, choice: u8) -> io::Result<()> {
        ship.talk_choice(&[ship.player_id().to_le_bytes().as_slice(), &[choice]].concat())
    }

    #[test]
    fn npc_trades_require_station_offered_branch_and_live_vendor_and_persist_together() {
        let mut ship = session();
        ship.logged_in = true;
        assert!(ship.open_npc(16).is_err()); // space
        ship.sector = ship.sectors.sector(10761).unwrap();
        assert!(ship.open_npc(9).is_err()); // another station
        assert!(npc_choice(&mut ship, 2).is_err()); // no dialogue
        ship.open_npc(16).unwrap();
        assert!(npc_choice(&mut ship, 99).is_err());
        assert!(ship
            .talk_choice(&[(ship.player_id() + 1).to_le_bytes().as_slice(), &[2]].concat())
            .is_err());
        let transfer = ItemTransfer {
            source: 4,
            source_slot: 0,
            destination: 1,
            destination_slot: u32::MAX as usize,
            quantity: 1,
        };
        assert!(ship.inventory_candidate(transfer).is_err()); // greeting is not trade
        npc_choice(&mut ship, 2).unwrap();
        assert!(ship.npc_interaction.as_ref().unwrap().vendor);
        let offers = ship
            .npcs
            .group(ship.npcs.npc(16, 10761).unwrap())
            .unwrap()
            .inventory(&ship.items)
            .unwrap();
        let offer = offers[0];
        ship.inventory.credits = 10_000_000;
        let before = ship.inventory.credits;
        ship.transfer_inventory(transfer).unwrap();
        assert_eq!(
            ship.inventory.cargo[0].as_ref().unwrap().template,
            offer.item
        );
        assert_eq!(ship.inventory.credits, before - offer.price);
        let saved: Inventory =
            serde_json::from_slice(&fs::read(ship.state_dir.join("inventory.json")).unwrap())
                .unwrap();
        assert_eq!(saved, ship.inventory);
        let price = ship
            .npcs
            .group(ship.npcs.npc(16, 10761).unwrap())
            .unwrap()
            .buy_price(offer.item, &ship.items)
            .unwrap();
        assert!(price > 0);
        ship.transfer_inventory(ItemTransfer {
            source: 1,
            source_slot: 0,
            destination: 4,
            destination_slot: 0,
            quantity: 1,
        })
        .unwrap();
        assert!(ship.inventory.cargo[0].is_none());
        assert_eq!(ship.inventory.credits, before - offer.price + price);
        npc_choice(&mut ship, 230).unwrap();
        assert!(ship.npc_interaction.is_none());
        assert!(ship.inventory_candidate(transfer).is_err());
        assert!(ship
            .pending
            .iter()
            .any(|frame| frame.opcode == 0x56 && frame.body == (-32i32).to_le_bytes()));
        ship.sector = ship.sectors.sector(12150).unwrap();
        ship.open_npc(446).unwrap();
        npc_choice(&mut ship, 3).unwrap(); // public trade tree, missing vendor group
        assert!(!ship.npc_interaction.as_ref().unwrap().vendor);
        assert!(ship.inventory_candidate(transfer).is_err());
        npc_choice(&mut ship, 0).unwrap();
        assert!(ship.npc_interaction.is_none());
    }

    #[test]
    fn active_vendor_survives_restart_but_cannot_be_used_in_another_station() {
        let mut ship = session();
        ship.sector = ship.sectors.sector(10761).unwrap();
        ship.logged_in = true;
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:21002".parse().unwrap());
        ship.open_npc(16).unwrap();
        npc_choice(&mut ship, 2).unwrap();
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(restored.logged_in && restored.npc_interaction.as_ref().unwrap().vendor);
        let transfer = ItemTransfer {
            source: 4,
            source_slot: 0,
            destination: 1,
            destination_slot: 0,
            quantity: 1,
        };
        restored.inventory.credits = 10_000_000;
        assert!(restored.inventory_candidate(transfer).is_ok());
        restored.sector = restored.sectors.sector(17051).unwrap();
        assert!(restored.inventory_candidate(transfer).is_err());
        restored.restore_npc_service().unwrap();
        assert!(restored.npc_interaction.is_none());
    }

    #[test]
    fn duplicate_vendor_udp_is_ignored_before_and_after_checkpoint_restore() {
        let mut ship = session();
        ship.sector = ship.sectors.sector(10761).unwrap();
        ship.logged_in = true;
        let endpoint: SocketAddr = "127.0.0.1:21003".parse().unwrap();
        ship.endpoint = Some(endpoint);
        ship.selected_peer = Some(endpoint.ip());
        ship.open_npc(16).unwrap();
        npc_choice(&mut ship, 2).unwrap();
        ship.inventory.credits = 10_000_000;
        let request = Datagram {
            opcode: 0x27,
            player: ship.player_id(),
            sequence: 50,
            body: [ship.player_id(), 4, 0, 1, u32::MAX, 1]
                .into_iter()
                .flat_map(u32::to_be_bytes)
                .collect(),
        };
        ship.handle_udp(SECTOR_PORT, endpoint, request.clone())
            .unwrap();
        let after = ship.inventory.clone();
        ship.handle_udp(SECTOR_PORT, endpoint, request.clone())
            .unwrap();
        assert_eq!(ship.inventory, after);
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        restored.handle_udp(SECTOR_PORT, endpoint, request).unwrap();
        assert_eq!(restored.inventory, after);
    }

    #[test]
    fn failed_vendor_disk_commit_leaves_cargo_credits_and_success_frames_unchanged() {
        let mut ship = session();
        ship.sector = ship.sectors.sector(10761).unwrap();
        ship.logged_in = true;
        ship.open_npc(16).unwrap();
        npc_choice(&mut ship, 2).unwrap();
        ship.inventory.credits = 10_000_000;
        fs::create_dir_all(&ship.state_dir).unwrap();
        let blocked = ship.state_dir.join("blocked");
        fs::write(&blocked, b"file, not directory").unwrap();
        ship.state_dir = blocked;
        ship.pending.clear();
        let before = ship.inventory.clone();
        let transfer = ItemTransfer {
            source: 4,
            source_slot: 0,
            destination: 1,
            destination_slot: 0,
            quantity: 1,
        };
        assert!(ship.transfer_inventory(transfer).is_err());
        assert_eq!(ship.inventory, before);
        assert!(ship.pending.is_empty());
    }

    #[test]
    fn restart_retains_known_peer_actors_without_duplicate_creates() {
        let mut ship = session();
        let mut other = session();
        ship.logged_in = true;
        other.logged_in = true;
        other.account.character.game_id += 1;
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:21001".parse().unwrap());
        let peer = other.peer_snapshot().unwrap();
        ship.peer_ids.insert(other.player_id());
        ship.publish_peers(std::slice::from_ref(&peer));
        assert!(ship.pending.iter().any(|frame| frame.opcode == 4));
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        restored.pending.clear();
        restored.publish_peers(&[peer]);
        assert!(!restored.pending.iter().any(|frame| frame.opcode == 4));
        assert!(restored.visible_peers.contains(&other.player_id()));
    }

    #[test]
    fn beams_use_equipped_damage_energy_and_reload_without_toggle_bypass() {
        let mut ship = session();
        ship.logged_in = true;
        ship.inventory.equipped[4] = ship.inventory.equipped[3].clone();
        ship.refresh_equipment().unwrap();
        ship.target = MOB_ID;
        let shared = Arc::clone(&ship.combat);
        lock_combat(&shared).unwrap().actors[0].position = [100., 0., 0.];
        let before = lock_combat(&shared).unwrap().actors[0].shield;
        ship.toggle_all_weapons();
        ship.fire_weapons_with_roll(|| 5000).unwrap();
        assert_eq!(lock_combat(&shared).unwrap().actors[0].shield, before - 18.);
        assert_eq!(ship.energy, 120.);
        assert_eq!(ship.weapon_cooldowns[3], 2.);
        ship.toggle_all_weapons();
        ship.toggle_all_weapons();
        ship.fire_weapons_with_roll(|| 5000).unwrap();
        assert_eq!(ship.energy, 120.); // Toggling does not reset either reload.
        lock_combat(&shared).unwrap().take_damage_reports();
        ship.advance_player(1.).unwrap();
        assert_eq!(lock_combat(&shared).unwrap().actors[0].shield, before - 18.);
        ship.advance_player(1.).unwrap();
        let reports = lock_combat(&shared).unwrap().take_damage_reports();
        assert_eq!(reports.len(), 2);
        assert!(reports
            .iter()
            .all(|report| matches!((report.damage, report.inflicted), (0. | 9., 0) | (18., 3))));
        let damage: f32 = reports.iter().map(|report| report.damage).sum();
        assert_eq!(
            lock_combat(&shared).unwrap().actors[0].shield,
            before - 18. - damage
        );
        assert_eq!(ship.inventory.shot_sequence, 4);
        ship.player_firing = false;
        ship.weapon_firing.fill(false);
        let activation = [ship.player_id().to_le_bytes().as_slice(), &[2, 3]].concat();
        ship.activate_equipped_item(&activation).unwrap();
        assert!(ship.weapon_firing[3]);
        assert!(!ship.weapon_firing[4]);
    }

    #[test]
    fn reactor_and_engine_removal_affect_regeneration_movement_and_warp() {
        let mut ship = session();
        ship.logged_in = true;
        ship.motion.thrust(2).unwrap();
        ship.advance_player(1.).unwrap();
        assert!(ship.position[0] > 0.);
        let stopped = ship.position;
        ship.unequip("2").unwrap();
        assert_eq!(ship.motion.max_speed, 0.);
        assert_eq!(ship.motion.speed, 0.);
        ship.advance_player(1.).unwrap();
        assert_eq!(ship.position, stopped);
        assert!(ship
            .warp
            .start(VecDeque::new(), ship.position, &mut ship.motion)
            .is_err());
        ship.equip_cargo("0 2").unwrap();
        assert_eq!(ship.motion.max_speed, 192.);
        assert_eq!(ship.warp.speed, 2700.);
        ship.unequip("1").unwrap();
        assert_eq!(ship.energy, 0.);
        ship.advance_player(1.).unwrap();
        assert_eq!(ship.energy, 0.);
        ship.equip_cargo("0 1").unwrap();
        ship.advance_player(1.).unwrap();
        assert!((ship.energy - 5.4).abs() < 0.001);
        ship.shield = 0.;
        ship.advance_player(1.).unwrap();
        assert!((ship.shield - 1.8).abs() < 0.001);
        ship.energy = 0.1;
        ship.warp
            .start(VecDeque::new(), ship.position, &mut ship.motion)
            .unwrap();
        ship.warp
            .advance(
                &mut ship.position,
                &mut ship.motion,
                &ship.sector.bounds,
                2.1,
            )
            .unwrap();
        ship.advance_player(0.1).unwrap();
        assert_eq!(ship.energy, 0.);
        assert!(!ship.warp.active());
    }

    #[test]
    fn restart_preserves_weapon_firing_and_reload_and_reconciles_derived_parameters() {
        let mut ship = session();
        ship.logged_in = true;
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:21001".parse().unwrap());
        ship.weapon_firing[3] = true;
        ship.player_firing = true;
        ship.weapon_cooldowns[3] = 1.5;
        ship.checkpoint_session(true).unwrap();
        let restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(restored.logged_in);
        assert!(restored.weapon_firing[3]);
        assert!(restored.player_firing);
        assert_eq!(restored.weapon_cooldowns[3], 1.5);
        assert_eq!(restored.motion.max_speed, 192.);
        assert_eq!(restored.warp.speed, 2700.);
    }

    #[test]
    fn denied_equipment_moves_explain_failure_and_resync_without_saving() {
        let mut ship = session();
        ship.logged_in = true;
        let before = ship.inventory.clone();
        let request = [ship.player_id(), 2, 2, 2, 0, 1]
            .into_iter()
            .flat_map(u32::to_be_bytes)
            .collect::<Vec<_>>();
        ship.move_inventory(&request).unwrap();
        assert_eq!(ship.inventory, before);
        assert!(!ship.state_dir.join("inventory.json").exists());
        assert_eq!(ship.pending[0].opcode, 0x1d);
        assert_eq!(
            ship.pending
                .iter()
                .filter(|frame| frame.opcode == 0x1b)
                .count(),
            3
        );
    }

    #[test]
    fn inventory_packets_validate_ownership_and_persist_before_publishing() {
        let mut ship = session();
        ship.logged_in = true;
        let original = ship.inventory.clone();
        let packet = |owner: u32| {
            [owner, 2, 2, 1, 0, 1]
                .into_iter()
                .flat_map(u32::to_be_bytes)
                .collect::<Vec<_>>()
        };
        assert!(ship.move_inventory(&packet(ship.player_id() + 1)).is_err());
        assert!(ship
            .move_inventory(&packet(ship.player_id())[..23])
            .is_err());
        assert_eq!(ship.inventory, original);
        // Simulate a storage failure without editing any real character data.
        fs::create_dir_all(&ship.state_dir).unwrap();
        let blocker = ship.state_dir.join("inventory.json.tmp");
        fs::create_dir(&blocker).unwrap();
        assert!(ship.move_inventory(&packet(ship.player_id())).is_err());
        assert_eq!(ship.inventory, original);
        assert!(ship.pending.is_empty());
        fs::remove_dir(blocker).unwrap();
        ship.move_inventory(&packet(ship.player_id())).unwrap();
        assert!(ship.inventory.equipped[2].is_none());
        assert_eq!(ship.inventory.cargo[0], original.equipped[2]);
        assert_eq!(ship.pending.last().unwrap().opcode, 0x1b);
        let restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert_eq!(restored.inventory, ship.inventory);
        ship.hull = 0.;
        assert!(ship.move_inventory(&packet(ship.player_id())).is_err());
    }

    #[test]
    fn abilities_validate_targets_charge_once_and_apply_cloak_and_leech() {
        let mut session = session();
        session.logged_in = true;
        let before = session.energy;
        session.target = u32::MAX;
        assert!(session.begin_ability(106).is_err());
        assert_eq!(session.energy, before); // invalid target never spends energy
        session.begin_ability(28).unwrap();
        assert!(session.begin_ability(28).is_err()); // one cast at a time
        for _ in 0..32 {
            session.advance_abilities(0.1).unwrap();
        }
        assert_eq!(session.skills.cloak_rank, 1);
        session.begin_ability(28).unwrap(); // toggling off bypasses cooldown/cost
        assert_eq!(session.skills.cloak_rank, 0);
        session.target = MOB_ID;
        lock_combat(&session.combat).unwrap().actors[0].position = [100.; 3];
        let hull = lock_combat(&session.combat).unwrap().actors[0].health;
        let shield = lock_combat(&session.combat).unwrap().actors[0].shield;
        session.begin_ability(106).unwrap();
        for _ in 0..12 {
            session.advance_abilities(0.1).unwrap();
        }
        assert_eq!(
            lock_combat(&session.combat).unwrap().actors[0].shield,
            shield - 75.
        );
        assert_eq!(lock_combat(&session.combat).unwrap().actors[0].health, hull);
        assert!(session.begin_ability(106).is_err()); // shared skill recovery
    }

    #[test]
    fn fold_space_moves_five_kilometers_and_wormhole_persists_destination() {
        let mut session = session();
        session.logged_in = true;
        session.begin_ability(126).unwrap();
        for _ in 0..12 {
            session.advance_abilities(0.1).unwrap();
        }
        assert!((distance(session.position, [0.; 3]) - 5000.).abs() < 1.);
        session.begin_ability(67).unwrap();
        for _ in 0..32 {
            session.advance_abilities(0.1).unwrap();
        }
        assert_eq!(session.sector.id, 1910);
        assert!(!session.logged_in);
        assert!(session.pending.iter().any(|frame| frame.opcode == 0x3a));
        let restored = PlayerSession::new(
            serde_json::from_str(include_str!("../data/sector.json")).unwrap(),
            session.bind,
            session.state_dir.clone(),
        )
        .unwrap();
        assert_eq!(restored.sector.id, 1910);
    }

    #[test]
    fn restart_preserves_udp_identity_sequence_clock_and_active_warp() {
        let mut original = session();
        original.logged_in = true;
        original.selected_peer = Some("127.0.0.1".parse().unwrap());
        original.endpoint = Some("127.0.0.1:23456".parse().unwrap());
        original.comms = Some("127.0.0.1:23457".parse().unwrap());
        original.sequence = 318;
        original.inputs.push_back(300);
        original
            .handle_input(
                0x9b,
                &[
                    FIRST_PLAYER_ID.to_le_bytes().as_slice(),
                    &0u16.to_le_bytes(),
                ]
                .concat(),
            )
            .unwrap();
        original.tick_offset = 100_000;
        original.checkpoint_session(true).unwrap();
        let mut replacement = PlayerSession::new(
            original.sector.clone(),
            original.bind,
            original.state_dir.clone(),
        )
        .unwrap();
        assert!(replacement.logged_in);
        assert_eq!(replacement.endpoint, original.endpoint);
        assert_eq!(replacement.comms, original.comms);
        assert_eq!(replacement.sequence, 318);
        assert!(replacement.tick_ms() >= 100_000);
        assert_eq!(replacement.warp.phase, WarpPhase::Charging);
        // The same socket can cancel without a new handoff or authentication.
        replacement
            .handle_udp(
                SECTOR_PORT,
                original.endpoint.unwrap(),
                Datagram {
                    opcode: 0x14,
                    player: FIRST_PLAYER_ID,
                    sequence: 301,
                    body: [FIRST_PLAYER_ID.to_le_bytes().as_slice(), &[4]].concat(),
                },
            )
            .unwrap();
        assert_eq!(replacement.warp.phase, WarpPhase::Recovering);
        replacement.logout().unwrap();
        assert!(!replacement.state_dir.join("session.local.json").exists());
        let clean = PlayerSession::new(
            replacement.sector.clone(),
            replacement.bind,
            replacement.state_dir.clone(),
        )
        .unwrap();
        assert!(!clean.logged_in);
    }

    #[test]
    fn selecting_a_gate_updates_the_ship_target_without_resetting_its_state() {
        let mut session = session();
        session.logged_in = true;
        let target = session
            .sector
            .navpoints
            .iter()
            .find(|n| n.object_type == 11)
            .unwrap()
            .id;
        let request = [FIRST_PLAYER_ID.to_le_bytes(), target.to_le_bytes()].concat();
        session.handle_input(0x17, &request).unwrap();
        assert_eq!(session.target, target);
        let display = session.pending.iter().find(|f| f.opcode == 0x19).unwrap();
        assert_eq!(
            display.body,
            [target.to_le_bytes(), u32::MAX.to_le_bytes()].concat()
        );
        let actor = session.pending.iter().find(|f| f.opcode == 0x1b).unwrap();
        assert_eq!(&actor.body[..4], &FIRST_PLAYER_ID.to_le_bytes());
        assert_eq!(&actor.body[7..15], &[2, 0, 0, 128, 0, 0, 0, 0]);
        assert_eq!(&actor.body[15..], &target.to_le_bytes());
        assert!(session.pending.iter().any(|f| f.opcode == 0x5c));
    }

    #[test]
    fn warp_request_supports_free_flight_and_stops_without_resetting_inventory() {
        let mut session = session();
        session.logged_in = true;
        let request = [
            FIRST_PLAYER_ID.to_le_bytes().as_slice(),
            &0u16.to_le_bytes(),
        ]
        .concat();
        session.handle_input(0x9b, &request).unwrap();
        assert_eq!(session.warp.phase, WarpPhase::Charging);
        assert!(session.pending.iter().any(|f| f.opcode == 9));
        for frame in session.pending.iter().filter(|f| f.opcode == 0x1b) {
            assert_eq!(frame.body[7], 2); // one-bit diff: no default resets
        }
        session.handle_input(0x9b, &request).unwrap();
        assert_eq!(session.warp.phase, WarpPhase::Recovering);
        assert!(session.warp_effects.is_empty());
        assert!(session.pending.iter().any(|f| f.opcode == 0xf));
        session.handle_input(0x9b, &request).unwrap();
        assert_eq!(session.warp.phase, WarpPhase::Recovering);
        session.logout().unwrap();
        assert_eq!(session.warp.phase, WarpPhase::Idle);
    }

    #[test]
    fn real_sector_content_has_station_gates_and_valid_geometry() {
        let sector: Sector =
            serde_json::from_str(include_str!("../data/asteroid_belt_alpha.json")).unwrap();
        sector.validate().unwrap();
        assert_eq!(sector.name, "Asteroid Belt Alpha");
        assert!(sector.navpoints.len() > 400);
        assert!(sector
            .navpoints
            .iter()
            .any(|n| n.name == "ECS Dauntless" && n.object_type == 12));
        assert!(sector
            .navpoints
            .iter()
            .any(|n| n.gate_destination == Some(1060)));
        for nav in &sector.navpoints {
            assert!((nav.orientation.iter().map(|v| v * v).sum::<f32>() - 1.).abs() < 0.001);
        }
    }

    #[test]
    fn impulse_commands_require_owner_and_fixed_reports_require_registered_endpoint() {
        let mut session = session();
        let peer: SocketAddr = "127.0.0.1:23000".parse().unwrap();
        session.selected_peer = Some(peer.ip());
        session.comms = Some(peer);
        session.logged_in = true;
        let mut command = Vec::from((FIRST_PLAYER_ID + 1).to_le_bytes());
        command.push(2);
        assert!(session.handle_input(0x14, &command).is_err());
        command[..4].copy_from_slice(&FIRST_PLAYER_ID.to_le_bytes());
        session.handle_input(0x14, &command).unwrap();
        for _ in 0..10 {
            session.advance(0.1).unwrap();
        }
        assert!(session.position[0] > 30.);
        let previous = session.position;
        let packet = Datagram {
            opcode: 0x1004,
            player: FIRST_PLAYER_ID,
            sequence: 0,
            body: [50f32, 10., 0., 0., 1., 0.]
                .into_iter()
                .flat_map(f32::to_le_bytes)
                .collect(),
        };
        session
            .handle_udp(3806, "127.0.0.1:23001".parse().unwrap(), packet.clone())
            .unwrap();
        assert_eq!(session.position, previous);
        session.handle_udp(3806, peer, packet).unwrap();
        assert_eq!(session.position, [50., 10., 0.]);
        assert!(session.motion.heading()[1] > 0.99);
        let timestamped = Datagram {
            opcode: 0x1004,
            player: FIRST_PLAYER_ID,
            sequence: 0,
            body: [60f32, 10., 0., 0., 1., 0.]
                .into_iter()
                .flat_map(f32::to_le_bytes)
                .chain(1234u32.to_le_bytes())
                .collect(),
        };
        session.handle_udp(3806, peer, timestamped).unwrap();
        assert_eq!(session.position, [60., 10., 0.]);
        session
            .handle_input(
                0x14,
                &[FIRST_PLAYER_ID.to_le_bytes().as_slice(), &[4]].concat(),
            )
            .unwrap();
        for _ in 0..20 {
            session.advance(0.1).unwrap();
        }
        assert_eq!(session.motion.speed, 0.);
    }
    pub(super) fn session() -> PlayerSession {
        PlayerSession::new(
            serde_json::from_str(include_str!("../data/sector.json")).unwrap(),
            Ipv4Addr::new(127, 0, 0, 2),
            std::env::temp_dir().join(format!("enb-rust-test-{}", rand::random::<u64>())),
        )
        .unwrap()
    }
    #[test]
    fn resources_stream_once_and_leave_visibility_without_duplicate_creation() {
        let mut ship = session();
        ship.logged_in = true;
        let mut node = ship.sector.navpoints[0].clone();
        node.id = 0x20000001;
        node.object_type = 38;
        node.asset = 1822;
        node.name = "Asteroid (L3)".into();
        node.position = [100., 0., 0.];
        ship.sector.resources.push(crate::content::ResourceNode {
            object: node,
            field_id: 1,
            level: 3,
            resource_type: 2,
            ore_items: vec![crate::content::ResourceOre {
                item_id: 2837,
                frequency: 1.,
            }],
        });
        ship.resource_field = Arc::new(Mutex::new(
            ResourceField::load(
                &ship.sector,
                &ship.items,
                ship.state_dir.join("resource-test.json"),
            )
            .unwrap(),
        ));
        ship.publish_resources().unwrap();
        assert!(ship.visible_resources.contains(&0x20000001));
        assert!(ship
            .pending
            .iter()
            .any(|frame| frame.opcode == 0x2018 && frame.body[4] == 38));
        assert!(ship
            .pending
            .iter()
            .any(|frame| frame.opcode == 0x1b && frame.body[..4] == 0x20000001u32.to_le_bytes()));
        let created = ship
            .pending
            .iter()
            .filter(|frame| frame.opcode == 0x2018)
            .count();
        ship.last_resource_visibility = None;
        ship.publish_resources().unwrap();
        assert_eq!(
            ship.pending
                .iter()
                .filter(|frame| frame.opcode == 0x2018)
                .count(),
            created
        );
        assert_eq!(
            ship.warp_waypoint(0x20000001).unwrap().position,
            [100., 0., 0.]
        );
        // A server replacement keeps the client's already-created IDs. Do not
        // send Create again merely because per-process visibility was reset.
        ship.position = [0.; 3];
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22346".parse().unwrap());
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        restored.publish_resources().unwrap();
        assert!(restored.visible_resources.contains(&0x20000001));
        assert!(!restored.pending.iter().any(|frame| frame.opcode == 0x2018));
        ship.position = [50_000., 0., 0.];
        ship.last_resource_visibility = None;
        ship.publish_resources().unwrap();
        assert!(ship.visible_resources.is_empty());
        assert!(ship
            .pending
            .iter()
            .any(|frame| frame.opcode == 7 && frame.body == 0x20000001u32.to_le_bytes()));
        assert!(ship.warp_waypoint(0x20000001).is_err());
    }
    #[test]
    fn self_jumpstart_and_native_tow_recover_incapacitated_ships() {
        let mut ship = session();
        ship.logged_in = true;
        ship.hull = 0.;
        ship.target = ship.player_id();
        ship.begin_ability(65).unwrap();
        for _ in 0..80 {
            ship.advance(0.1).unwrap();
        }
        assert_eq!(ship.hull, ship.equipment.ship.hull_capacity * 0.7);
        assert!(ship.shield > 0. && ship.shield <= ship.equipment.ship.shield_capacity);
        assert!(ship.tow_to_station().is_err());
        ship.hull = 0.;
        let owner = ship.player_id();
        ship.handle_input(0x8d, &owner.to_le_bytes()).unwrap();
        assert!(ship.pending.iter().any(|frame| frame.opcode == 0x54));
        assert!(ship
            .handle_input(0x55, &[0u32.to_le_bytes().as_slice(), &[0]].concat())
            .is_err());
        ship.handle_input(0x55, &[owner.to_le_bytes().as_slice(), &[0]].concat())
            .unwrap();
        assert_eq!(ship.sector.id, 10761);
        assert_eq!(ship.hull, ship.equipment.ship.hull_capacity);
        assert!(ship.pending.iter().any(|frame| frame.opcode == 0x3a));
    }
    #[test]
    fn warp_can_resolve_a_nearby_live_mob_but_rejects_dead_or_unknown_targets() {
        let ship = session();
        let mob = MOB_ID;
        assert!(ship.warp_waypoint(mob).is_err()); // outside the visibility radius
        {
            let mut combat = lock_combat(&ship.combat).unwrap();
            combat.actors[0].position = [10_000., 0., 0.];
        }
        assert_eq!(ship.warp_waypoint(mob).unwrap().stand_off, 800.);
        lock_combat(&ship.combat).unwrap().actors[0].damage(1_000_000.);
        assert!(ship.warp_waypoint(mob).is_err());
        assert!(ship.warp_waypoint(0x20000001).is_err());
    }
    #[test]
    fn station_docking_requires_clearance_and_launch_restores_exterior_sector() {
        let mut session = session();
        session.sector = session.sectors.sector(1076).unwrap();
        session.logged_in = true;
        session.target = 100002;
        session.selected_peer = Some("127.0.0.1".parse().unwrap());
        session.endpoint = Some("127.0.0.1:22345".parse().unwrap());
        let exterior = session
            .sector
            .navpoints
            .iter()
            .find(|nav| nav.id == 100002)
            .unwrap()
            .position;
        assert!(session.complete_docking(100002).is_err());
        assert!(session.begin_docking(100002).is_err()); // too far
        session.position = [exterior[0] + 4500., exterior[1], exterior[2]];
        assert!(session.begin_docking(100000).is_err());
        session.begin_docking(100002).unwrap();
        assert!(session
            .pending
            .iter()
            .any(|frame| frame.opcode == 0x92 && frame.body[..4] == 5u32.to_be_bytes()));
        session.complete_docking(100002).unwrap();
        assert_eq!(session.sector.id, 10761);
        assert!(!session.logged_in);
        assert!(session.pending.iter().any(|frame| frame.opcode == 0x3a));
        let mut session = PlayerSession::new(
            session.sectors.sector(1076).unwrap(),
            session.bind,
            session.state_dir.clone(),
        )
        .unwrap();
        assert!(!session.logged_in);
        let handoff = session
            .pending
            .iter()
            .find(|frame| frame.opcode == 0x3a)
            .unwrap();
        assert_eq!(&handoff.body[24..28], &1076u32.to_be_bytes());
        let ticket = packets::character_ticket(10761, &session.nonce, session.player_id());
        let mut login = vec![0; 137];
        login[..64].copy_from_slice(&ticket.body[4..]);
        session.handle_input(2, &login).unwrap();
        assert!(session.pending.iter().any(|frame| frame.opcode == 0x52
            && frame.body.len()
                == 132 + 265 * session.sector.station.as_ref().unwrap().npcs.len()));
        assert!(session.pending.iter().any(|frame| frame.opcode == 0x4f
            && frame.body == [10761u32.to_le_bytes().as_slice(), &[0, 0]].concat()));
        assert!(session
            .handle_input(
                0x9b,
                &[session.player_id().to_le_bytes().as_slice(), &[0, 0]].concat()
            )
            .is_err());
        session.advance_player(1.).unwrap();
        assert_eq!(session.position, [0.; 3]);
        let movement = [
            session.player_id().to_le_bytes().as_slice(),
            &0u32.to_le_bytes(),
            &1f32.to_le_bytes(),
            &10f32.to_le_bytes(),
            &20f32.to_le_bytes(),
            &30f32.to_le_bytes(),
            &1u32.to_le_bytes(),
        ]
        .concat();
        session.handle_input(0x9d, &movement).unwrap();
        assert_eq!(
            session.station_pose.as_ref().unwrap().position,
            [10., 20., 30.]
        );
        assert_eq!(session.position, [0.; 3]); // avatar reports cannot move the ship
        let mut spoofed = movement.clone();
        spoofed[..4].copy_from_slice(&(session.player_id() + 1).to_le_bytes());
        assert!(session.handle_input(0x9d, &spoofed).is_err());
        assert!(session
            .handle_input(
                0x9f,
                &[
                    session.player_id().to_le_bytes(),
                    99u32.to_le_bytes(),
                    0u32.to_le_bytes()
                ]
                .concat()
            )
            .is_err());
        assert!(session.station_request(99999, 1).is_err());
        session.station_request(10761, 1).unwrap();
        assert_eq!(session.sector.id, 1076);
        assert!(distance(session.position, exterior) >= 4499.);
        let saved: SavedPlayer =
            serde_json::from_slice(&fs::read(session.state_dir.join("player.json")).unwrap())
                .unwrap();
        assert_eq!(saved.sector, 1076);
    }
    #[test]
    fn registration_and_one_use_ticket_bound_to_peer() {
        let mut session = session();
        let peer: SocketAddr = "127.0.0.1:12345".parse().unwrap();
        assert!(session.issue_ticket("local", "local", peer.ip()).is_err());
        session
            .handle_udp(
                3806,
                peer,
                Datagram {
                    opcode: 0x1000,
                    player: 0,
                    sequence: 0,
                    body: 246u32.to_le_bytes().to_vec(),
                },
            )
            .unwrap();
        let ticket = session.issue_ticket("local", "local", peer.ip()).unwrap();
        assert!(!session.consume_ticket(&ticket, "127.0.0.3".parse().unwrap()));
        assert!(session.consume_ticket(&ticket, peer.ip()));
        assert!(!session.consume_ticket(&ticket, peer.ip()));
    }
    #[test]
    fn wrong_endpoint_cannot_move_player() {
        let mut session = session();
        session
            .select(0, "127.0.0.1".parse().unwrap(), false)
            .unwrap();
        let packet = Datagram {
            opcode: 0x1004,
            player: FIRST_PLAYER_ID,
            sequence: 1,
            body: vec![0; 12],
        };
        assert!(session
            .handle_udp(SECTOR_PORT, "127.0.0.1:2222".parse().unwrap(), packet)
            .is_err());
        assert_eq!(session.position, [0.; 3]);
    }

    #[test]
    fn proxy_rebuilt_ticket_is_accepted_only_for_proxy_selection() {
        let peer: SocketAddr = "127.0.0.1:22334".parse().unwrap();
        for via_proxy in [false, true] {
            let mut session = session();
            session.select(0, peer.ip(), via_proxy).unwrap();
            session
                .handle_udp(
                    3808,
                    peer,
                    Datagram {
                        opcode: 0x2008,
                        player: FIRST_PLAYER_ID,
                        sequence: 0,
                        body: session.sector.id.to_le_bytes().to_vec(),
                    },
                )
                .unwrap();
            let mut body = vec![0; 137];
            body[16..20].copy_from_slice(&FIRST_PLAYER_ID.to_be_bytes());
            body[20..24].copy_from_slice(&session.sector.id.to_be_bytes());
            body[44..60].copy_from_slice(b"MY_Avatar_Ticket");
            let packet = Datagram {
                opcode: 2,
                player: FIRST_PLAYER_ID,
                sequence: 1,
                body,
            };
            let mut wrong_player = packet.clone();
            wrong_player.body[16..20].copy_from_slice(&(FIRST_PLAYER_ID + 1).to_be_bytes());
            assert!(session.handle_udp(SECTOR_PORT, peer, wrong_player).is_err());
            // Use a fresh sequence because rejected inputs are also deduplicated.
            let mut packet = packet;
            packet.sequence = 2;
            let result = session.handle_udp(SECTOR_PORT, peer, packet);
            assert_eq!(result.is_ok(), via_proxy);
            assert_eq!(session.logged_in, via_proxy);
        }
    }
}
