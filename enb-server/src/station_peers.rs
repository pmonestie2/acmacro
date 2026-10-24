//! Per-room station avatar visibility. The coordinator buckets snapshots once
//! per tick; unchanged idle avatars generate no repeated pose/appearance data.
use super::*;
use crate::peers::StationPeerSnapshot;

impl PlayerSession {
    pub(crate) fn station_location(&self) -> Option<(u32, u32)> {
        if !self.logged_in || self.sector.station.is_none() {
            return None;
        }
        self.station_pose
            .as_ref()
            .map(|pose| (self.sector.id, pose.room))
    }

    pub(crate) fn station_snapshot(&self) -> Option<StationPeerSnapshot> {
        self.station_location()?;
        Some(StationPeerSnapshot {
            profile: self.account.character.clone(),
            station: self.sector.id,
            pose: self.station_pose.clone()?,
        })
    }

    pub(crate) fn publish_station_peers(&mut self, peers: &[StationPeerSnapshot]) {
        let location = self.station_location();
        let visible: HashSet<u32> = peers
            .iter()
            .filter(|peer| {
                peer.profile.game_id != self.player_id()
                    && location == Some((peer.station, peer.pose.room))
            })
            .map(|peer| peer.profile.game_id)
            .collect();
        // Notify departures immediately, including room/sector changes,
        // logout and recipients which currently have no valid interior pose.
        let removed: Vec<u32> = self
            .station_peers
            .keys()
            .filter(|id| !visible.contains(id))
            .copied()
            .collect();
        for id in removed {
            let previous = self.station_peers.remove(&id).unwrap();
            if self.logged_in && self.sector.station.is_some() {
                self.pending
                    .push(packets::station_avatar_room(id, u32::MAX, previous.room));
            }
        }
        if location.is_none() {
            return;
        }
        let broadcast = self.station_view_dirty
            || self.last_station_broadcast.elapsed() >= Duration::from_millis(200);
        for peer in peers
            .iter()
            .filter(|peer| visible.contains(&peer.profile.game_id))
        {
            let id = peer.profile.game_id;
            let previous = self.station_peers.get(&id);
            let entering = previous.is_none() || self.station_view_dirty;
            if entering {
                self.pending
                    .push(packets::station_avatar_description(&peer.profile));
                self.pending
                    .push(packets::station_avatar_room(id, peer.pose.room, u32::MAX));
            }
            if entering || (broadcast && previous != Some(&peer.pose)) {
                self.pending
                    .push(packets::station_avatar_pose(id, &peer.pose));
                self.station_peers.insert(id, peer.pose.clone());
            }
        }
        self.station_view_dirty = false;
        if broadcast {
            self.last_station_broadcast = Instant::now();
        }
    }
}
