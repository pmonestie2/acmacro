//! Station-scoped dialogue and vendor sessions. A transfer names a vendor slot,
//! never an arbitrary item ID or a client-provided price. All mutations go
//! through the same durable inventory commit as equipment and vault transfers.
use super::*;
use crate::npcs::{Branch, TalkNode};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub(super) struct NpcInteraction {
    pub(super) npc: u32,
    pub(super) node: u8,
    pub(super) vendor: bool,
}

impl PlayerSession {
    pub(super) fn open_npc(&mut self, identity: u32) -> io::Result<()> {
        let station = self
            .sector
            .station
            .as_ref()
            .filter(|_| self.logged_in && self.hull > 0.)
            .ok_or_else(|| invalid("Enter a station alive before talking to an NPC."))?;
        if !station.npcs.iter().any(|npc| npc.id == identity) {
            return Err(invalid("NPC is not placed in this station."));
        }
        let npc = self.npcs.npc(identity, station.id)?;
        println!(
            "NPC conversation: {}; npc={identity} ({})",
            self.account.character.name, npc.name
        );
        self.close_npc();
        self.npc_interaction = Some(NpcInteraction {
            npc: identity,
            node: 1,
            vendor: false,
        });
        self.show_npc_node(1)
    }

    fn npc_node(&self, identity: u32, node: u8) -> io::Result<TalkNode> {
        let npc = self.npcs.npc(identity, self.sector.id)?;
        if !npc.nodes.is_empty() {
            return npc
                .nodes
                .iter()
                .find(|entry| entry.id == node)
                .cloned()
                .ok_or_else(|| invalid("NPC conversation node does not exist."));
        }
        match node {
            1 => {
                let mut branches = Vec::new();
                if npc.group != 0 {
                    branches.push(Branch {
                        node: 2,
                        text: "I would like to trade.".into(),
                    });
                }
                branches.push(Branch {
                    node: 0,
                    text: "Goodbye.".into(),
                });
                Ok(TalkNode {
                    id: 1,
                    text: format!("/happy1 Welcome, @name. I am {}.", npc.name),
                    flag: 0,
                    branches,
                })
            }
            2 if npc.group != 0 => Ok(TalkNode {
                id: 2,
                text: "/happy2 Thank you for your business.".into(),
                flag: 1,
                branches: Vec::new(),
            }),
            _ => Err(invalid("Unknown NPC dialogue node.")),
        }
    }

    fn talk_text(&self, text: &str) -> String {
        let profile = &self.account.character;
        let race = ["Terran", "Jenquai", "Progen"]
            .get(profile.race as usize)
            .unwrap_or(&"Jenquai");
        let profession = ["Warrior", "Trader", "Explorer"]
            .get(profile.profession as usize)
            .unwrap_or(&"Explorer");
        // Replace longer tokens first to avoid replacing prefixes of another.
        text.replace(
            "@aprofession",
            if *profession == "Explorer" {
                "an Explorer"
            } else if *profession == "Trader" {
                "a Trader"
            } else {
                "a Warrior"
            },
        )
        .replace("@profession", profession)
        .replace("@race", race)
        .replace("@name", &profile.name)
    }

    fn show_npc_node(&mut self, node: u8) -> io::Result<()> {
        let identity = self
            .npc_interaction
            .as_ref()
            .ok_or_else(|| invalid("No NPC conversation is active."))?
            .npc;
        let mut entry = self.npc_node(identity, node)?;
        if entry.flag > 1 {
            return Err(invalid("This NPC mission action is not implemented yet."));
        }
        // A few public talk trees advertise trade without a vendor group. Keep
        // the conversation usable while making the content gap explicit.
        if entry.flag == 1
            && self
                .npcs
                .group(self.npcs.npc(identity, self.sector.id)?)
                .is_err()
        {
            entry.flag = 0;
            entry.text = "I have no inventory available at the moment.".into();
            entry.branches = vec![Branch {
                node: 0,
                text: "Goodbye.".into(),
            }];
            eprintln!("NPC vendor content unavailable: npc={identity}");
        }
        let text = self.talk_text(&entry.text);
        let mut branches: Vec<(u8, String)> = entry
            .branches
            .iter()
            .map(|branch| (branch.node, self.talk_text(&branch.text)))
            .collect();
        if branches.is_empty() && entry.flag == 0 {
            branches.push((0, "Goodbye.".into()));
        }
        if entry.flag == 1 {
            // Resolve every template/quote before changing the active service.
            let frames = self.vendor_frames(identity)?;
            self.npc_interaction.as_mut().unwrap().vendor = true;
            self.pending.extend(frames);
            self.publish_vendor_prices()?;
            self.pending.push(packets::talk_control(5));
            self.pending.push(packets::talk_control(3));
            self.pending.push(packets::talk_tree(&text, &branches));
            self.pending.push(packets::talk_control(6));
            println!(
                "Vendor opened: {}; npc={identity}",
                self.account.character.name
            );
        } else {
            if self
                .npc_interaction
                .as_ref()
                .is_some_and(|active| active.vendor)
            {
                self.npc_interaction.as_mut().unwrap().vendor = false;
                self.pending.push(packets::talk_control(-32));
                self.pending.push(packets::vendor_inventory(&[]));
                self.publish_vendor_prices()?;
            }
            self.pending.push(packets::talk_tree(&text, &branches));
        }
        self.npc_interaction.as_mut().unwrap().node = node;
        Ok(())
    }

