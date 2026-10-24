//! Shared world/journal replacement: serialize and sync the temporary file
//! before publishing it with rename. A failed write leaves the old file intact.
use serde::Serialize;
use std::path::Path;
use std::{fs, io};

pub fn replace<T: Serialize>(path: &Path, value: &T) -> io::Result<()> {
    replace_staged(path, &path.with_extension("json.next"), value)
}

pub fn replace_staged<T: Serialize>(path: &Path, temporary: &Path, value: &T) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("State file needs a parent directory."))?;
    fs::create_dir_all(parent)?;
    let mut file = fs::File::create(temporary)?;
    serde_json::to_writer(&mut file, value)?;
    file.sync_all()?;
    drop(file);
    fs::rename(temporary, path)
}
