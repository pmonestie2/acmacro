//! Six-player groups with explicit invitations and leader permissions. This is
//! independent of sockets: the coordinator authenticates actors and sends AUX.
use crate::protocol::invalid;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};
use std::io;
use std::time::{Duration, Instant};

/// Legacy GroupManager offsets, indexed by formation action 4..6 and the
/// member's position in the complete group (leader occupies slot zero).
pub fn formation_offset(formation: u32, member: usize) -> [f32; 3] {
    const OFFSETS: [[[f32; 3]; 6]; 3] = [
        [
            [0., 0., 0.],
            [-300., -200., 50.],
            [-300., 200., 50.],
            [-300., 0., 50.],
            [-400., -200., 100.],
            [-400., 200., 100.],
        ],
        [
            [0., 0., 0.],
            [0., -200., 0.],
            [0., 200., 0.],
            [-300., 0., 100.],
            [-300., -200., 100.],
            [-300., 200., 100.],
        ],
        [
            [0., 0., 0.],
            [-300., -200., 0.],
            [-300., 200., 0.],
            [-400., 0., 0.],
            [-600., -200., 0.],
            [-600., 200., 0.],
        ],
    ];
    OFFSETS
        .get(formation.wrapping_sub(4) as usize)
        .and_then(|slots| slots.get(member))
        .copied()
        .unwrap_or([0.; 3])
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Group {
    pub id: u32,
    pub leader: u32,
    pub members: Vec<u32>,
    pub formation: u32,
    /// Formation is an invitation to follow, not an automatic teleport of every
    /// member. Each follower explicitly forms up with action 7.
    #[serde(default)]
    pub formed: Vec<u32>,
}

struct Invitation {
    source: u32,
    group: u32,
    expires: Instant,
}

#[derive(Default, Serialize, Deserialize)]
pub struct GroupManager {
    next_id: u32,
    groups: BTreeMap<u32, Group>,
    #[serde(skip)]
    membership: BTreeMap<u32, u32>,
    #[serde(skip)]
    invitations: BTreeMap<u32, Invitation>,
}

impl GroupManager {
    pub fn group(&self, member: u32) -> Option<&Group> {
        self.membership
            .get(&member)
            .and_then(|id| self.groups.get(id))
    }
    pub fn members(&self, member: u32) -> Vec<u32> {
        self.group(member)
            .map_or_else(Vec::new, |group| group.members.clone())
    }
    pub fn together(&self, a: u32, b: u32) -> bool {
        self.membership
            .get(&a)
            .is_some_and(|group| self.membership.get(&b) == Some(group))
    }
    pub fn invite(&mut self, source: u32, target: u32) -> io::Result<()> {
        self.invitations
            .retain(|_, invitation| invitation.expires > Instant::now());
        if source == target
            || self.group(target).is_some()
            || self.invitations.contains_key(&target)
        {
            return Err(invalid(
                "Player is already grouped, invited, or is yourself.",
            ));
        }
        let id = if let Some(group) = self.group(source) {
            if group.leader != source {
                return Err(invalid("Only the group leader can invite."));
            }
            let reserved = self
                .invitations
                .values()
                .filter(|invitation| invitation.group == group.id)
                .count();
            if group.members.len() + reserved >= 6 {
                return Err(invalid("The group is full."));
            }
            group.id
        } else {
            self.next_id += 1;
            self.groups.insert(
                self.next_id,
                Group {
                    id: self.next_id,
                    leader: source,
                    members: vec![source],
                    formation: 0,
                    formed: Vec::new(),
                },
            );
            self.membership.insert(source, self.next_id);
            self.next_id
        };
        self.invitations.insert(
            target,
            Invitation {
                source,
                group: id,
                expires: Instant::now() + Duration::from_secs(30),
            },
        );
        Ok(())
    }
    pub fn accept(&mut self, target: u32) -> io::Result<()> {
        let invitation = self
            .invitations
            .remove(&target)
            .ok_or_else(|| invalid("No pending group invitation."))?;
        if invitation.expires <= Instant::now() || self.group(target).is_some() {
            return Err(invalid("Invitation expired or player already grouped."));
        }
        let group = self
            .groups
            .get_mut(&invitation.group)
            .ok_or_else(|| invalid("Inviting group no longer exists."))?;
        if group.leader != invitation.source || group.members.len() >= 6 {
            return Err(invalid("Invitation is no longer valid."));
        }
        group.members.push(target);
        self.membership.insert(target, group.id);
        Ok(())
    }
    pub fn decline(&mut self, target: u32) {
        self.invitations.remove(&target);
    }
    pub fn leave(&mut self, member: u32) {
        self.invitations.remove(&member);
        self.invitations
            .retain(|_, invitation| invitation.source != member);
        let Some(id) = self.membership.remove(&member) else {
            return;
        };
        if let Some(group) = self.groups.get_mut(&id) {
            group.members.retain(|id| *id != member);
            group.formed.retain(|id| *id != member);
            if group.members.len() < 2 {
                for id in &group.members {
                    self.membership.remove(id);
                }
                self.groups.remove(&id);
                self.invitations
                    .retain(|_, invitation| invitation.group != id);
            } else if group.leader == member {
                group.leader = group.members[0];
                group.formation = 0;
                group.formed.clear();
            }
        }
    }
    pub fn disband(&mut self, leader: u32) -> io::Result<()> {
        let group = self
            .group(leader)
            .ok_or_else(|| invalid("You are not in a group."))?;
        if group.leader != leader {
            return Err(invalid("Only the leader can disband."));
        }
        let id = group.id;
        let members = group.members.clone();
        self.groups.remove(&id);
        self.invitations
            .retain(|_, invitation| invitation.group != id);
        for member in members {
            self.membership.remove(&member);
        }
        Ok(())
    }
    pub fn kick(&mut self, leader: u32, member: u32) -> io::Result<()> {
        if !self
            .group(leader)
            .is_some_and(|group| group.leader == leader && group.members.contains(&member))
        {
            return Err(invalid("Only the leader can remove a group member."));
        }
        self.leave(member);
        Ok(())
    }
    pub fn set_formation(&mut self, leader: u32, formation: u32) -> io::Result<()> {
        if !matches!(formation, 0 | 4 | 5 | 6) {
            return Err(invalid("Unknown group formation."));
        }
        let id = self
            .group(leader)
            .filter(|group| group.leader == leader)
            .map(|group| group.id)
            .ok_or_else(|| invalid("Only the leader can set formation."))?;
        let group = self.groups.get_mut(&id).unwrap();
        group.formation = formation;
        group.formed = if formation == 0 {
            Vec::new()
        } else {
            vec![leader]
        };
        Ok(())
    }
    pub fn form_up(&mut self, member: u32) -> io::Result<()> {
        let id = self
            .group(member)
            .map(|group| group.id)
            .ok_or_else(|| invalid("You are not in a group."))?;
        let group = self.groups.get_mut(&id).unwrap();
        if group.formation == 0 {
            return Err(invalid("The leader must choose a formation first."));
        }
        if !group.formed.contains(&member) {
            group.formed.push(member);
        }
        Ok(())
    }
    pub fn leave_formation(&mut self, member: u32) {
        let Some(id) = self.group(member).map(|group| group.id) else {
            return;
        };
        let group = self.groups.get_mut(&id).unwrap();
        if group.leader == member {
            group.formation = 0;
            group.formed.clear();
        } else {
            group.formed.retain(|id| *id != member);
        }
    }
    pub fn formation_groups(&self) -> Vec<Group> {
        self.groups
            .values()
            .filter(|group| group.formation != 0)
            .cloned()
            .collect()
    }
    pub fn restore_index(&mut self, known: &HashSet<u32>) -> io::Result<()> {
        self.membership.clear();
        for group in self.groups.values_mut() {
            group.members.retain(|id| known.contains(id));
            if !matches!(group.formation, 0 | 4..=6) {
                return Err(invalid("Saved group has an invalid formation."));
            }
            group.formed.retain(|id| group.members.contains(id));
            if group.members.len() > 6 {
                return Err(invalid("Saved group exceeds six members."));
            }
            if !group.members.contains(&group.leader) && !group.members.is_empty() {
                group.leader = group.members[0];
                group.formation = 0;
                group.formed.clear();
            }
            for member in &group.members {
                if self.membership.insert(*member, group.id).is_some() {
                    return Err(invalid("Saved member belongs to multiple groups."));
                }
            }
        }
        self.groups.retain(|_, group| !group.members.is_empty());
        self.next_id = self
            .next_id
            .max(self.groups.keys().copied().max().unwrap_or(0));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invitations_permissions_capacity_and_leader_departure() {
        let mut groups = GroupManager::default();
        groups.invite(1, 2).unwrap();
        groups.accept(2).unwrap();
        assert!(groups.together(1, 2));
        assert!(groups.invite(2, 3).is_err());
        for id in 3..=6 {
            groups.invite(1, id).unwrap();
            groups.accept(id).unwrap();
        }
        assert!(groups.invite(1, 7).is_err());
        assert!(groups.kick(2, 3).is_err());
        groups.leave(1);
        assert_eq!(groups.group(2).unwrap().leader, 2);
        groups.kick(2, 3).unwrap();
        assert!(!groups.together(2, 3));
        groups.disband(2).unwrap();
        assert!(groups.group(4).is_none());
    }
    #[test]
    fn stale_invitation_cannot_join_and_groups_restore_without_invites() {
        let mut groups = GroupManager::default();
        groups.invite(1, 2).unwrap();
        groups.invitations.get_mut(&2).unwrap().expires = Instant::now();
        assert!(groups.accept(2).is_err());
        groups.invite(1, 2).unwrap();
        groups.accept(2).unwrap();
        let mut restored: GroupManager =
            serde_json::from_slice(&serde_json::to_vec(&groups).unwrap()).unwrap();
        restored.restore_index(&HashSet::from([1, 2])).unwrap();
        assert!(restored.together(1, 2));
        assert!(restored.accept(3).is_err());
    }
}
