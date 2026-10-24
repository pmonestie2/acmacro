//! Shared command dispatch, chat, loot permissions and group-scoped skills.
//! Membership is checked here at
//! delivery time as well as at cast time: a stale client selection cannot buff,
//! teleport or revive somebody who left the group during the cast.
use super::*;
use crate::mob::distance;

impl GameState {
    pub(super) fn handle_event(&mut self, source: u32, event: ClientEvent) -> io::Result<()> {
        if !self
            .sessions
            .get(&source)
            .is_some_and(|session| session.logged_in)
        {
            return Err(invalid("Enter the game before using social commands."));
        }
        match event {
            ClientEvent::LootOpen { target, toggle } => {
                self.update_views();
                self.sessions
                    .get_mut(&source)
                    .unwrap()
                    .request_loot_open(target, toggle)
            }
            ClientEvent::LootBegin { target, slot } => {
                self.update_views();
                self.sessions
                    .get_mut(&source)
                    .unwrap()
                    .request_loot_begin(target, slot)
            }
            ClientEvent::Group { action, target } => self.group_action(source, action, target),
            ClientEvent::Chat { channel, text } => self.chat(source, channel, &text),
            ClientEvent::Formation { action, target } => {
                match action {
                    4..=6 => {
                        if !self.sessions[&source].can_form_up() {
                            return Err(invalid(
                                "Choose a formation alive in space, outside warp.",
                            ));
                        }
                        self.groups.set_formation(source, action)?;
                    }
                    7 => {
                        if !self.sessions[&source].can_form_up() {
                            return Err(invalid("Leave warp and recover before forming up."));
                        }
                        let group = self
                            .groups
                            .group(source)
                            .ok_or_else(|| invalid("You are not in a group."))?;
                        let follower = self.sessions[&source]
                            .peer_snapshot()
                            .ok_or_else(|| invalid("Enter space before forming up."))?;
                        let leader = self.sessions[&group.leader]
                            .peer_snapshot()
                            .ok_or_else(|| invalid("The leader is not in space."))?;
                        if follower.hull <= 0.
                            || leader.hull <= 0.
                            || follower.sector != leader.sector
                            || distance(follower.position, leader.position) > 5000.
                        {
                            return Err(invalid(
                                "Form up alive within 5000m of your leader in the same sector.",
                            ));
                        }
                        self.groups.form_up(source)?;
                    }
                    8 => self.groups.leave_formation(source),
                    9 => self.groups.set_formation(source, 0)?,
                    12 => {
                        let selected = self.sessions[&source].current_target();
                        for id in self.groups.members(source) {
                            if id != source && (target == 0 || target == id) {
                                self.sessions.get_mut(&id).unwrap().group_target(selected)?;
                            }
                        }
                    }
                    _ => return Err(invalid("This formation command is not implemented yet.")),
                }
                let mut body = Vec::from(source.to_le_bytes());
                body.extend_from_slice(&15u32.to_le_bytes());
                body.push(1);
                self.sessions
                    .get_mut(&source)
                    .unwrap()
                    .queue([Frame::new(0xbd, body)]);
                self.groups_dirty = true;
                Ok(())
            }
        }
    }

    fn group_action(&mut self, source: u32, action: u32, target: u32) -> io::Result<()> {
        match action {
            10 => {
                if !self
                    .sessions
                    .get(&target)
                    .is_some_and(|session| session.logged_in)
                {
                    return Err(invalid("Invite a player who is online."));
                }
                self.groups.invite(source, target)?;
                let name = self.sessions[&source].account.character.name.clone();
                self.sessions
                    .get_mut(&target)
                    .unwrap()
                    .queue([packets::group_invite(&name)]);
                self.sessions
                    .get_mut(&source)
                    .unwrap()
                    .message("Group invitation sent.");
            }
            11 => self.groups.accept(source)?,
            12 => self.groups.decline(source),
            13 => self.groups.disband(source)?,
            14 => self.groups.leave(source),
            15 => self.groups.kick(source, target)?,
            _ => return Err(invalid("Unknown group action.")),
        }
        self.groups_dirty = true;
        Ok(())
    }

    fn find_online(&self, name: &str) -> io::Result<u32> {
        self.sessions
            .iter()
            .find(|(_, session)| {
                session.logged_in && session.account.character.name.eq_ignore_ascii_case(name)
            })
            .map(|(id, _)| *id)
            .ok_or_else(|| invalid("That character is not online."))
    }

