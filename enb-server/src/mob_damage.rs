//! Incoming NPC damage commits HP and a receipt together before external
//! reflection or feedback. The sector retains the shot until acknowledgment.
use super::*;
use crate::combat_damage::{
    damage_control_fraction, player_damage, DamageResult, DamageType, PlayerDefense,
};
use crate::npc_projectiles::{MobHit, MobLaunchVisual};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub(super) struct MobDamageReceipt {
    pub(super) sector: u32,
    pub(super) sequence: u64,
    reflection: f32,
}

pub(super) fn valid_receipts(receipts: &[MobDamageReceipt]) -> bool {
    let mut identities = HashSet::new();
    receipts.len() <= 16384
        && receipts.iter().all(|receipt| {
            receipt.sector > 0
                && receipt.sequence > 0
                && receipt.reflection.is_finite()
                && (0. ..=50.).contains(&receipt.reflection)
                && identities.insert((receipt.sector, receipt.sequence))
        })
}

pub(super) struct DamagePlan {
    pub(super) result: DamageResult,
    pub(super) energy: f32,
    pub(super) reflection: f32,
    pub(super) motion: Motion,
    pub(super) warp: Warp,
    pub(super) warp_event: Option<WarpEvent>,
}

impl PlayerSession {
    fn plan_mob_damage(&self, damage: f32, kind: DamageType) -> io::Result<DamagePlan> {
        let mut reduced = damage;
        let mut energy = self.energy;
        let mut reflection = 0.;
        if self.skills.environment_rank > 0 {
            reduced *= if self.skills.environment_rank >= 3 {
                0.9
            } else {
                0.95
            };
            if self.skills.environment_rank >= 5 {
                let maximum = match self.skills.environment_rank {
                    5 => 10.,
                    6 => 25.,
                    _ => 50.,
                };
                reflection = reduced.min(maximum);
            }
            if self.skills.environment_rank >= 6 {
                energy =
                    (energy + (reduced * 0.05).min(10.)).min(self.equipment.ship.energy_capacity);
            }
        }
        let profile = &self.account.character;
        let damage_control = if profile.profession == 0 && matches!(profile.race, 0 | 2) {
            damage_control_fraction(self.skill_catalog.level(17))
        } else {
            0.
        };
        let result = player_damage(
            reduced,
            kind,
            PlayerDefense {
                shield: self.shield,
                hull: self.hull,
                deflect_percent: self.equipment.ship.deflects[kind.index()],
                damage_control,
            },
        )?;
        let mut motion = self.motion.clone();
        let mut warp = self.warp.clone();
        let warp_event = (result.hull_branch && warp.active())
            .then(|| warp.finish(FinishReason::Cancelled, &mut motion));
        if result.hull == 0. {
            motion.stop();
        }
        Ok(DamagePlan {
            result,
            energy,
            reflection,
            motion,
            warp,
            warp_event,
        })
    }

