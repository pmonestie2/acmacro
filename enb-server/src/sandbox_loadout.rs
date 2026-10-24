//! Opt-in local test grants. A durable inventory revision makes a grant apply
//! once, including to existing pilots, without refilling money on every login.
use crate::account::CharacterProfile;
use crate::inventory::{Inventory, ItemInstance};
use crate::items::ItemCatalog;
use crate::protocol::invalid;
use crate::skills::SkillCatalog;
use serde::Deserialize;
use std::io::Write;
use std::path::Path;
use std::{fs, io};

#[derive(Deserialize)]
pub struct SandboxLoadout {
    pub revision: u32,
    pub credits: u64,
    pub equipment: Vec<GrantItem>,
    #[serde(default)]
    pub by_class: std::collections::BTreeMap<String, Vec<GrantItem>>,
}

#[derive(Deserialize)]
pub struct GrantItem {
    pub slot: usize,
    pub template: u32,
    pub quality: f32,
}

impl SandboxLoadout {
    pub fn apply(
        &self,
        inventory: &Inventory,
        profile: &CharacterProfile,
        items: &ItemCatalog,
        skills: &SkillCatalog,
    ) -> io::Result<Option<Inventory>> {
        let equipment = self
            .by_class
            .get(profile.class().code())
            .unwrap_or(&self.equipment);
        if self.revision == 0 || equipment.is_empty() {
            return Err(invalid(
                "Invalid sandbox grant revision or empty equipment.",
            ));
        }
        if inventory.sandbox_grant >= self.revision {
            return Ok(None);
        }
        let mut candidate = inventory.clone();
        let mut seen = std::collections::HashSet::new();
        for grant in equipment {
            if grant.slot >= candidate.equipped.len() || !seen.insert(grant.slot) {
                return Err(invalid("Invalid or duplicate sandbox equipment slot."));
            }
            profile
                .class()
                .check_mount(grant.slot, items.get(grant.template)?.item_type)?;
            items
                .get(grant.template)?
                .check_requirements(profile, skills, [50, 50, 50])?;
            let mut item = ItemInstance::stock(grant.template, 1);
            item.quality = grant.quality;
            if candidate.equipped[grant.slot].as_ref() == Some(&item) {
                continue;
            }
            if let Some(ammo) = candidate.ammunition[grant.slot].take() {
                let empty = candidate
                    .vault
                    .iter_mut()
                    .find(|slot| slot.is_none())
                    .ok_or_else(|| invalid("No vault space to preserve loaded ammunition."))?;
                *empty = Some(ammo);
            }
            if let Some(previous) = candidate.equipped[grant.slot].take() {
                let empty = candidate
                    .vault
                    .iter_mut()
                    .find(|slot| slot.is_none())
                    .ok_or_else(|| invalid("No vault space to preserve replaced sandbox gear."))?;
                *empty = Some(previous);
            }
            candidate.equipped[grant.slot] = Some(item);
        }
        candidate.credits = candidate.credits.max(self.credits);
        candidate.sandbox_grant = self.revision;
        candidate.validate(items)?;
        Ok(Some(candidate))
    }
}