    fn chat(&mut self, source: u32, channel: u8, text: &str) -> io::Result<()> {
        let text = text.trim();
        if text.is_empty() {
            return Ok(());
        }
        if let Some(command) = text.strip_prefix('/') {
            let (command, argument) = command.split_once(' ').unwrap_or((command, ""));
            let argument = argument.trim();
            match command.to_ascii_lowercase().as_str() {
                "d_spawn" => return self.developer_spawn(source, argument),
                "d_jump" => return self.developer_jump(source, argument),
                "launch" => return self.sessions.get_mut(&source).unwrap().launch_from_station(),
                "tow" => return self.sessions.get_mut(&source).unwrap().tow_to_station(),
                "jumpstart" => return self.sessions.get_mut(&source).unwrap().self_jumpstart(),
                "equip" => return self.sessions.get_mut(&source).unwrap().equip_cargo(argument),
                "unequip" => return self.sessions.get_mut(&source).unwrap().unequip(argument),
                "invite" => return self.group_action(source, 10, self.find_online(argument)?),
                "accept" => return self.group_action(source, 11, 0),
                "decline" => return self.group_action(source, 12, 0),
                "leave" => return self.group_action(source, 14, 0),
                "disband" => return self.group_action(source, 13, 0),
                "kick" => return self.group_action(source, 15, self.find_online(argument)?),
                "ability" => {
                    let index: u32 = argument.parse().map_err(|_| invalid("Use /ability followed by an ability number."))?;
                    return self.sessions.get_mut(&source).unwrap().handle_input(0x58,
                        &[source.to_le_bytes(), 0u32.to_le_bytes(), index.to_le_bytes()].concat());
                }
                "who" => {
                    let names = self.sessions.values().filter(|session| session.logged_in)
                        .map(|session| session.account.character.name.clone()).collect::<Vec<_>>().join(", ");
                    self.sessions.get_mut(&source).unwrap().message(&format!("Online: {names}"));
                }
                "where" => {
                    let session = &self.sessions[&source];
                    let message = format!("{}: {:.0}, {:.0}, {:.0}", session.sector.name, session.position[0], session.position[1], session.position[2]);
                    self.sessions.get_mut(&source).unwrap().message(&message);
                }
                "help" => self.sessions.get_mut(&source).unwrap().message("Commands: /who, /where, /invite NAME, /accept, /decline, /leave, /kick NAME, /disband, /ability NUMBER, /jumpstart, /tow, /launch, /equip CARGO_SLOT MOUNT_SLOT, /unequip MOUNT_SLOT. Dev: /d_spawn [MOB_NAME_OR_ID] [COUNT], /d_jump SECTOR_NAME_OR_ID. Slots are zero-based."),
                _ => return Err(invalid("Unknown local command. Use /help.")),
            }
            return Ok(());
        }
        let session = &self.sessions[&source];
        let name = session.account.character.name.clone();
        let sector = session.sector.id;
        let position = session.position;
        let recipients: Vec<u32> = match channel {
            0 => {
                // Legacy "to target" chat, rendered as a private message.
                let target = session.current_target();
                if !self
                    .sessions
                    .get(&target)
                    .is_some_and(|session| session.logged_in)
                {
                    return Err(invalid("Select an online player for private chat."));
                }
                vec![source, target]
            }
            1 => {
                let members = self.groups.members(source);
                if members.is_empty() {
                    return Err(invalid("You are not in a group."));
                }
                members
            }
            2 => return Err(invalid("Guilds are not implemented yet.")),
            _ => self
                .sessions
                .iter()
                .filter(|(_, session)| {
                    session.logged_in
                        && session.sector.id == sector
                        && (channel == 4 || distance(session.position, position) <= 50_000.)
                })
                .map(|(id, _)| *id)
                .collect(),
        };
        let prefix = if channel == 0 {
            "[PM] "
        } else if channel == 1 {
            "[Group] "
        } else {
            ""
        };
        for id in recipients {
            self.sessions
                .get_mut(&id)
                .unwrap()
                .message(&format!("{prefix}{name}: {text}"));
        }
        Ok(())
    }

    pub(super) fn publish_groups(&mut self) {
        let updates: Vec<_> = self
            .sessions
            .keys()
            .map(|id| {
                let group = self.groups.group(*id);
                let members = self
                    .groups
                    .members(*id)
                    .iter()
                    .filter_map(|member| self.sessions.get(member))
                    .map(|session| session.account.character.clone())
                    .collect::<Vec<_>>();
                (
                    *id,
                    packets::group_state(
                        *id,
                        &members,
                        group.map_or(0, |group| group.leader),
                        group.map_or(0, |group| group.formation),
                        group.map_or(&[], |group| group.formed.as_slice()),
                    ),
                )
            })
            .collect();
        for (id, frame) in updates {
            if self.sessions[&id].logged_in {
                self.sessions.get_mut(&id).unwrap().queue([frame]);
            }
        }
    }

    pub(super) fn save_groups(&self) -> io::Result<()> {
        std::fs::create_dir_all(&self.root)?;
        let temporary = self.root.join("groups.local.json.tmp");
        std::fs::write(&temporary, serde_json::to_vec_pretty(&self.groups)?)?;
        std::fs::rename(temporary, self.root.join("groups.local.json"))
    }

