//! Fixed SplitMix64 for reproducible public-world contents across builds.
pub(crate) struct WorldRandom(pub u64);

impl WorldRandom {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e3779b97f4a7c15);
        let mut value = self.0;
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d049bb133111eb);
        value ^ (value >> 31)
    }
    pub fn below(&mut self, limit: u32) -> u32 {
        assert!(limit > 0);
        (self.next() % u64::from(limit)) as u32
    }
    pub fn fraction(&mut self) -> f64 {
        (self.next() >> 11) as f64 / (1u64 << 53) as f64
    }
}
