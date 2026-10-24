//! Read-only public maps shared by sessions. Actor simulation remains mutable
//! in shared SectorCombat instances; this catalog resolves IDs to validated content.
use crate::content::{Sector, Station};
use crate::mob::MobDefinition;
use crate::protocol::invalid;
use std::collections::HashMap;
use std::io;
use std::sync::{Arc, OnceLock};

#[derive(Clone)]
pub struct SectorCatalog(Arc<HashMap<u32, Sector>>);

impl SectorCatalog {
    pub fn load() -> io::Result<Self> {
        static CATALOG: OnceLock<Arc<HashMap<u32, Sector>>> = OnceLock::new();
        if let Some(catalog) = CATALOG.get() {
            return Ok(Self(Arc::clone(catalog)));
        }
        let maps: Vec<Sector> = serde_json::from_str(include_str!("../data/universe.json"))?;
        let mut catalog = HashMap::new();
        for sector in maps {
            sector
                .validate()
                .map_err(|error| invalid(&format!("sector {}: {error}", sector.id)))?;
            catalog.insert(sector.id, sector);
        }
        let alpha: Sector = serde_json::from_str(include_str!("../data/asteroid_belt_alpha.json"))?;
        alpha.validate()?;
        catalog.insert(alpha.id, alpha);
        let stations: Vec<Station> = serde_json::from_str(include_str!("../data/stations.json"))?;
        for station in stations {
            let parent = catalog
                .get(&station.parent)
                .ok_or_else(|| invalid("Station parent sector is missing."))?;
            let sector = Sector {
                id: station.id,
                name: station.name.clone(),
                system: parent.system.clone(),
                backdrop_asset: parent.backdrop_asset,
                bounds: parent.bounds.clone(),
                navpoints: Vec::new(),
                mobs: Vec::new(),
                resources: Vec::new(),
                station: Some(station),
            };
            sector.validate()?;
            if catalog.insert(sector.id, sector).is_some() {
                return Err(invalid("Duplicate station sector identity."));
            }
        }
        let _ = CATALOG.set(Arc::new(catalog));
        Ok(Self(Arc::clone(CATALOG.get().unwrap())))
    }
    pub fn sector(&self, id: u32) -> io::Result<Sector> {
        self.0
            .get(&id)
            .cloned()
            .ok_or_else(|| invalid("Destination is not a hosted sector."))
    }

    /// Developer navigation accepts native IDs or punctuation-insensitive
    /// names. Ambiguity is reported rather than selecting a random map from
    /// the catalog's hash-table iteration order.
    pub fn resolve_sector(&self, query: &str) -> io::Result<Sector> {
        if let Some(id) = parse_identity(query) {
            return self.sector(id);
        }
        // The game's conventional BBW abbreviation splits "Blackbeard" into
        // two words, unlike the archived sector name "Blackbeards Wake".
        if normalized(query) == "bbw" {
            return self.sector(3605);
        }
        let id = resolve_name(
            query,
            self.0
                .values()
                .map(|sector| (sector.id, sector.name.as_str())),
        )?;
        self.sector(id)
    }

    /// A public MOB may have many placements. Choose its lowest source ID as
    /// the deterministic template representative; the spawn command supplies a
    /// fresh identity and location without changing imported map content.
    pub fn resolve_mob(&self, query: &str) -> io::Result<MobDefinition> {
        let mut templates = std::collections::BTreeMap::<u32, &MobDefinition>::new();
        for mob in self.0.values().flat_map(|sector| &sector.mobs) {
            templates
                .entry(mob.template_id)
                .and_modify(|selected| {
                    if mob.source_id < selected.source_id {
                        *selected = mob;
                    }
                })
                .or_insert(mob);
        }
        let identity = if let Some(id) = parse_identity(query) {
            id
        } else {
            resolve_name(
                query,
                templates.iter().map(|(id, mob)| (*id, mob.name.as_str())),
            )?
        };
        templates
            .get(&identity)
            .cloned()
            .cloned()
            .ok_or_else(|| invalid("No hosted public MOB has that template ID."))
    }
}

