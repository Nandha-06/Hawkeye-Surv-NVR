use rand::RngCore;
use std::path::{Path, PathBuf};

pub const TOKEN_HEADER: &str = "X-Local-Token";
pub const TOKEN_QUERY: &str = "token";
pub const TOKEN_FILENAME: &str = ".local_api_token";

#[cfg(unix)]
fn set_owner_only_perms(path: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mut perms = std::fs::metadata(path)?.permissions();
    perms.set_mode(0o600);
    std::fs::set_permissions(path, perms)
}

#[cfg(not(unix))]
fn set_owner_only_perms(_path: &Path) -> std::io::Result<()> {
    Ok(())
}

pub fn load_or_create_token(data_dir: &Path) -> String {
    let path: PathBuf = data_dir.join(TOKEN_FILENAME);

    if let Ok(content) = std::fs::read_to_string(&path) {
        let trimmed = content.trim();
        if !trimmed.is_empty() && trimmed.len() >= 32 {
            return trimmed.to_string();
        }
    }

    if !data_dir.exists() {
        let _ = std::fs::create_dir_all(data_dir);
    }

    let mut bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    let token: String = bytes.iter().map(|b| format!("{:02x}", b)).collect();

    if let Ok(mut f) = std::fs::File::create(&path) {
        use std::io::Write;
        let _ = f.write_all(token.as_bytes());
        let _ = f.sync_all();
        let _ = set_owner_only_perms(&path);
    }

    token
}
