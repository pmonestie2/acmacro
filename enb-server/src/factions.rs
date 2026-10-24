//! Public faction matrix plus immutable per-character reactions. Native setup
//! uses base_value (not the dump's mutable current_value), clips PDA defaults,
//! then grants7500 with the character's own class faction on first creation.
use crate::classes::CharacterClass;
use crate::protocol::invalid;
use serde::Deserialize;
use std::collections::{BTreeMap, HashMap};
use std::io;
use std::sync::{Arc, OnceLock};

#[derive(Clone, Debug, Deserialize)]
pub struct FactionDefinition {
    pub id: u32,
    pub name: String,
    pub pda: bool,
    pub pda_text: String,
}

#[derive(Deserialize)]
struct MatrixEntry {
    source: u32,
    target: u32,
    base: f32,
}

#[derive(Deserialize)]
struct CatalogFile {
    factions: Vec<FactionDefinition>,
    relationships: Vec<MatrixEntry>,
}

struct CatalogData {
    factions: BTreeMap<u32, FactionDefinition>,
    relationships: HashMap<(u32, u32), f32>,
}

#[derive(Clone)]
pub struct FactionCatalog(Arc<CatalogData>);

/// Shared cheaply with each sector's player threat. It changes only after a
/// durable inventory/reputation transaction or character-class refresh.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct FactionReactions {
    pub class_faction: u32,
    pub affiliation: String,
    pub identifier: String,
    values: BTreeMap<u32, f32>,
}

impl FactionReactions {
    pub fn neutral() -> Arc<Self> {
        Arc::new(Self::default())
    }
    pub fn standing(&self, faction: u32) -> f32 {
        self.values.get(&faction).copied().unwrap_or(0.)
    }
    pub fn allows_spontaneous_attack(&self, faction: u32) -> bool {
        self.standing(faction) <= 0.
    }
    /// MOB::SendRelationship uses different thresholds from acquisition.
    /// Unassigned MOBs and the current hostility target are red by default.
    pub fn relationship(&self, faction: u32, attacking: bool) -> u32 {
        if faction == 0 || attacking || self.standing(faction) <= -2000. {
            0
        } else if self.standing(faction) >= 2000. {
            2
        } else {
            1
        }
    }
}

