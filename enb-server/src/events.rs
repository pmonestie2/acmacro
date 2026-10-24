//! Authenticated client requests that affect more than one player. A session
//! validates its wire owner/endpoint before the shared coordinator handles them.
pub enum ClientEvent {
    LootOpen { target: u32, toggle: bool },
    LootBegin { target: u32, slot: usize },
    Group { action: u32, target: u32 },
    Chat { channel: u8, text: String },
    Formation { action: u32, target: u32 },
}