    /// The frozen receipt survives a lost world acknowledgment and out-of-order
    /// projectile arrivals. A sequence high-water mark would lose slower shots.
    pub(crate) fn receive_mob_hit(&mut self, hit: &MobHit) -> io::Result<f32> {
        if !hit.valid() || hit.target != self.player_id() {
            return Err(invalid("Invalid incoming NPC shot identity."));
        }
        if let Some(receipt) = self
            .mob_damage_receipts
            .iter()
            .find(|receipt| receipt.sector == hit.sector && receipt.sequence == hit.sequence)
        {
            return Ok(receipt.reflection);
        }
        if !self.logged_in
            || self.hull <= 0.
            || self.sector.id != hit.sector
            || self.session_epoch != hit.target_epoch
        {
            return Ok(0.);
        }
        if self.transition_pending.is_some() || self.mob_damage_receipts.len() >= 16384 {
            return Err(invalid(
                "Incoming damage is waiting for pending durable state.",
            ));
        }
        let kind = DamageType::from_index(hit.damage_type)?;
        let plan = self.plan_mob_damage(hit.damage, kind)?;
        let receipt = MobDamageReceipt {
            sector: hit.sector,
            sequence: hit.sequence,
            reflection: plan.reflection,
        };
        // Acquire the sole external cleanup dependency before committing. No
        // mutation, packet, mining cancellation or reflection precedes the write.
        let resources = Arc::clone(&self.resource_field);
        let mut field = if self.mining.is_some() {
            Some(lock_resources(&resources)?)
        } else {
            None
        };
        self.checkpoint_mob_damage(&plan, &receipt)?;
        self.mob_damage_receipts.push(receipt);
        self.shield = plan.result.shield;
        self.hull = plan.result.hull;
        self.energy = plan.energy;
        self.warp = plan.warp;
        self.motion = plan.motion;
        if let Some(cast) = self.mining.take() {
            field
                .as_mut()
                .expect("mining dependency acquired")
                .release(cast.node, self.player_id());
            self.emit_harvest_visual(Frame::new(0xf, cast.effect.to_le_bytes().to_vec()));
            if let Some(article) = cast.article {
                self.emit_harvest_visual(Frame::new(7, article.to_le_bytes().to_vec()));
            }
            self.pending
                .push(packets::message("Incoming fire interrupted prospecting."));
        }
        if let Some(event) = plan.warp_event {
            self.emit_warp_event(event);
        }
        if self.hull == 0. {
            self.stop_weapons();
            self.stop_following();
            self.detach_formation();
            self.motion.stop();
            self.pending
                .push(packets::incapacitated(self.player_id(), true));
        }
        self.pending.push(packets::client_damage(
            self.player_id(),
            MOB_ID + hit.attacker_index as u32,
            plan.result.reported_damage,
            plan.result.reported_damage - hit.damage,
            kind.index() as u32,
            1,
        ));
        self.pending.push(packets::ship_resources(
            self.player_id(),
            &self.equipment.ship,
            Some(self.energy),
            Some(self.shield),
            Some(self.hull),
        ));
        Ok(plan.reflection)
    }

    /// Called only after the authoritative sector has durably retired the shot.
    /// Persisting this cleanup can wait for the ordinary checkpoint; retaining
    /// an old receipt after a crash cannot recreate an acknowledged shot.
    pub(crate) fn forget_mob_hit_receipt(&mut self, sector: u32, sequence: u64) {
        self.mob_damage_receipts
            .retain(|receipt| receipt.sector != sector || receipt.sequence != sequence);
    }