    pub(super) fn talk_choice(&mut self, body: &[u8]) -> io::Result<()> {
        if self.sector.station.is_none() {
            return self.rescue_choice(body);
        }
        if body.len() != 5 {
            return Err(invalid("Invalid NPC selection packet length."));
        }
        let mut reader = Reader::new(body);
        let owner = reader.u32()?;
        if owner != self.player_id() && owner != self.player_id() & 0xffffff {
            return Err(invalid("NPC selection owner mismatch."));
        }
        if !self.logged_in || self.hull <= 0. {
            return Err(invalid("NPC interaction unavailable."));
        }
        let choice = reader.take(1)?[0];
        if choice == 230 {
            self.close_npc();
            return Ok(());
        }
        let active = self
            .npc_interaction
            .as_ref()
            .ok_or_else(|| invalid("No NPC conversation is active."))?;
        let current = self.npc_node(active.npc, active.node)?;
        // A modified client cannot jump directly to any trade/mission node.
        if !current.branches.iter().any(|branch| branch.node == choice)
            && !(choice == 0 && (current.branches.is_empty() || active.vendor))
        {
            return Err(invalid("NPC choice was not offered by the current node."));
        }
        if choice == 0 {
            self.close_npc();
            Ok(())
        } else {
            self.show_npc_node(choice)
        }
    }

    fn vendor_frames(&self, identity: u32) -> io::Result<Vec<Frame>> {
        let npc = self.npcs.npc(identity, self.sector.id)?;
        let offers = self.npcs.group(npc)?.inventory(&self.items)?;
        let mut frames: Vec<Frame> = offers
            .iter()
            .map(|offer| self.items.get(offer.item).map(ItemTemplate::packet))
            .collect::<io::Result<_>>()?;
        frames.push(packets::vendor_inventory(&offers));
        Ok(frames)
    }

    pub(super) fn vendor_prices(&self, inventory: &Inventory) -> io::Result<HashMap<u32, u64>> {
        let Some(active) = self.npc_interaction.as_ref().filter(|active| active.vendor) else {
            return Ok(HashMap::new());
        };
        let npc = self.npcs.npc(active.npc, self.sector.id)?;
        let group = self.npcs.group(npc)?;
        inventory
            .templates(&self.items)?
            .into_iter()
            .map(|template| {
                let price = if template.level > 9 {
                    0
                } else {
                    group.buy_price(template.id, &self.items)?
                };
                Ok((template.id, price))
            })
            .collect()
    }

    pub(super) fn publish_vendor_prices(&mut self) -> io::Result<()> {
        let prices = self.vendor_prices(&self.inventory)?;
        self.pending.extend(packets::inventory_prices(
            self.player_id(),
            &self.inventory,
            &prices,
        ));
        Ok(())
    }

    pub(super) fn close_npc(&mut self) {
        if self.npc_interaction.take().is_some() {
            self.pending.push(packets::talk_control(-32));
            self.pending.push(packets::vendor_inventory(&[]));
            self.pending.extend(packets::inventory_prices(
                self.player_id(),
                &self.inventory,
                &HashMap::new(),
            ));
        }
    }

    pub(super) fn restore_npc_service(&mut self) -> io::Result<()> {
        if let Some(active) = self.npc_interaction.clone() {
            if self.sector.station.is_none() || self.npc_node(active.npc, active.node).is_err() {
                self.close_npc();
            } else if active.vendor {
                self.pending.extend(self.vendor_frames(active.npc)?);
                self.publish_vendor_prices()?;
            }
        }
        Ok(())
    }

    pub(super) fn vendor_transaction(&self, transfer: ItemTransfer) -> io::Result<Inventory> {
        if self.sector.station.is_none() {
            return Err(invalid("Vendor transactions require a station."));
        }
        let active = self
            .npc_interaction
            .as_ref()
            .filter(|active| active.vendor)
            .ok_or_else(|| invalid("Open a vendor before trading."))?;
        let npc = self.npcs.npc(active.npc, self.sector.id)?;
        let group = self.npcs.group(npc)?;
        match (transfer.source, transfer.destination) {
            (4, 1) => {
                let offers = group.inventory(&self.items)?;
                let offer = offers
                    .get(transfer.source_slot)
                    .ok_or_else(|| invalid("Vendor slot is empty or invalid."))?;
                self.inventory.purchased(
                    offer.item,
                    transfer.quantity,
                    offer.price,
                    transfer.destination_slot,
                    &self.items,
                )
            }
            (1, 4) if transfer.destination_slot == 0 => {
                let item = self
                    .inventory
                    .cargo
                    .get(transfer.source_slot)
                    .and_then(Option::as_ref)
                    .ok_or_else(|| invalid("Sale cargo is empty or invalid."))?;
                self.inventory.sold(
                    transfer.source_slot,
                    transfer.quantity,
                    group.buy_price(item.template, &self.items)?,
                    &self.items,
                )
            }
            _ => Err(invalid("Unsupported vendor transfer path or destination.")),
        }
    }
}
