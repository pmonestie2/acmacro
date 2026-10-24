//! Native ammo is a second inventory indexed by the launcher's mount. Loading
//! uses cargo -> equipment (1 -> 2), rather than replacing the launcher itself.
use crate::inventory::{Inventory, ItemInstance, ItemTransfer};
use crate::items::ItemCatalog;
use crate::protocol::invalid;
use std::io;

impl Inventory {
    pub(crate) fn check_ammunition(
        &self,
        slot: usize,
        ammo: &ItemInstance,
        items: &ItemCatalog,
    ) -> io::Result<()> {
        let launcher = self
            .equipped
            .get(slot)
            .and_then(Option::as_ref)
            .ok_or_else(|| invalid("No launcher in that mount."))?;
        let launcher = items.get(launcher.template)?;
        let template = items.get(ammo.template)?;
        if !(3..=8).contains(&slot)
            || !matches!(launcher.item_type, 15 | 16)
            || template.item_type != 10
            || launcher.ammo_type == 0
            || template.ammo_type != launcher.ammo_type
        {
            return Err(invalid("Ammunition does not match this launcher."));
        }
        Ok(())
    }

    pub(crate) fn loaded_ammunition(
        &self,
        cargo: usize,
        mount: usize,
        quantity: u32,
        items: &ItemCatalog,
    ) -> io::Result<Self> {
        let source = self
            .cargo
            .get(cargo)
            .and_then(Option::as_ref)
            .ok_or_else(|| invalid("No ammunition in that cargo slot."))?;
        if quantity == 0 || quantity > source.quantity {
            return Err(invalid("Invalid ammo quantity."));
        }
        self.check_ammunition(mount, source, items)?;
        let mut candidate = self.clone();
        let previous = candidate.ammunition[mount].take();
        let mut arriving = source.clone();
        arriving.quantity = quantity;
        let remaining = source.quantity - quantity;
        candidate.cargo[cargo] = (remaining > 0).then(|| {
            let mut item = source.clone();
            item.quantity = remaining;
            item
        });
        match previous {
            Some(mut loaded) if loaded.stacks_with(source) => {
                let moved = quantity.min(items.get(source.template)?.max_stack - loaded.quantity);
                if moved == 0 {
                    return Err(invalid("Launcher ammunition stack is full."));
                }
                loaded.quantity += moved;
                candidate.ammunition[mount] = Some(loaded);
                let leftover = source.quantity - moved;
                candidate.cargo[cargo] = (leftover > 0).then(|| {
                    let mut item = source.clone();
                    item.quantity = leftover;
                    item
                });
            }
            previous => {
                candidate.ammunition[mount] = Some(arriving);
                if let Some(previous) = previous {
                    candidate = candidate.received_instance(&previous, u32::MAX as usize, items)?;
                }
            }
        }
        candidate.validate(items)?;
        Ok(candidate)
    }

    /// Consume one loaded round, then auto-reload a compatible owned stack.
    /// The caller prepares this on its inventory copy before persisting a volley.
    pub(crate) fn consume_ammunition(
        &mut self,
        mount: usize,
        items: &ItemCatalog,
    ) -> io::Result<ItemInstance> {
        let mut round = self
            .ammunition
            .get(mount)
            .and_then(Option::as_ref)
            .cloned()
            .ok_or_else(|| invalid("Launcher has no ammunition."))?;
        self.check_ammunition(mount, &round, items)?;
        if round.quantity > 1 {
            self.ammunition[mount]
                .as_mut()
                .expect("loaded stack")
                .quantity -= 1;
        } else {
            self.ammunition[mount] = None;
            let family = items.get(round.template)?.ammo_type;
            if let Some(slot) = self.cargo.iter().position(|item| {
                item.as_ref().is_some_and(|item| {
                    items.get(item.template).is_ok_and(|template| {
                        template.item_type == 10 && template.ammo_type == family
                    })
                })
            }) {
                self.ammunition[mount] = self.cargo[slot].take();
            }
        }
        round.quantity = 1;
        Ok(round)
    }

