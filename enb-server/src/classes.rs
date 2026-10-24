//! Native race/profession order is Terran/Jenquai/Progen, then
//! Warrior/Trader/Explorer (StaticData.h). Public class names use TE/TT/TS etc.
use crate::protocol::invalid;
use std::io;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CharacterClass(pub usize);

impl CharacterClass {
    pub fn parse(code: &str) -> io::Result<Self> {
        let index = match code.to_ascii_lowercase().as_str() {
            "te" | "tw" => 0,
            "tt" => 1,
            "ts" => 2,
            "jd" | "jw" => 3,
            "js" | "jt" => 4,
            "je" => 5,
            "pw" => 6,
            "pp" | "pt" => 7,
            "ps" | "pe" => 8,
            _ => {
                return Err(invalid(
                    "Class must be te, tt, ts, jd, js, je, pw, pp or ps.",
                ))
            }
        };
        Ok(Self(index))
    }
    pub fn from_profile(race: u32, profession: u32) -> io::Result<Self> {
        if race > 2 || profession > 2 {
            return Err(invalid("Invalid character race/profession."));
        }
        Ok(Self((race * 3 + profession) as usize))
    }
    pub fn code(self) -> &'static str {
        ["te", "tt", "ts", "jd", "js", "je", "pw", "pp", "ps"][self.0]
    }
    pub fn race(self) -> u32 {
        (self.0 / 3) as u32
    }
    pub fn profession(self) -> u32 {
        (self.0 % 3) as u32
    }
    pub fn weapons(self) -> usize {
        [5, 4, 4, 5, 4, 3, 6, 5, 4][self.0]
    }
    pub fn devices(self) -> usize {
        [4, 5, 5, 4, 5, 6, 3, 4, 5][self.0]
    }
    pub fn faction(self) -> u32 {
        [6, 11, 10, 18, 17, 16, 3, 5, 15][self.0]
    }
    pub fn base_scan_range(self) -> f32 {
        if self.profession() == 2 {
            5000.
        } else {
            3500.
        }
    }
    pub fn base_signature(self) -> f32 {
        [3500., 2500., 2500., 500., 500., 500., 2500., 2500., 2500.][self.0]
    }
    pub fn check_mount(self, slot: usize, item_type: u32) -> io::Result<()> {
        let valid = match item_type {
            2 => slot == 0,
            7 => slot == 1,
            6 => slot == 2,
            14..=16 => (3..3 + self.weapons()).contains(&slot),
            11 => (9..9 + self.devices()).contains(&slot),
            _ => false,
        };
        if valid {
            Ok(())
        } else {
            Err(invalid("Mount is unavailable to this class."))
        }
    }
    pub fn hull_asset(self) -> u16 {
        [1600, 1603, 1606, 1609, 1612, 1615, 1618, 1621, 1624][self.0]
    }
    pub fn profession_asset(self) -> u32 {
        [1630, 1627, 1633, 1639, 1636, 1642, 1648, 1645, 1651][self.0]
    }
    pub fn wing_asset(self) -> u32 {
        [1654, 1657, 1660, 1663, 1666, 1669, 1672, 1675, 1678][self.0]
    }
    pub fn engine_asset(self) -> u32 {
        [1681, 1684, 1687][self.race() as usize]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn all_nine_classes_have_unique_native_identities_and_actual_mount_limits() {
        let mut pairs = std::collections::HashSet::new();
        for code in ["te", "tt", "ts", "jd", "js", "je", "pw", "pp", "ps"] {
            let class = CharacterClass::parse(code).unwrap();
            assert!(pairs.insert((class.race(), class.profession())));
            assert_eq!(
                CharacterClass::from_profile(class.race(), class.profession()).unwrap(),
                class
            );
            assert_eq!(class.weapons() + class.devices(), 9);
            assert!(class.check_mount(3 + class.weapons() - 1, 14).is_ok());
            assert!(class.check_mount(3 + class.weapons(), 14).is_err());
            assert!(class.check_mount(9 + class.devices() - 1, 11).is_ok());
            assert!(class.check_mount(9 + class.devices(), 11).is_err());
        }
        assert!(CharacterClass::parse("bad").is_err());
        assert!(CharacterClass::from_profile(3, 0).is_err());
    }
}
