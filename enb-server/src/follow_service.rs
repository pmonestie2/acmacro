//! Session ownership of native follow mode. The coordinator refreshes just one
//! selected peer contact per tick; MOB motion comes from the shared simulation.
use super::*;
use crate::following::{FollowContact, FOLLOW_VISIBILITY};

impl PlayerSession {
    pub(super) fn stop_following(&mut self) {
        if self.following {
            self.following = false;
            self.follow_peer = None;
            self.motion.stop();
            self.last_motion_report = None;
            println!(
                "Following stopped: {}; target={}",
                self.account.character.name, self.target
            );
        }
    }

    pub(super) fn begin_following(&mut self) -> io::Result<()> {
        self.stop_following();
        if !self.logged_in
            || self.sector.station.is_some()
            || self.hull <= 0.
            || self.warp.active()
            || self.gate_transit.is_some()
        {
            return Err(invalid(
                "Follow a target alive in space, outside warp and gate transitions.",
            ));
        }
        if self.target == self.player_id()
            || (!self.peer_ids.contains(&self.target) && self.mob_follow_contact()?.is_none())
        {
            return Err(invalid("Select a visible ship or living MOB to follow."));
        }
        self.cancel_mining("Following target.")?;
        self.cancel_looting("Following target.")?;
        self.detach_formation();
        self.motion.stop();
        self.following = true;
        self.last_motion_report = None;
        println!(
            "Following started: {}; target={}",
            self.account.character.name, self.target
        );
        Ok(())
    }

    fn mob_follow_contact(&self) -> io::Result<Option<FollowContact>> {
        let Some(index) = self
            .target
            .checked_sub(MOB_ID)
            .filter(|index| (*index as usize) < self.mob_states.len())
        else {
            return Ok(None);
        };
        let combat = lock_combat(&self.combat)?;
        Ok(combat
            .actors
            .get(index as usize)
            .filter(|mob| self.mob_visible_in_scan(index as usize, mob))
            .map(|mob| FollowContact {
                position: mob.position,
                // Native FollowTarget reads signed Object::Velocity(). The
                // legacy scalar is a distance-per-tick magnitude and loses
                // reverse movement when a pursuing NPC backs away.
                speed: mob.motion.speed,
                radius: mob.definition.radius,
            }))
    }

    pub(super) fn advance_following(&mut self, dt: f32) -> io::Result<bool> {
        if !self.following {
            return Ok(false);
        }
        if !self.logged_in
            || self.hull <= 0.
            || self.warp.active()
            || self.gate_transit.is_some()
            || self.formation_leader.is_some()
        {
            self.stop_following();
            return Ok(false);
        }
        let contact = if self.peer_ids.contains(&self.target) {
            self.follow_peer
        } else {
            self.mob_follow_contact()?
        };
        let Some(contact) =
            contact.filter(|contact| contact.range_to(self.position) <= FOLLOW_VISIBILITY)
        else {
            self.stop_following();
            return Ok(false);
        };
        let maximum = self.motion.max_speed * if self.skills.cloak_rank == 1 { 0.5 } else { 1. };
        contact.advance(
            &mut self.motion,
            &mut self.position,
            &self.sector.bounds,
            dt,
            maximum,
        )?;
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_follow_tracks_mobs_ignores_prediction_and_manual_stop_cancels() {
        let mut ship = super::super::tests::session();
        ship.logged_in = true;
        ship.target = MOB_ID;
        {
            let mut combat = lock_combat(&ship.combat).unwrap();
            combat.actors[0].position = [3000., 0., 0.];
            combat.actors[0].speed = 50.;
            combat.actors[0].motion.speed = 50.;
        }
        ship.position = [0.; 3];
        let mut command = ship.player_id().to_le_bytes().to_vec();
        command.push(12);
        ship.handle_input(0x14, &command).unwrap();
        assert!(ship.following);
        let before = ship.position;
        ship.advance_player(1.).unwrap();
        assert!(ship.position[0] > before[0]);
        assert!(ship.motion.speed > 0.);
        let authoritative = ship.position;
        let report: Vec<u8> = [1000f32, 0., 0.]
            .iter()
            .flat_map(|v| v.to_le_bytes())
            .collect();
        ship.handle_input(0x1004, &report).unwrap();
        assert_eq!(ship.position, authoritative);
        command[4] = 4;
        ship.handle_input(0x14, &command).unwrap();
        assert!(!ship.following);
        assert_eq!(ship.motion.set_speed, 0.);
        ship.target = 100000;
        assert!(ship.begin_following().is_err());
        ship.target = MOB_ID;
        ship.begin_following().unwrap();
        lock_combat(&ship.combat).unwrap().actors[0].damage(1_000_000.);
        ship.advance_player(0.).unwrap();
        assert!(!ship.following);
        assert_eq!(ship.motion.speed, 0.);
    }

    #[test]
    fn following_a_reversing_npc_uses_its_signed_velocity() {
        let mut ship = super::super::tests::session();
        ship.logged_in = true;
        ship.target = MOB_ID;
        ship.position = [0.; 3];
        {
            let mut combat = lock_combat(&ship.combat).unwrap();
            let mob = &mut combat.actors[0];
            mob.position = [1000., 0., 0.];
            mob.speed = 50.; // Legacy movement magnitude remains positive.
            mob.motion.speed = -50.;
        }
        ship.begin_following().unwrap();
        ship.advance_player(1.).unwrap();
        assert!(ship.following);
        assert_eq!(ship.motion.speed, -50.);
        assert_eq!(ship.position, [-50., 0., 0.]);
    }

    #[test]
    fn follow_selected_peer_uses_visibility_and_survives_restart_without_persisting_contact() {
        let mut ship = super::super::tests::journal_ship();
        let mut peer = ship.peer_snapshot().unwrap();
        peer.profile.game_id += 1;
        peer.position[0] += 3000.;
        peer.motion.speed = 100.;
        ship.target = peer.profile.game_id;
        ship.update_peer_view(&[peer.clone()], &[]);
        ship.begin_following().unwrap();
        ship.update_peer_view(&[peer.clone()], &[]);
        ship.advance_player(1.).unwrap();
        assert!(ship.following);
        ship.save().unwrap();
        ship.checkpoint_session(true).unwrap();
        let data: serde_json::Value =
            serde_json::from_slice(&fs::read(ship.state_dir.join("session.local.json")).unwrap())
                .unwrap();
        assert_eq!(data["following"], true);
        assert!(data.get("follow_peer").is_none());
        let mut resumed =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(resumed.following);
        assert!(resumed.follow_peer.is_none());
        resumed.update_peer_view(&[peer.clone()], &[]);
        resumed.advance_player(1.).unwrap();
        assert!(resumed.following);
        peer.cloaked = true;
        resumed.update_peer_view(&[peer], &[]);
        resumed.advance_player(1.).unwrap();
        assert!(!resumed.following);
        assert_eq!(resumed.motion.speed, 0.);
    }
}