/// Missing config leaves ordinary fixtures and servers unchanged. Persist the
/// equipment, preserved gear, credit floor and revision as one replacement.
pub fn provision(
    root: &Path,
    directory: &Path,
    inventory: &Inventory,
    profile: &CharacterProfile,
    items: &ItemCatalog,
    skills: &SkillCatalog,
) -> io::Result<Option<Inventory>> {
    let path = root.join("sandbox_loadout.json");
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let grant: SandboxLoadout = serde_json::from_slice(&bytes)?;
    let Some(candidate) = grant.apply(inventory, profile, items, skills)? else {
        return Ok(None);
    };
    fs::create_dir_all(directory)?;
    let temporary = directory.join("inventory.grant.next");
    let mut file = fs::File::create(&temporary)?;
    file.write_all(&serde_json::to_vec_pretty(&candidate)?)?;
    file.sync_all()?;
    drop(file);
    fs::rename(temporary, directory.join("inventory.json"))?;
    println!(
        "Sandbox grant: {}; revision={}; credits={}",
        profile.name, grant.revision, candidate.credits
    );
    Ok(Some(candidate))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::account::Account;
    use crate::equipment_stats::EquipmentStats;

    #[test]
    fn nine_class_presets_honor_own_skills_restrictions_and_mount_counts() {
        let items = ItemCatalog::load().unwrap();
        let grant: SandboxLoadout =
            serde_json::from_str(include_str!("../data/sandbox_loadout.json")).unwrap();
        for code in ["te", "tt", "ts", "jd", "js", "je", "pw", "pp", "ps"] {
            let class = crate::classes::CharacterClass::parse(code).unwrap();
            let mut pilot = Account::create(code.into(), "pass", code.to_uppercase()).unwrap();
            pilot.character.race = class.race();
            pilot.character.profession = class.profession();
            let skills = SkillCatalog::for_profile(&pilot.character).unwrap();
            let inventory = Inventory::starter_for(&pilot.character, &items).unwrap();
            inventory
                .validate_for_profile(&pilot.character, &items, &skills)
                .unwrap();
            let granted = grant
                .apply(&inventory, &pilot.character, &items, &skills)
                .unwrap()
                .unwrap();
            let stats = EquipmentStats::derive(&granted, &items, &skills).unwrap();
            assert_eq!(stats.weapons.len(), class.weapons());
            assert_eq!(stats.ship.weapon_mounts, class.weapons());
            assert_eq!(stats.ship.device_mounts, class.devices());
            assert_eq!(
                granted.equipped[9..].iter().flatten().count(),
                class.devices()
            );
            assert_eq!(granted.credits, 100_000_000);
            for (slot, item) in granted
                .equipped
                .iter()
                .enumerate()
                .filter_map(|(s, i)| i.as_ref().map(|i| (s, i)))
            {
                let template = items.get(item.template).unwrap();
                class.check_mount(slot, template.item_type).unwrap();
                template
                    .check_requirements(&pilot.character, &skills, [50, 50, 50])
                    .unwrap();
            }
        }
    }

    #[test]
    fn elite_je_grant_preserves_all_owned_items_is_legal_and_is_not_repeated() {
        let items = ItemCatalog::load().unwrap();
        let skills = SkillCatalog::load().unwrap();
        let pilot = Account::create("test".into(), "pass", "Pilot".into()).unwrap();
        let grant: SandboxLoadout =
            serde_json::from_str(include_str!("../data/sandbox_loadout.json")).unwrap();
        let mut original = Inventory::starter(&items).unwrap();
        original.cargo[0] = Some(ItemInstance::stock(2845, 5));
        original.vault[0] = Some(ItemInstance::stock(2866, 5));
        let upgraded = grant
            .apply(&original, &pilot.character, &items, &skills)
            .unwrap()
            .unwrap();
        assert_eq!(upgraded.cargo, original.cargo);
        assert_eq!(upgraded.vault[0], original.vault[0]);
        for item in original.equipped.iter().flatten() {
            assert!(upgraded.vault.contains(&Some(item.clone())));
        }
        assert_eq!(upgraded.credits, 100_000_000);
        let stats = EquipmentStats::derive(&upgraded, &items, &skills).unwrap();
        let starter = EquipmentStats::derive(&original, &items, &skills).unwrap();
        assert!(stats.ship.energy_capacity > starter.ship.energy_capacity);
        assert!(stats.ship.shield_capacity > starter.ship.shield_capacity);
        assert_eq!(stats.weapons.len(), 3);
        assert!(stats.weapons.iter().all(|weapon| weapon.damage > 1000.));
        let mut spent: Inventory =
            serde_json::from_slice(&serde_json::to_vec(&upgraded).unwrap()).unwrap();
        spent.credits = 12;
        spent.equipped[3] = None;
        assert!(grant
            .apply(&spent, &pilot.character, &items, &skills)
            .unwrap()
            .is_none());
        let mut full = original.clone();
        full.vault.fill(Some(ItemInstance::stock(2845, 1)));
        assert!(grant
            .apply(&full, &pilot.character, &items, &skills)
            .is_err());
        assert_eq!(full.equipped, original.equipped);
        assert_eq!(full.credits, original.credits);
    }
}
