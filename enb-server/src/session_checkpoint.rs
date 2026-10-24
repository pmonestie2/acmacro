//! Crash/restart continuity for an authenticated game session or sector handoff.
//!
//! `Instant` cannot survive a process, so the checkpoint stores the game clock
//! plus wall-clock time. Recovery advances that clock by the short outage while
//! pausing simulation. UDP addresses and sequence/replay state must survive too:
//! otherwise the proxy discards the replacement server's packets as old ones.
use super::*;
use std::time::{SystemTime, UNIX_EPOCH};

const VERSION: u32 = 1;
const MAX_OUTAGE_MS: u64 = 120_000;

#[derive(Serialize, Deserialize)]
pub(super) struct Checkpoint {
    version: u32,
    saved_at_ms: u64,
    bind: Ipv4Addr,
    username: String,
    sector: u32,
    tick: u32,
    position: [f32; 3],
    motion: Motion,
    selected_peer: IpAddr,
    endpoint: SocketAddr,
    comms: Option<SocketAddr>,
    nonce: [u8; 20],
    #[serde(default)]
    #[serde(alias = "refinery_epoch")]
    #[serde(alias = "production_epoch")]
    session_epoch: u64,
    #[serde(default)]
    #[serde(alias = "refinery")]
    workbench: Option<production_service::WorkbenchWindow>,
    #[serde(default)]
    #[serde(alias = "refinery_lab_ready")]
    workbench_lab_ready: bool,
    sequence: u32,
    replay: VecDeque<Datagram>,
    inputs: VecDeque<u32>,
    warp: Warp,
    warp_effects: Vec<u32>,
    next_effect: u32,
    target: u32,
    shield: f32,
    hull: f32,
    #[serde(default = "legacy_hull_capacity")]
    hull_capacity: f32,
    #[serde(default = "full_energy")]
    energy: f32,
    #[serde(default)]
    weapon_cooldowns: [f32; 20],
    #[serde(default)]
    weapon_firing: [bool; 20],
    #[serde(default)]
    player_firing: bool,
    #[serde(default)]
    following: bool,
    #[serde(default)]
    last_launch: u64,
    #[serde(default)]
    mob_damage_receipts: Vec<mob_damage::MobDamageReceipt>,
    #[serde(default)]
    skills: SkillState,
    #[serde(default)]
    handoff: Option<Vec<u8>>,
    #[serde(default)]
    docking_target: Option<u32>,
    #[serde(default)]
    gate_transit: Option<crate::gating::GateTransit>,
    #[serde(default)]
    resource_ids: HashSet<u32>,
    #[serde(default)]
    peer_ids: HashSet<u32>,
    #[serde(default)]
    station_pose: Option<StationPose>,
    #[serde(default)]
    station_peers: HashMap<u32, StationPose>,
    #[serde(default)]
    mining: Option<mining::MiningCast>,
    #[serde(default)]
    loot_cast: Option<looting::LootCast>,
    #[serde(default)]
    loot_window: Option<u32>,
    #[serde(default)]
    corpse_ids: HashMap<u32, [f32; 3]>,
    #[serde(default)]
    resource_window: Option<u32>,
    #[serde(default)]
    npc_interaction: Option<npc_services::NpcInteraction>,
    mobs: Vec<Mob>,
    #[serde(default)]
    mob_visible: Vec<bool>,
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn legacy_hull_capacity() -> f32 {
    1000.
}

fn full_energy() -> f32 {
    1000.
}

impl PlayerSession {
    pub(super) fn checkpoint_mob_damage(
        &self,
        plan: &mob_damage::DamagePlan,
        receipt: &mob_damage::MobDamageReceipt,
    ) -> io::Result<()> {
        let mut saved = self.capture_checkpoint()?.ok_or_else(|| {
            invalid("Incoming damage requires an authenticated transport checkpoint.")
        })?;
        saved.shield = plan.result.shield;
        saved.hull = plan.result.hull;
        saved.energy = plan.energy;
        saved.motion = plan.motion.clone();
        saved.warp = plan.warp.clone();
        if plan.warp_event.is_some() {
            saved.warp_effects.clear();
        }
        saved.mining = None;
        if plan.result.hull == 0. {
            saved.weapon_firing.fill(false);
            saved.player_firing = false;
            saved.following = false;
        }
        saved.mob_damage_receipts.push(receipt.clone());
        crate::durable_json::replace(&self.state_dir.join("session.local.json"), &saved)
    }

