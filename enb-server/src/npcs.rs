//! Public NPC conversations and vendor price policies. This immutable catalog
//! contains no player state; sessions own the active dialogue/vendor identity.
use crate::items::ItemCatalog;
use crate::protocol::invalid;
use serde::Deserialize;
use std::collections::{HashMap, HashSet};
use std::io;
use std::sync::{Arc, OnceLock};

#[derive(Clone, Debug, Deserialize)]
pub struct Branch {
    pub node: u8,
    pub text: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct TalkNode {
    pub id: u8,
    pub text: String,
    pub flag: u32,
    pub branches: Vec<Branch>,
}

#[derive(Debug, Deserialize)]
pub struct Npc {
    pub id: u32,
    pub station: u32,
    pub name: String,
    pub group: u32,
    pub nodes: Vec<TalkNode>,
}

#[derive(Debug, Deserialize)]
pub struct Offer {
    pub item: u32,
    pub sell: i64,
    pub buy: i64,
    pub quantity: i32,
}

#[derive(Debug, Deserialize)]
pub struct VendorGroup {
    pub id: u32,
    pub sell_multiplier: f64,
    pub buy_multiplier: f64,
    pub listed_only: bool,
    pub offers: Vec<Offer>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct VendorOffer {
    pub item: u32,
    pub price: u64,
}

#[derive(Deserialize)]
struct CatalogFile {
    npcs: Vec<Npc>,
    groups: Vec<VendorGroup>,
}

struct CatalogData {
    npcs: HashMap<u32, Npc>,
    groups: HashMap<u32, VendorGroup>,
}

#[derive(Clone)]
pub struct NpcCatalog(Arc<CatalogData>);

impl NpcCatalog {
    pub fn load() -> io::Result<Self> {
        static CATALOG: OnceLock<Arc<CatalogData>> = OnceLock::new();
        if let Some(catalog) = CATALOG.get() {
            return Ok(Self(Arc::clone(catalog)));
        }
        let source: CatalogFile = serde_json::from_str(include_str!("../data/npcs.json"))?;
        let items = ItemCatalog::load()?;
        let mut groups = HashMap::new();
        for group in source.groups {
            if !group.sell_multiplier.is_finite()
                || !group.buy_multiplier.is_finite()
                || !(0.0..=100.).contains(&group.sell_multiplier)
                || !(0.0..=100.).contains(&group.buy_multiplier)
                || group.offers.len() > 128
            {
                return Err(invalid(
                    "Invalid vendor price policy or inventory capacity.",
                ));
            }
            let mut identities = HashSet::new();
            for offer in &group.offers {
                items.get(offer.item)?;
                if offer.sell < -1 || offer.buy < -1 || !identities.insert(offer.item) {
                    return Err(invalid("Invalid or duplicate vendor offer."));
                }
            }
            if groups.insert(group.id, group).is_some() {
                return Err(invalid("Duplicate vendor group."));
            }
        }
        let mut npcs = HashMap::new();
        for npc in source.npcs {
            if npc.group != 0 && !groups.contains_key(&npc.group) {
                return Err(invalid("NPC vendor group does not exist."));
            }
            let identities: HashSet<u8> = npc.nodes.iter().map(|node| node.id).collect();
            if !npc.nodes.is_empty()
                && (identities.len() != npc.nodes.len() || !identities.contains(&1))
            {
                return Err(invalid("Invalid NPC conversation roots."));
            }
            for node in &npc.nodes {
                if node.id == 0
                    || node.id >= 230
                    || node.branches.len() > 20
                    || node.text.len() > 16_000
                    || node.branches.iter().any(|branch| {
                        branch.text.len() > 1000
                            || branch.node >= 230
                            || (branch.node != 0 && !identities.contains(&branch.node))
                    })
                {
                    return Err(invalid("Invalid NPC conversation branch."));
                }
            }
            if npcs.insert(npc.id, npc).is_some() {
                return Err(invalid("Duplicate NPC identity."));
            }
        }
        let _ = CATALOG.set(Arc::new(CatalogData { npcs, groups }));
        Ok(Self(Arc::clone(CATALOG.get().unwrap())))
    }

    pub fn npc(&self, identity: u32, station: u32) -> io::Result<&Npc> {
        self.0
            .npcs
            .get(&identity)
            .filter(|npc| npc.station == station)
            .ok_or_else(|| invalid("NPC is not in the current station."))
    }

    pub fn group(&self, npc: &Npc) -> io::Result<&VendorGroup> {
        self.0
            .groups
            .get(&npc.group)
            .ok_or_else(|| invalid("This NPC has no vendor service."))
    }
}

impl VendorGroup {
    /// Positive prices override the group's multiplier. Zero means the group's
    /// base-price policy; -1 forbids that direction. Trade goods require an
    /// explicit buyer, so arbitrary vendors cannot accept an entire trade route.
    pub fn buy_price(&self, item: u32, items: &ItemCatalog) -> io::Result<u64> {
        let template = items.get(item)?;
        let row = self.offers.iter().find(|offer| offer.item == item);
        match row.map(|row| row.buy) {
            Some(-1) => Ok(0),
            Some(price) if price > 0 => Ok(price as u64),
            None if self.listed_only || template.category == 90 => Ok(0),
            _ => Ok((f64::from(template.price) * self.buy_multiplier).floor() as u64),
        }
    }

    pub fn inventory(&self, items: &ItemCatalog) -> io::Result<Vec<VendorOffer>> {
        let mut result = Vec::new();
        for offer in &self.offers {
            if offer.quantity == 0 || offer.sell < 0 {
                continue;
            }
            let template = items.get(offer.item)?;
            // Zero-price trade rows are destinations, never free stock.
            if template.category == 90 && offer.sell == 0 {
                continue;
            }
            let price = if offer.sell > 0 {
                offer.sell as u64
            } else {
                (f64::from(template.price) * self.sell_multiplier).floor() as u64
            };
            if price > 0 {
                result.push(VendorOffer {
                    item: offer.item,
                    price,
                });
            }
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_vendor_catalog_has_real_station_conversations_and_prices() {
        let catalog = NpcCatalog::load().unwrap();
        let items = ItemCatalog::load().unwrap();
        let npc = catalog.npc(9, 17051).unwrap();
        assert_eq!(npc.name, "Ibuka Terao");
        assert!(npc.nodes.iter().any(|node| node.flag == 1));
        assert!(catalog.npc(9, 10761).is_err());
        let group = catalog.group(npc).unwrap();
        let offers = group.inventory(&items).unwrap();
        assert!(offers.len() <= 128 && !offers.is_empty());
        assert!(offers.iter().all(|offer| offer.price > 0));
        let trade = catalog.group(catalog.npc(126, 17301).unwrap()).unwrap();
        assert_eq!(trade.buy_price(4030, &items).unwrap(), 142);
        assert!(!trade
            .inventory(&items)
            .unwrap()
            .iter()
            .any(|offer| offer.item == 4030));
        assert_eq!(group.buy_price(4030, &items).unwrap(), 0);
    }
}