    /// Weapon swaps carry their loaded stacks. Removing or replacing a launcher
    /// returns its ammunition to cargo, or rejects the whole operation if full.
    pub(crate) fn reconcile_ammunition(
        &mut self,
        previous: &Self,
        transfer: ItemTransfer,
        items: &ItemCatalog,
    ) -> io::Result<()> {
        if transfer.source == 2 && transfer.destination == 2 {
            self.ammunition
                .swap(transfer.source_slot, transfer.destination_slot);
        } else {
            for slot in 0..self.equipped.len() {
                if self.equipped[slot] != previous.equipped[slot] {
                    if let Some(ammo) = self.ammunition[slot].take() {
                        *self = self.received_instance(&ammo, u32::MAX as usize, items)?;
                    }
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn armed(items: &ItemCatalog) -> Inventory {
        let mut inventory = Inventory::starter(items).unwrap();
        inventory.equipped[3] = Some(ItemInstance::stock(3022, 1));
        inventory.cargo[0] = Some(ItemInstance::stock(512, 20));
        inventory
    }
    #[test]
    fn native_load_split_merge_and_weapon_removal_conserve_owned_ammo() {
        let items = ItemCatalog::load().unwrap();
        let original = armed(&items);
        let load = ItemTransfer {
            source: 1,
            source_slot: 0,
            destination: 2,
            destination_slot: 3,
            quantity: 5,
        };
        let loaded = original.moved(load, &items).unwrap();
        assert_eq!(loaded.equipped, original.equipped);
        assert_eq!(loaded.cargo[0].as_ref().unwrap().quantity, 15);
        assert_eq!(loaded.ammunition[3].as_ref().unwrap().quantity, 5);
        let stacked = loaded.moved(load, &items).unwrap();
        assert_eq!(stacked.ammunition[3].as_ref().unwrap().quantity, 10);
        assert_eq!(stacked.cargo[0].as_ref().unwrap().quantity, 10);
        let restored: Inventory =
            serde_json::from_slice(&serde_json::to_vec(&stacked).unwrap()).unwrap();
        assert_eq!(restored, stacked);
        let unequipped = restored
            .moved(
                ItemTransfer {
                    source: 2,
                    source_slot: 3,
                    destination: 1,
                    destination_slot: 1,
                    quantity: 1,
                },
                &items,
            )
            .unwrap();
        assert!(unequipped.ammunition[3].is_none());
        assert!(unequipped.equipped[3].is_none());
        assert_eq!(unequipped.cargo[0].as_ref().unwrap().quantity, 20);
        assert_eq!(unequipped.cargo[1].as_ref().unwrap().template, 3022);
        assert!(original
            .moved(
                ItemTransfer {
                    destination_slot: 4,
                    ..load
                },
                &items
            )
            .is_err());
        let mut wrong = original.clone();
        wrong.cargo[0] = Some(ItemInstance::stock(507, 5));
        assert!(wrong.moved(load, &items).is_err());
    }
    #[test]
    fn swapping_launchers_carries_ammo_and_full_cargo_rejects_without_loss() {
        let items = ItemCatalog::load().unwrap();
        let original = armed(&items);
        let mut loaded = original
            .moved(
                ItemTransfer {
                    source: 1,
                    source_slot: 0,
                    destination: 2,
                    destination_slot: 3,
                    quantity: 20,
                },
                &items,
            )
            .unwrap();
        loaded.equipped[4] = Some(ItemInstance::stock(3022, 1));
        let swapped = loaded
            .moved(
                ItemTransfer {
                    source: 2,
                    source_slot: 3,
                    destination: 2,
                    destination_slot: 4,
                    quantity: 1,
                },
                &items,
            )
            .unwrap();
        assert!(swapped.ammunition[3].is_none());
        assert_eq!(swapped.ammunition[4].as_ref().unwrap().quantity, 20);
        loaded.cargo.fill(Some(ItemInstance::stock(2845, 1)));
        let snapshot = loaded.clone();
        assert!(loaded
            .moved(
                ItemTransfer {
                    source: 2,
                    source_slot: 3,
                    destination: 1,
                    destination_slot: 0,
                    quantity: 1
                },
                &items
            )
            .is_err());
        assert_eq!(loaded, snapshot);
    }
}