    /// The file is local operational state, never a reusable login ticket.
    /// Restore only the same account, bind address and sector for a short outage.
    pub(super) fn restore_session(&mut self) -> io::Result<bool> {
        let path = self.state_dir.join("session.local.json");
        if !path.exists() {
            return Ok(false);
        }
        let saved: Checkpoint = serde_json::from_slice(&fs::read(&path)?)
            .map_err(|_| invalid("invalid session checkpoint"))?;
        let gap = unix_ms().checked_sub(saved.saved_at_ms);
        if saved.version != VERSION
            || saved.bind != self.bind
            || saved.username != self.account.username
            || saved.sector != self.sector.id
            || gap.is_none_or(|gap| gap > MAX_OUTAGE_MS)
            || saved.endpoint.ip() != saved.selected_peer
            || saved
                .comms
                .is_some_and(|peer| peer.ip() != saved.selected_peer)
            || saved
                .gate_transit
                .as_ref()
                .is_some_and(|gate| !gate.valid_for(&self.sector))
            || !self.sector.bounds.contains(saved.position)
            || saved.mobs.len() > 256
            || saved.mob_visible.len() > 256
            || saved.replay.len() > 128
            || saved.inputs.len() > 256
            || !mob_damage::valid_receipts(&saved.mob_damage_receipts)
            || saved
                .workbench
                .as_ref()
                .is_some_and(|window| !window.valid(self.sector.id))
            || !saved.shield.is_finite()
            || !saved.hull_capacity.is_finite()
            || saved.hull_capacity <= 0.
            || saved.hull < 0.
            || saved.hull > saved.hull_capacity
            || !saved.hull.is_finite()
            || !saved.energy.is_finite()
            || saved.mining.as_ref().is_some_and(|cast| !cast.valid())
            || saved.skills.cast.as_ref().is_some_and(|cast| !cast.valid())
            || saved.loot_cast.as_ref().is_some_and(|cast| !cast.valid())
            || saved.corpse_ids.len() > 4096
            || saved.corpse_ids.iter().any(|(id, position)| {
                !(0x30000000..0x40000000).contains(id)
                    || position.iter().any(|value| !value.is_finite())
            })
            || saved.station_pose.as_ref().is_some_and(|pose| {
                self.sector
                    .station
                    .as_ref()
                    .is_none_or(|station| !pose.valid_for(station))
            })
            || saved.station_peers.len() > 1024
            || saved.station_peers.iter().any(|(id, pose)| {
                *id == self.player_id()
                    || *id & 0xff000000 != 0x40000000
                    || self
                        .sector
                        .station
                        .as_ref()
                        .is_none_or(|station| !pose.valid_for(station))
            })
            || saved
                .weapon_cooldowns
                .iter()
                .any(|value| !value.is_finite() || *value < 0. || *value > 3600.)
        {
            return Err(invalid(
                "stale or incompatible session checkpoint; normal login required",
            ));
        }
        self.position = saved.position;
        self.motion = saved.motion;
        self.selected_peer = Some(saved.selected_peer);
        self.endpoint = Some(saved.endpoint);
        self.comms = saved.comms;
        self.nonce = saved.nonce;
        if saved.session_epoch != 0 {
            self.session_epoch = saved.session_epoch;
        }
        self.workbench = saved.workbench;
        self.workbench_lab_ready = saved.workbench_lab_ready;
        self.sequence = saved.sequence;
        self.replay = saved.replay;
        self.inputs = saved.inputs;
        self.warp = saved.warp;
        self.warp_effects = saved.warp_effects;
        self.next_effect = saved.next_effect;
        self.target = saved.target;
        self.shield = saved.shield;
        let capacity = self.equipment.ship.hull_capacity;
        self.hull = if capacity == saved.hull_capacity {
            saved.hull
        } else {
            (saved.hull / saved.hull_capacity).clamp(0., 1.) * capacity
        };
        self.energy = saved.energy;
        self.weapon_cooldowns = saved.weapon_cooldowns;
        self.weapon_firing = saved.weapon_firing;
        self.player_firing = saved.player_firing;
        self.following = saved.following;
        self.last_launch = saved.last_launch;
        self.mob_damage_receipts = saved.mob_damage_receipts;
        self.follow_peer = None;
        self.skills = saved.skills;
        self.handoff = saved.handoff;
        self.docking_target = saved.docking_target;
        self.gate_transit = saved.gate_transit;
        self.visible_resources = saved.resource_ids;
        self.visible_peers = saved.peer_ids.clone();
        self.peer_ids = saved.peer_ids;
        // Rebuild action eligibility from live coordinator snapshots, never
        // persisted health/position belonging to another authenticated session.
        self.peer_contacts.clear();
        self.station_pose = saved.station_pose;
        self.station_peers = saved.station_peers;
        self.mining = saved.mining;
        self.loot_cast = saved.loot_cast;
        self.loot_window = saved.loot_window;
        self.visible_corpses = saved.corpse_ids;
        self.resource_window = saved.resource_window;
        self.npc_interaction = saved.npc_interaction;
        // Keep known MOB identities and health; permit content additions on reload.
        let loot_shared = Arc::clone(&self.loot_world);
        let loot = lock_loot(&loot_shared)?;
        let shared = Arc::clone(&self.combat);
        let mut combat = lock_combat(&shared)?;
        let previous_mob_count = saved.mobs.len();
        for (current, previous) in combat.actors.iter_mut().zip(saved.mobs) {
            if current.definition.source_id == previous.definition.source_id {
                current.restore_from(previous)?;
            }
        }
        loot.reconcile_combat(&mut combat, unix_ms());
        drop(loot);
        self.mob_states = combat.actors.iter().map(|mob| mob.state).collect();
        self.mob_visible = saved.mob_visible;
        self.mob_visible.resize(combat.actors.len(), false);
        self.mob_relationships = vec![None; combat.actors.len()];
        self.tick_offset = saved.tick.wrapping_add(gap.unwrap() as u32);
        self.started = Instant::now();
        self.logged_in = self.handoff.is_none();
        self.last_seen = Instant::now();
        if let Some(join) = &self.handoff {
            self.pending.push(Frame::new(0x3a, join.clone()));
            println!("Resumed sector handoff: {}", self.account.character.name);
            return Ok(true);
        }
        // An inventory file can be newer than the transport checkpoint if the
        // process ended between durable replacement and sending the AUX reply.
        // Reconcile the client from authoritative state on every resume.
        for nav in &self.sector.navpoints {
            self.pending.extend(packets::static_navigation_refresh(nav));
        }
        self.pending.extend(
            self.inventory
                .templates(&self.items)?
                .into_iter()
                .map(ItemTemplate::packet),
        );
        self.pending.push(packets::inventory_update(
            self.player_id(),
            &self.inventory,
            &self.items,
            &self.equipment.ship,
        ));
        self.pending
            .push(packets::ship_target(self.player_id(), self.target));
        self.refresh_target_verbs(true);
        // Skill grants can change during development without another login.
        self.pending.push(packets::player_aux(
            &self.sector,
            &self.account.character,
            &self.inventory,
            &self.items,
        ));
        self.publish_reputation();
        self.restore_npc_service()?;
        self.initialise_workbench_lab(!self.workbench_lab_ready)?;
        self.pending
            .push(packets::cloak(self.player_id(), self.skills.cloak_rank > 0));
        for (&skill, &remaining) in &self.skills.cooldowns {
            if remaining > 0. {
                self.pending.push(packets::skill_cooldown(
                    skill,
                    self.tick_ms(),
                    (remaining * 1000.) as u32,
                ));
            }
        }
        if let Some(cast) = &self.skills.cast {
            self.pending.push(packets::ability_progress(
                Some(cast.ability),
                self.tick_ms(),
                self.tick_ms().wrapping_add((cast.remaining * 1000.) as u32),
            ));
        }
        self.pending.push(packets::ship_resources(
            self.player_id(),
            &self.equipment.ship,
            Some(self.energy),
            Some(self.shield),
            Some(self.hull),
        ));
        self.pending
            .push(packets::incapacitated(self.player_id(), self.hull <= 0.));
        for index in 0..previous_mob_count.max(combat.actors.len()) {
            self.pending.push(Frame::new(
                7,
                (MOB_ID + index as u32).to_le_bytes().to_vec(),
            ));
        }
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
        self.pending.push(packets::warp_state(
            self.player_id(),
            self.warp.phase,
            None,
            self.tick_ms(),
        ));
        self.pending.push(packets::message(
            "Local server resumed; your game session was preserved.",
        ));
        println!(
            "Resumed game session: {}; next_sequence={}; outage={}ms",
            self.account.character.name,
            self.sequence,
            gap.unwrap()
        );
        drop(combat);
        self.restore_mining_claim()?;
        self.restore_loot_claim()?;
        self.resume_loot_visuals()?;
        self.resume_mining_visuals()?;
        self.refresh_resource_target(true)?;
        Ok(true)
    }

