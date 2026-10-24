//! Shared production window and transport replay identity.
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq)]
pub struct WorkbenchView {
    pub mode: u32,
    pub selected: u32,
    pub ready: bool,
    pub components: Vec<(u32, u32)>,
    pub validity: u32,
    pub failure: String,
    pub additional: u32,
    pub filter: u32,
    pub products: Vec<u32>,
    pub cost: u64,
    pub base_cost: u64,
    pub chance: f32,
    pub target: Option<crate::inventory::ItemInstance>,
    pub category: u32,
    pub available_categories: Vec<(i32, i32)>,
}

pub struct CategoryDefinition {
    pub id: u32,
    pub name: &'static str,
    pub subcategories: &'static [(u32, &'static str)],
}

/// Native AUX hierarchy from AuxManufacturingIndex::InitializeCategories.
pub const CATEGORIES: &[CategoryDefinition] = &[
    CategoryDefinition {
        id: 10,
        name: "Weapons",
        subcategories: &[
            (100, "Beam"),
            (101, "Projectile"),
            (102, "Missile"),
            (103, "Ammo"),
        ],
    },
    CategoryDefinition {
        id: 11,
        name: "Systems",
        subcategories: &[(110, "Basic"), (111, "Drone")],
    },
    CategoryDefinition {
        id: 12,
        name: "Core",
        subcategories: &[(120, "Reactor"), (121, "Engine"), (122, "Shield")],
    },
    CategoryDefinition {
        id: 13,
        name: "Consumables",
        subcategories: &[(130, "Consumable X")],
    },
    CategoryDefinition {
        id: 50,
        name: "Computer / Electronics",
        subcategories: &[(140, "Software"), (141, "Electronics"), (142, "Computer")],
    },
    CategoryDefinition {
        id: 51,
        name: "Power",
        subcategories: &[
            (150, "Power Converter"),
            (151, "Power Coupling"),
            (152, "Power Core"),
            (153, "Generator"),
        ],
    },
    CategoryDefinition {
        id: 52,
        name: "Fabricated",
        subcategories: &[
            (160, "Shielded Casing"),
            (161, "Engine Frame"),
            (162, "Drone Frame"),
            (163, "Mounts"),
        ],
    },
    CategoryDefinition {
        id: 53,
        name: "Weapon Components",
        subcategories: &[
            (170, "Firing Mechanism"),
            (171, "Ammo Feeder / Loader"),
            (172, "Optics"),
            (173, "Barrel / Launch Tube"),
        ],
    },
    CategoryDefinition {
        id: 54,
        name: "Ammo",
        subcategories: &[
            (180, "Slug / Rail"),
            (181, "Warhead"),
            (182, "Propellant"),
            (183, "Shell Casing"),
        ],
    },
];

pub fn valid_category(id: u32) -> bool {
    id == 0
        || CATEGORIES.iter().any(|category| {
            category.id == id || category.subcategories.iter().any(|(sub, _)| *sub == id)
        })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProductionRequest {
    pub epoch: u64,
    pub sequence: u32,
}

impl ProductionRequest {
    pub fn follows(self, previous: Option<Self>) -> bool {
        previous.is_none_or(|old| {
            old.epoch != self.epoch || {
                let delta = self.sequence.wrapping_sub(old.sequence);
                delta != 0 && delta < 0x80000000
            }
        })
    }
}