fn parse_identity(query: &str) -> Option<u32> {
    let query = query.trim();
    if let Some(hex) = query
        .strip_prefix("0x")
        .or_else(|| query.strip_prefix("0X"))
    {
        u32::from_str_radix(hex, 16).ok()
    } else {
        query.parse().ok()
    }
}

fn normalized(name: &str) -> String {
    name.chars()
        .filter(char::is_ascii_alphanumeric)
        .map(|letter| letter.to_ascii_lowercase())
        .collect()
}

fn initials(name: &str) -> String {
    name.split(|letter: char| !letter.is_ascii_alphanumeric())
        .filter_map(|word| word.chars().next())
        .map(|letter| letter.to_ascii_lowercase())
        .collect()
}

fn resolve_name<'a>(
    query: &str,
    candidates: impl Iterator<Item = (u32, &'a str)>,
) -> io::Result<u32> {
    let query = normalized(query);
    if query.is_empty() {
        return Err(invalid("Supply a name or native ID."));
    }
    let mut matches: Vec<(u8, u32, &str)> = candidates
        .filter_map(|(id, name)| {
            let key = normalized(name);
            let rank = if key == query {
                0
            } else if initials(name) == query {
                1
            } else if key.starts_with(&query) {
                2
            } else if key.contains(&query) {
                3
            } else {
                return None;
            };
            Some((rank, id, name))
        })
        .collect();
    matches.sort_by_key(|(rank, id, _)| (*rank, *id));
    let Some((best, identity, _)) = matches.first().copied() else {
        return Err(invalid("No hosted name matches that query."));
    };
    let best_matches: Vec<_> = matches
        .iter()
        .filter(|(rank, _, _)| *rank == best)
        .collect();
    if best_matches.len() > 1 {
        let options = best_matches
            .iter()
            .take(8)
            .map(|(_, id, name)| format!("{id}: {name}"))
            .collect::<Vec<_>>()
            .join(", ");
        return Err(invalid(&format!(
            "Ambiguous name; use a native ID: {options}"
        )));
    }
    Ok(identity)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn all_wormhole_destinations_and_adjacent_gates_have_valid_maps() {
        let catalog = SectorCatalog::load().unwrap();
        assert_eq!(catalog.0.len(), 172);
        for id in [1910, 1070, 4120, 1705, 1077, 4520, 2210, 1060, 1071] {
            assert!(!catalog.sector(id).unwrap().navpoints.is_empty());
        }
        assert_eq!(catalog.sector(1076).unwrap().mobs.len(), 40);
        assert_eq!(
            catalog
                .0
                .values()
                .map(|sector| sector.mobs.len())
                .sum::<usize>(),
            6650
        );
        assert!(catalog.sector(2210).unwrap().mobs.len() > 100);
    }

    #[test]
    fn developer_lookup_accepts_ids_punctuation_abbreviations_and_rejects_ambiguity() {
        let catalog = SectorCatalog::load().unwrap();
        assert_eq!(catalog.resolve_sector("bbw").unwrap().id, 3605);
        assert_eq!(
            catalog.resolve_sector("BLACKBEARD'S WAKE").unwrap().id,
            3605
        );
        assert_eq!(catalog.resolve_sector("0xe15").unwrap().id, 3605);
        assert_eq!(catalog.resolve_sector("ragnar").unwrap().id, 1745);
        assert!(catalog
            .resolve_sector("asteroid belt")
            .unwrap_err()
            .to_string()
            .contains("Ambiguous"));
        assert!(catalog.resolve_sector("does not exist").is_err());
        assert!(catalog.resolve_sector("").is_err());
        assert_eq!(
            catalog.resolve_mob("achilles_drone").unwrap().template_id,
            46
        );
        assert_eq!(catalog.resolve_mob("46").unwrap().name, "Achilles Drone");
        assert_eq!(
            catalog.resolve_mob("The Controller").unwrap().template_id,
            1429
        );
    }
}
