//! Local-only account configuration. Passwords are read from stdin when an
//! account is created; only a salted PBKDF2 hash is written to ignored state.
use crate::protocol::invalid;
use pbkdf2::pbkdf2_hmac;
use rand::RngCore;
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::path::Path;
use std::{fs, io};

const ROUNDS: u32 = 100_000;
pub const FIRST_PLAYER_ID: u32 = 0x40000001;
/// Current sandbox provisioning advertises50 in each of the three XP tracks.
/// Until XP progression owns levels, combat detection uses the same total.
pub const SANDBOX_TRACK_LEVEL: u32 = 50;
fn first_player_id() -> u32 {
    FIRST_PLAYER_ID
}

#[derive(Clone, Serialize, Deserialize)]
pub struct CharacterProfile {
    #[serde(default = "first_player_id")]
    pub game_id: u32,
    pub name: String,
    pub race: u32,
    pub profession: u32,
}

impl CharacterProfile {
    pub fn total_level(&self) -> u32 {
        SANDBOX_TRACK_LEVEL * 3
    }
    pub fn class(&self) -> crate::classes::CharacterClass {
        crate::classes::CharacterClass::from_profile(self.race, self.profession)
            .expect("validated character class")
    }
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Account {
    pub username: String,
    pub character: CharacterProfile,
    salt: Vec<u8>,
    password_hash: Vec<u8>,
    rounds: u32,
}
impl Account {
    pub fn create(username: String, password: &str, name: String) -> io::Result<Self> {
        if username.is_empty()
            || username.len() > 64
            || !username
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_')
            || name.is_empty()
            || name.len() > 19
            || !name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
            || password.is_empty()
            || password.len() > 128
        {
            return Err(invalid(
                "invalid local account name, character name, or password length",
            ));
        }
        let mut salt = vec![0u8; 16];
        rand::thread_rng().fill_bytes(&mut salt);
        let mut password_hash = vec![0u8; 32];
        pbkdf2_hmac::<Sha256>(password.as_bytes(), &salt, ROUNDS, &mut password_hash);
        // C++ Net7.h: Jenquai=1, Explorer=2. The first sandbox character is JE.
        Ok(Self {
            username,
            character: CharacterProfile {
                game_id: FIRST_PLAYER_ID,
                name,
                race: 1,
                profession: 2,
            },
            salt,
            password_hash,
            rounds: ROUNDS,
        })
    }
    pub fn load(directory: &Path) -> io::Result<Self> {
        let path = directory.join("account.local.json");
        if !path.exists() {
            return Self::create("local".into(), "local", "SandboxPilot".into());
        }
        let account: Self = serde_json::from_slice(&fs::read(path)?)
            .map_err(|_| invalid("invalid local account configuration"))?;
        if account.salt.len() != 16
            || account.password_hash.len() != 32
            || account.rounds != ROUNDS
            || account.character.name.len() > 19
            || !account.character.name.is_ascii()
            || account.character.race > 2
            || account.character.profession > 2
            || !(FIRST_PLAYER_ID..=0x4000ffff).contains(&account.character.game_id)
        {
            return Err(invalid("invalid local account hash or character settings"));
        }
        Ok(account)
    }
    pub fn verify(&self, username: &str, password: &str) -> bool {
        if username != self.username || password.len() > 128 {
            return false;
        }
        let mut candidate = [0u8; 32];
        pbkdf2_hmac::<Sha256>(password.as_bytes(), &self.salt, self.rounds, &mut candidate);
        // Compare every hash byte; a mismatch does not reveal a matching prefix.
        candidate
            .iter()
            .zip(&self.password_hash)
            .fold(0u8, |difference, (a, b)| difference | (a ^ b))
            == 0
    }
    pub fn save_new(&self, directory: &Path) -> io::Result<()> {
        use std::io::Write;
        fs::create_dir_all(directory)?;
        let path = directory.join("account.local.json");
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)?;
        file.write_all(&serde_json::to_vec_pretty(self)?)?;
        file.sync_all()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn local_credentials_are_hashed_and_je_class_is_explicit() {
        let account = Account::create("test".into(), "example-secret", "Pilot".into()).unwrap();
        assert!(account.verify("test", "example-secret"));
        assert!(!account.verify("test", "wrong"));
        assert_eq!(
            (account.character.race, account.character.profession),
            (1, 2)
        );
        assert!(!serde_json::to_string(&account)
            .unwrap()
            .contains("example-secret"));
    }
}
