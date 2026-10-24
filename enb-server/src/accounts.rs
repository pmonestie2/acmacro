//! Account files and durable character identities. The original primary account
//! stays at state/account.local.json; additional accounts get independent state
//! directories under state/accounts/<username>. Password hashes never go on wire.
use crate::account::{Account, FIRST_PLAYER_ID};
use crate::protocol::invalid;
use std::collections::HashSet;
use std::io;
use std::path::{Path, PathBuf};

pub struct StoredAccount {
    pub account: Account,
    pub directory: PathBuf,
}

pub struct AccountDirectory {
    root: PathBuf,
}

impl AccountDirectory {
    pub fn new(root: &Path) -> Self {
        Self {
            root: root.to_path_buf(),
        }
    }

    pub fn load(&self) -> io::Result<Vec<StoredAccount>> {
        let mut accounts = vec![StoredAccount {
            account: Account::load(&self.root)?,
            directory: self.root.clone(),
        }];
        let extra = self.root.join("accounts");
        if extra.exists() {
            let mut paths = std::fs::read_dir(extra)?
                .map(|entry| entry.map(|entry| entry.path()))
                .collect::<io::Result<Vec<_>>>()?;
            paths.sort();
            for path in paths {
                if path.is_dir() && path.join("account.local.json").is_file() {
                    accounts.push(StoredAccount {
                        account: Account::load(&path)?,
                        directory: path,
                    });
                }
            }
        }
        let mut ids = HashSet::new();
        let mut names = HashSet::new();
        let mut usernames = HashSet::new();
        for stored in &accounts {
            if !ids.insert(stored.account.character.game_id)
                || !names.insert(stored.account.character.name.to_ascii_lowercase())
                || !usernames.insert(stored.account.username.to_ascii_lowercase())
            {
                return Err(invalid("duplicate account/character name or game identity"));
            }
        }
        Ok(accounts)
    }

    pub fn create(
        &self,
        username: String,
        password: &str,
        character: String,
    ) -> io::Result<StoredAccount> {
        self.create_class(
            username,
            password,
            character,
            crate::classes::CharacterClass::parse("je")?,
        )
    }

    pub fn create_class(
        &self,
        username: String,
        password: &str,
        character: String,
        class: crate::classes::CharacterClass,
    ) -> io::Result<StoredAccount> {
        // Validate components before using the username as a path component.
        let mut account = Account::create(username, password, character)?;
        account.character.race = class.race();
        account.character.profession = class.profession();
        if !self.root.join("account.local.json").exists() {
            account.save_new(&self.root)?;
            return Ok(StoredAccount {
                account,
                directory: self.root.clone(),
            });
        }
        let existing = self.load()?;
        if existing.iter().any(|stored| {
            stored
                .account
                .username
                .eq_ignore_ascii_case(&account.username)
                || stored
                    .account
                    .character
                    .name
                    .eq_ignore_ascii_case(&account.character.name)
        }) {
            return Err(invalid("account or character name already exists"));
        }
        account.character.game_id = (FIRST_PLAYER_ID..=0x4000ffff)
            .find(|id| {
                existing
                    .iter()
                    .all(|stored| stored.account.character.game_id != *id)
            })
            .ok_or_else(|| invalid("no character identities remain"))?;
        let directory = self.root.join("accounts").join(&account.username);
        account.save_new(&directory)?;
        Ok(StoredAccount { account, directory })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn accounts_have_distinct_durable_ids_and_separate_state() {
        let root = std::env::temp_dir().join(format!("enb-accounts-{}", rand::random::<u64>()));
        let directory = AccountDirectory::new(&root);
        let first = directory
            .create("alice".into(), "example", "Alice".into())
            .unwrap();
        let second = directory
            .create("bob".into(), "example", "Bob".into())
            .unwrap();
        assert_ne!(first.directory, second.directory);
        assert_eq!(first.account.character.game_id, FIRST_PLAYER_ID);
        assert_eq!(second.account.character.game_id, FIRST_PLAYER_ID + 1);
        assert_eq!(directory.load().unwrap().len(), 2);
        assert!(directory
            .create("bob".into(), "example", "Other".into())
            .is_err());
        std::fs::remove_dir_all(root).unwrap();
    }
}