    pub(super) fn advance_formations(&mut self) {
        // Formed ships retain their relative offsets while the leader warps.
        // A sector transfer, incapacitation or logout releases them.
        let mut following = std::collections::HashSet::new();
        for group in self.groups.formation_groups() {
            let leader = self
                .sessions
                .get(&group.leader)
                .filter(|session| session.can_lead_formation())
                .and_then(|session| session.peer_snapshot());
            for member in group
                .formed
                .iter()
                .copied()
                .filter(|id| *id != group.leader)
            {
                let eligible = leader.as_ref().is_some_and(|leader| {
                    self.sessions.get(&member).is_some_and(|session| {
                        session.can_form_up() && session.sector.id == leader.sector
                    })
                });
                if eligible {
                    let index = group.members.iter().position(|id| *id == member).unwrap();
                    let offset = crate::groups::formation_offset(group.formation, index);
                    self.sessions
                        .get_mut(&member)
                        .unwrap()
                        .follow_formation(leader.as_ref().unwrap(), offset);
                    following.insert(member);
                } else {
                    self.groups.leave_formation(member);
                    self.groups_dirty = true;
                }
            }
        }
        for (id, session) in &mut self.sessions {
            if !following.contains(id) {
                session.release_formation();
            }
        }
    }

    pub(super) fn deliver_support(&mut self) -> io::Result<()> {
        let actions: Vec<_> = self
            .sessions
            .iter_mut()
            .map(|(id, session)| {
                (
                    *id,
                    session.sector.id,
                    session.position,
                    session.take_support(),
                )
            })
            .collect();
        for (source, sector, position, actions) in actions {
            for action in actions {
                let mut delivered = false;
                // Native Jumpstart can rescue any visible incapacitated ship,
                // independently of group membership. Other support stays scoped
                // to the source's actual group.
                let recipients = match action {
                    SupportAction::Jumpstart { target, .. } => vec![target],
                    _ => self.groups.members(source),
                };
                for id in recipients {
                    if id == source {
                        continue;
                    }
                    let Some(peer) = self
                        .sessions
                        .get(&id)
                        .and_then(PlayerSession::peer_snapshot)
                    else {
                        continue;
                    };
                    let eligible = match action {
                        SupportAction::Wormhole {
                            origin_sector,
                            origin,
                            ..
                        } => {
                            peer.sector == origin_sector && distance(peer.position, origin) <= 6000.
                        }
                        SupportAction::Cloak { enabled: false, .. } => true,
                        SupportAction::Cloak { .. } => {
                            peer.sector == sector && distance(peer.position, position) <= 1000.
                        }
                        SupportAction::Environment { target, .. } => {
                            (target == 0 || target == id)
                                && peer.sector == sector
                                && distance(peer.position, position) <= 3000.
                        }
                        SupportAction::Jumpstart { target, range, .. } => {
                            target == id
                                && self.sessions[&source].can_deliver_jumpstart(&peer, range)
                        }
                        SupportAction::Fold { target } => {
                            target == id
                                && peer.sector == sector
                                && distance(peer.position, position) <= 6000.
                        }
                        SupportAction::Energy { .. } => {
                            peer.sector == sector && distance(peer.position, position) <= 3000.
                        }
                    };
                    if eligible {
                        self.sessions
                            .get_mut(&id)
                            .unwrap()
                            .receive_support(source, &action)?;
                        delivered = true;
                    }
                }
                if !delivered
                    && matches!(
                        action,
                        SupportAction::Jumpstart { .. } | SupportAction::Fold { .. }
                    )
                {
                    self.sessions.get_mut(&source).unwrap().message(
                        if matches!(action, SupportAction::Jumpstart { .. }) {
                            "The target ship left Jumpstart range or no longer needs recovery."
                        } else {
                            "The group member left ability range before completion."
                        },
                    );
                }
            }
        }
        Ok(())
    }

    pub(super) fn expire_group_cloak(&mut self) -> io::Result<()> {
        let peers = self.snapshots();
        let expired: Vec<_> = self
            .sessions
            .iter()
            .filter_map(|(id, session)| session.cloak_parent().map(|source| (*id, source)))
            .filter(|(id, source)| {
                !self.groups.together(*id, *source)
                    || !peers
                        .iter()
                        .find(|peer| peer.profile.game_id == *source)
                        .is_some_and(|leader| {
                            leader.cloaked
                                && leader.hull > 0.
                                && self.sessions[id].sector.id == leader.sector
                                && distance(self.sessions[id].position, leader.position) <= 1000.
                        })
            })
            .collect();
        for (id, source) in expired {
            self.sessions.get_mut(&id).unwrap().receive_support(
                source,
                &SupportAction::Cloak {
                    enabled: false,
                    rank: 0,
                    stealth: 0,
                },
            )?;
        }
        // Legacy checkpoints did not retain caster-derived group stealth.
        // Refresh it before NPC targeting instead of using a recipient's own
        // (possibly absent) trained Cloak skill.
        let inherited: Vec<_> = self
            .sessions
            .iter()
            .filter_map(|(id, session)| {
                let source = session.cloak_parent()?;
                Some((*id, source, self.sessions.get(&source)?.cloak_stealth()?))
            })
            .collect();
        for (id, source, stealth) in inherited {
            self.sessions
                .get_mut(&id)
                .unwrap()
                .inherit_cloak_stealth(source, stealth);
        }
        Ok(())
    }
}