impl FactionCatalog {
    pub fn load() -> io::Result<Self> {
        static CATALOG: OnceLock<Arc<CatalogData>> = OnceLock::new();
        if let Some(data) = CATALOG.get() {
            return Ok(Self(Arc::clone(data)));
        }
        let file: CatalogFile = serde_json::from_str(include_str!("../data/factions.json"))?;
        let mut factions = BTreeMap::new();
        for faction in file.factions {
            if faction.id == 0
                || faction.name.is_empty()
                || !faction.name.is_ascii()
                || !faction.pda_text.is_ascii()
                || faction.name.len() >= 64
                || faction.pda_text.len() >= 64
                || factions.insert(faction.id, faction).is_some()
            {
                return Err(invalid("Invalid public faction identity or native text."));
            }
        }
        if factions.values().filter(|entry| entry.pda).count() > 32 {
            return Err(invalid("Public factions exceed native PDA capacity."));
        }
        let mut relationships = HashMap::new();
        for entry in file.relationships {
            if !entry.base.is_finite()
                || relationships
                    .insert((entry.source, entry.target), entry.base)
                    .is_some()
            {
                return Err(invalid("Invalid or duplicate faction matrix entry."));
            }
        }
        let _ = CATALOG.set(Arc::new(CatalogData {
            factions,
            relationships,
        }));
        Ok(Self(Arc::clone(CATALOG.get().unwrap())))
    }
    pub fn faction(&self, id: u32) -> Option<&FactionDefinition> {
        self.0.factions.get(&id)
    }
    pub fn visible(&self) -> impl Iterator<Item = &FactionDefinition> {
        self.0.factions.values().filter(|faction| faction.pda)
    }
    pub fn validate_overrides(&self, values: &BTreeMap<u32, f32>) -> io::Result<()> {
        if values.len() > 32
            || values.iter().any(|(id, value)| {
                !value.is_finite()
                    || !(-9000. ..=9999.).contains(value)
                    || self.faction(*id).is_none_or(|faction| !faction.pda)
            })
        {
            return Err(invalid("Invalid player faction standing."));
        }
        Ok(())
    }
    pub fn reactions(
        &self,
        class: CharacterClass,
        overrides: &BTreeMap<u32, f32>,
    ) -> io::Result<Arc<FactionReactions>> {
        self.validate_overrides(overrides)?;
        let own = self
            .faction(class.faction())
            .ok_or_else(|| invalid("Character faction is absent from public catalog."))?;
        let mut values: BTreeMap<u32, f32> = self
            .0
            .relationships
            .iter()
            .filter(|((source, _), _)| *source == own.id)
            .map(|((_, target), value)| (*target, *value))
            .collect();
        for faction in self.visible() {
            let initial = if faction.id == own.id {
                7500.
            } else {
                values
                    .get(&faction.id)
                    .copied()
                    .unwrap_or(0.)
                    .clamp(-9000., 9999.)
            };
            values.insert(
                faction.id,
                overrides.get(&faction.id).copied().unwrap_or(initial),
            );
        }
        Ok(Arc::new(FactionReactions {
            class_faction: own.id,
            affiliation: own.name.clone(),
            identifier: own.pda_text.clone(),
            values,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_class_defaults_and_durable_overrides_do_not_refill_reputation() {
        let catalog = FactionCatalog::load().unwrap();
        assert_eq!(catalog.0.factions.len(), 28);
        assert_eq!(catalog.0.relationships.len(), 809);
        for code in ["te", "tt", "ts", "jd", "js", "je", "pw", "pp", "ps"] {
            let class = CharacterClass::parse(code).unwrap();
            let initial = catalog.reactions(class, &BTreeMap::new()).unwrap();
            assert_eq!(initial.standing(class.faction()), 7500.);
            assert!(!initial.allows_spontaneous_attack(class.faction()));
            assert_eq!(initial.relationship(class.faction(), false), 2);
            assert_eq!(initial.relationship(class.faction(), true), 0);
            let changed = catalog
                .reactions(class, &BTreeMap::from([(class.faction(), -2500.)]))
                .unwrap();
            assert_eq!(changed.standing(class.faction()), -2500.);
            assert!(changed.allows_spontaneous_attack(class.faction()));
        }
    }
    #[test]
    fn matrix_uses_public_base_values_and_preserves_native_threshold_boundaries() {
        let catalog = FactionCatalog::load().unwrap();
        let class = CharacterClass::parse("pw").unwrap();
        let initial = catalog.reactions(class, &BTreeMap::new()).unwrap();
        for (&(source, target), &base) in &catalog.0.relationships {
            if source == class.faction() && target != class.faction() {
                let expected = if catalog.faction(target).is_some_and(|faction| faction.pda) {
                    base.clamp(-9000., 9999.)
                } else {
                    base
                };
                assert_eq!(initial.standing(target), expected);
            }
        }
        for (value, relation, acquisition) in [
            (-2000., 0, true),
            (-1999., 1, true),
            (0., 1, true),
            (1., 1, false),
            (1999., 1, false),
            (2000., 2, false),
        ] {
            let changed = catalog
                .reactions(class, &BTreeMap::from([(22, value)]))
                .unwrap();
            assert_eq!(changed.relationship(22, false), relation);
            assert_eq!(changed.allows_spontaneous_attack(22), acquisition);
        }
        assert_eq!(initial.standing(0), 0.);
        assert_eq!(initial.relationship(0, false), 0);
        assert!(catalog
            .validate_overrides(&BTreeMap::from([(0, 1.)]))
            .is_err());
        assert!(catalog
            .validate_overrides(&BTreeMap::from([(22, f32::NAN)]))
            .is_err());
    }
}