    /// Capture the current authenticated transport and gameplay state.
    pub(super) fn capture_checkpoint(&self) -> io::Result<Option<Checkpoint>> {
        if (!self.logged_in && self.handoff.is_none())
            || self.endpoint.is_none()
            || self.selected_peer.is_none()
        {
            return Ok(None);
        }
        Ok(self.checkpoint_snapshot(lock_combat(&self.combat)?.actors.clone()))
    }

    fn checkpoint_snapshot(&self, mobs: Vec<Mob>) -> Option<Checkpoint> {
        if (!self.logged_in && self.handoff.is_none())
            || self.endpoint.is_none()
            || self.selected_peer.is_none()
        {
            return None;
        }
        let saved = Checkpoint {
            version: VERSION,
            saved_at_ms: unix_ms(),
            bind: self.bind,
            username: self.account.username.clone(),
            sector: self.sector.id,
            tick: self.tick_ms(),
            position: self.position,
            motion: self.motion.clone(),
            selected_peer: self.selected_peer.unwrap(),
            endpoint: self.endpoint.unwrap(),
            comms: self.comms,
            nonce: self.nonce,
            session_epoch: self.session_epoch,
            workbench: self.workbench.clone(),
            workbench_lab_ready: self.workbench_lab_ready,
            sequence: self.sequence,
            replay: self.replay.clone(),
            inputs: self.inputs.clone(),
            warp: self.warp.clone(),
            warp_effects: self.warp_effects.clone(),
            next_effect: self.next_effect,
            target: self.target,
            shield: self.shield,
            hull: self.hull,
            hull_capacity: self.equipment.ship.hull_capacity,
            energy: self.energy,
            weapon_cooldowns: self.weapon_cooldowns,
            weapon_firing: self.weapon_firing,
            player_firing: self.player_firing,
            following: self.following,
            last_launch: self.last_launch,
            mob_damage_receipts: self.mob_damage_receipts.clone(),
            skills: self.skills.clone(),
            handoff: self.handoff.clone(),
            docking_target: self.docking_target,
            gate_transit: self.gate_transit.clone(),
            resource_ids: self.visible_resources.clone(),
            peer_ids: self.visible_peers.clone(),
            station_pose: self.station_pose.clone(),
            station_peers: self.station_peers.clone(),
            mining: self.mining.clone(),
            loot_cast: self.loot_cast.clone(),
            loot_window: self.loot_window,
            corpse_ids: self.visible_corpses.clone(),
            resource_window: self.resource_window,
            npc_interaction: self.npc_interaction.clone(),
            mobs,
            mob_visible: self.mob_visible.clone(),
        };
        Some(saved)
    }

