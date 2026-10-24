//! Field nodes are streamed by distance, rather than loading every asteroid in
//! every field into the client on sector entry. IDs/positions come from the DB.
use super::*;

impl PlayerSession {
    pub(crate) fn publish_resources(&mut self) -> io::Result<()> {
        if !self.logged_in
            || self.sector.station.is_some()
            || self
                .last_resource_visibility
                .is_some_and(|at| at.elapsed() < Duration::from_millis(200))
        {
            return Ok(());
        }
        let shared = Arc::clone(&self.resource_field);
        let field = lock_resources(&shared)?;
        let wanted: HashSet<u32> = self
            .sector
            .resources
            .iter()
            .filter(|node| distance(node.object.position, self.position) <= 20_000.)
            .filter(|node| field.get(node.object.id).is_ok_and(|node| node.available()))
            .map(|node| node.object.id)
            .collect();
        for id in self.visible_resources.difference(&wanted) {
            self.pending.push(Frame::new(7, id.to_le_bytes().to_vec()));
            self.resource_fractions.remove(id);
            if self.resource_window == Some(*id) {
                self.resource_window = None;
                self.pending.push(packets::resource_interface(false));
            }
        }
        for node in self
            .sector
            .resources
            .iter()
            .filter(|node| wanted.contains(&node.object.id))
        {
            let id = node.object.id;
            if !self.visible_resources.contains(&id) {
                self.pending.push(packets::map_object(&node.object));
                self.pending
                    .push(packets::resource_name(id, &node.object.name));
            }
            let fraction = field.get(id)?.fraction();
            if self.resource_fractions.get(&id) != Some(&fraction) {
                self.pending
                    .push(packets::resource_level(id, fraction, node.level));
                self.resource_fractions.insert(id, fraction);
            }
        }
        self.visible_resources = wanted;
        self.last_resource_visibility = Some(Instant::now());
        Ok(())
    }
}
