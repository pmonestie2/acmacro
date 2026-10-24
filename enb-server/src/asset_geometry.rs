//! Numeric installed asset geometry plus the active C++ Resource/Husk overrides.
//! Static object and MOB radius policies remain separate; visual scale is not
//! applied to native Object::RangeFrom's receiver radius.
use crate::protocol::invalid;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::io;
use std::sync::{Arc, OnceLock};

#[derive(Deserialize)]
struct GeometryData {
    version: u32,
    radii: BTreeMap<u16, f32>,
    resource_overrides: BTreeMap<u16, f32>,
}

#[derive(Clone)]
pub struct AssetGeometry(Arc<GeometryData>);

impl AssetGeometry {
    fn decode(source: &str) -> io::Result<Self> {
        let data: GeometryData = serde_json::from_str(source)?;
        if data.version != 1
            || data.radii.is_empty()
            || data.resource_overrides.is_empty()
            || data
                .radii
                .values()
                .chain(data.resource_overrides.values())
                .any(|value| !value.is_finite() || !(0. ..=2_000_000.).contains(value))
        {
            return Err(invalid("Invalid native asset radius catalog."));
        }
        Ok(Self(Arc::new(data)))
    }

    pub fn load() -> io::Result<Self> {
        static DATA: OnceLock<AssetGeometry> = OnceLock::new();
        if let Some(data) = DATA.get() {
            return Ok(data.clone());
        }
        let data = Self::decode(include_str!("../data/asset_radii.json"))?;
        let _ = DATA.set(data);
        Ok(DATA.get().unwrap().clone())
    }

    pub fn base_radius(&self, asset: u16) -> io::Result<f32> {
        self.0
            .radii
            .get(&asset)
            .copied()
            .ok_or_else(|| invalid("Asset has no exported native radius."))
    }

    pub fn resource_radius(&self, asset: u16) -> io::Result<f32> {
        if let Some(radius) = self.0.resource_overrides.get(&asset) {
            Ok(*radius)
        } else {
            self.base_radius(asset)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn active_resource_overrides_differ_from_base_models_and_unknowns_fail() {
        let geometry = AssetGeometry::load().unwrap();
        assert_eq!(geometry.0.radii.len(), 3186);
        for (asset, radius) in [
            (1822, 800.),
            (1824, 800.),
            (1825, 630.),
            (1827, 630.),
            (1828, 510.),
            (1830, 510.),
            (1831, 750.),
            (1833, 750.),
            (1834, 0.),
        ] {
            assert_eq!(geometry.resource_radius(asset).unwrap(), radius);
        }
        assert_ne!(
            geometry.resource_radius(1822).unwrap(),
            geometry.base_radius(1822).unwrap()
        );
        assert_eq!(
            geometry.resource_radius(1960).unwrap(),
            geometry.base_radius(1960).unwrap()
        );
        assert!(geometry.resource_radius(65000).is_err());
    }

    #[test]
    fn malformed_geometry_is_rejected_without_inventing_values() {
        for source in [
            r#"{"version":1,"radii":{"1":-1},"resource_overrides":{"2":0}}"#,
            r#"{"version":1,"radii":{},"resource_overrides":{"2":0}}"#,
            r#"{"version":1,"radii":{"1":10}}"#,
            r#"{"version":2,"radii":{"1":10},"resource_overrides":{"2":0}}"#,
            r#"{"version":1,"radii":{"65536":10},"resource_overrides":{"2":0}}"#,
        ] {
            assert!(AssetGeometry::decode(source).is_err());
        }
    }
}