    /// Normal checkpoints follow packet construction; a prepared transition
    /// instead owns an explicit destination checkpoint before publication.
    pub fn checkpoint_session(&mut self, force: bool) -> io::Result<()> {
        // A committed transition journal owns both files until materialization.
        if self.transition_pending.is_some() {
            return Ok(());
        }
        if !force && self.last_checkpoint.elapsed() < Duration::from_millis(250) {
            return Ok(());
        }
        let Some(saved) = self.capture_checkpoint()? else {
            return Ok(());
        };
        fs::create_dir_all(&self.state_dir)?;
        let temporary = self.state_dir.join("session.local.json.tmp");
        fs::write(&temporary, serde_json::to_vec(&saved)?)?;
        fs::rename(temporary, self.state_dir.join("session.local.json"))?;
        self.last_checkpoint = Instant::now();
        Ok(())
    }

    pub(super) fn forget_session(&self) -> io::Result<()> {
        match fs::remove_file(self.state_dir.join("session.local.json")) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    }
}

impl Checkpoint {
    pub(super) fn validates_transition(
        &self,
        account: &Account,
        bind: Ipv4Addr,
        player: &SavedPlayer,
    ) -> bool {
        self.version == VERSION
            && self.username == account.username
            && self.bind == bind
            && self.sector == player.sector
            && self.position == player.position
            && self.handoff.as_ref().is_some_and(|join| {
                join.len() >= 64
                    && join[16..20] == account.character.game_id.to_be_bytes()
                    && join[20..24] == player.sector.to_be_bytes()
            })
    }
}

impl PlayerSession {
    pub(super) fn transition_checkpoint(
        &self,
        destination: &Sector,
        position: [f32; 3],
        join: &[u8],
    ) -> io::Result<Option<Checkpoint>> {
        // Ability callers may already hold the departing combat lock.
        // The destination checkpoint needs its new actors, not that old lock.
        let Some(mut saved) =
            self.checkpoint_snapshot(destination.mobs.iter().cloned().map(Mob::new).collect())
        else {
            return Ok(None);
        };
        saved.sector = destination.id;
        saved.position = position;
        saved.motion.stop();
        saved.warp.reset(&mut saved.motion);
        saved.warp_effects.clear();
        saved.target = u32::MAX;
        saved.player_firing = false;
        saved.following = false;
        saved.weapon_firing = [false; 20];
        saved.handoff = Some(join.to_vec());
        saved.docking_target = None;
        saved.gate_transit = None;
        saved.workbench = None;
        saved.workbench_lab_ready = false;
        saved.resource_ids.clear();
        saved.peer_ids.clear();
        saved.station_pose = None;
        saved.station_peers.clear();
        saved.mining = None;
        saved.loot_cast = None;
        saved.loot_window = None;
        saved.corpse_ids.clear();
        saved.resource_window = None;
        saved.npc_interaction = None;
        Ok(Some(saved))
    }
}