    pub(crate) fn publish_mob_launch(&mut self, visual: &MobLaunchVisual) {
        if !self.logged_in
            || self.sector.id != visual.sector
            || self.sector.station.is_some()
            || !self
                .mob_visible
                .get(visual.attacker_index)
                .copied()
                .unwrap_or(false)
            || (visual.target != self.player_id() && !self.visible_peers.contains(&visual.target))
        {
            return;
        }
        self.pending.push(packets::weapon_effect(
            MOB_ID + visual.attacker_index as u32,
            visual.target,
            visual.effect,
            self.next_effect,
            self.tick_ms(),
            visual.duration_ms.min(u32::from(u16::MAX)) as u16,
        ));
        self.next_effect = self.next_effect.wrapping_add(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ship() -> PlayerSession {
        let mut ship = super::super::tests::session();
        ship.logged_in = true;
        ship.selected_peer = Some("127.0.0.1".parse().unwrap());
        ship.endpoint = Some("127.0.0.1:22343".parse().unwrap());
        ship.shield = 45.;
        ship.hull = 100.;
        ship.save().unwrap();
        ship
    }

    fn hit(ship: &PlayerSession, sequence: u64) -> MobHit {
        let combat = lock_combat(&ship.combat).unwrap();
        let mob = &combat.actors[0];
        MobHit {
            sector: ship.sector.id,
            sequence,
            target: ship.player_id(),
            target_epoch: ship.session_epoch,
            attacker_index: 0,
            attacker_source: mob.definition.source_id,
            attacker_template: mob.definition.template_id,
            attacker_generation: mob.death_generation,
            damage: 10.,
            damage_type: 3,
        }
    }

    #[test]
    fn hp_and_out_of_order_receipts_restore_together_and_replays_never_repeat_feedback() {
        let mut ship = ship();
        let slow = hit(&ship, 1);
        let fast = hit(&ship, 2);
        ship.receive_mob_hit(&fast).unwrap();
        ship.receive_mob_hit(&slow).unwrap();
        assert_eq!(ship.shield, 25.);
        let mut restored =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        assert!(restored.logged_in);
        assert_eq!(restored.shield, 25.);
        let pending = restored.pending.len();
        restored.receive_mob_hit(&slow).unwrap();
        restored.receive_mob_hit(&fast).unwrap();
        assert_eq!(restored.shield, 25.);
        assert_eq!(restored.pending.len(), pending);
        restored.forget_mob_hit_receipt(slow.sector, slow.sequence);
        assert_eq!(restored.mob_damage_receipts.len(), 1);
    }

    #[test]
    fn failed_damage_commit_preserves_resources_motion_receipts_and_external_mob_health() {
        let mut ship = ship();
        ship.skills.environment_rank = 7;
        ship.energy = 10.;
        let hit = hit(&ship, 1);
        let blocked = ship.state_dir.join("session.local.json.next");
        fs::create_dir(&blocked).unwrap();
        let pending = ship.pending.len();
        let original = lock_combat(&ship.combat).unwrap().actors[0].health;
        assert!(ship.receive_mob_hit(&hit).is_err());
        assert_eq!((ship.shield, ship.hull, ship.energy), (45., 100., 10.));
        assert!(ship.mob_damage_receipts.is_empty());
        assert_eq!(ship.pending.len(), pending);
        assert_eq!(
            lock_combat(&ship.combat).unwrap().actors[0].health,
            original
        );
        fs::remove_dir(blocked).unwrap();
        assert_eq!(ship.receive_mob_hit(&hit).unwrap(), 9.);
        assert_eq!((ship.shield, ship.energy), (36., 10.45));
        assert_eq!(
            lock_combat(&ship.combat).unwrap().actors[0].health,
            original
        );
        assert_eq!(ship.receive_mob_hit(&hit).unwrap(), 9.);
        assert_eq!((ship.shield, ship.energy), (36., 10.45));
    }

    #[test]
    fn forged_identity_wrong_sector_and_invalid_receipts_do_not_damage_a_player() {
        let mut ship = ship();
        let mut shot = hit(&ship, 1);
        shot.target += 1;
        assert!(ship.receive_mob_hit(&shot).is_err());
        shot.target -= 1;
        shot.sector += 1;
        assert_eq!(ship.receive_mob_hit(&shot).unwrap(), 0.);
        assert_eq!(ship.shield, 45.);
        assert!(valid_receipts(&[]));
        let receipt = MobDamageReceipt {
            sector: 1076,
            sequence: 1,
            reflection: 0.,
        };
        assert!(!valid_receipts(&[receipt.clone(), receipt]));
    }

    #[test]
    fn a_fresh_login_cannot_inherit_a_previous_transports_unacknowledged_hit() {
        let mut ship = ship();
        let original = hit(&ship, 1);
        ship.receive_mob_hit(&original).unwrap();
        assert_eq!(ship.shield, 35.);
        ship.logged_in = false;
        ship.forget_session().unwrap();
        let mut fresh =
            PlayerSession::new(ship.sector.clone(), ship.bind, ship.state_dir.clone()).unwrap();
        fresh.logged_in = true;
        fresh.selected_peer = Some("127.0.0.1".parse().unwrap());
        fresh.endpoint = Some("127.0.0.1:22343".parse().unwrap());
        assert_ne!(fresh.session_epoch, original.target_epoch);
        let shield = fresh.shield;
        assert_eq!(fresh.receive_mob_hit(&original).unwrap(), 0.);
        assert_eq!(fresh.shield, shield);
        assert!(fresh.mob_damage_receipts.is_empty());
    }
}
