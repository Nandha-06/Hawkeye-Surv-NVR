use std::path::{Path, PathBuf};

/// Resolves `child` relative to `parent` and verifies the resulting path
/// is contained inside `parent`. Returns `None` if the path escapes
/// (e.g. contains `..` segments, an absolute path, or a symlink target
/// outside `parent`).
pub fn safe_join_under(parent: &Path, child: &str) -> Option<PathBuf> {
    if child.is_empty() {
        return None;
    }

    if child.contains('\0') {
        return None;
    }

    let child_path = Path::new(child);

    if child_path.is_absolute() {
        return None;
    }

    for component in child_path.components() {
        use std::path::Component;
        match component {
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => return None,
            _ => {}
        }
    }

    let joined = parent.join(child_path);

    let parent_abs = match std::fs::canonicalize(parent) {
        Ok(p) => p,
        Err(_) => {
            if !parent.exists() {
                if let Err(_) = std::fs::create_dir_all(parent) {
                    return None;
                }
            }
            std::fs::canonicalize(parent).ok()?
        }
    };

    let resolved = if joined.exists() {
        std::fs::canonicalize(&joined).ok()?
    } else {
        let parent_canon = parent_abs.clone();
        let suffix = joined.strip_prefix(parent).unwrap_or(&joined);
        parent_canon.join(suffix)
    };

    if !resolved.starts_with(&parent_abs) {
        return None;
    }

    Some(resolved)
}

/// Verify an existing filesystem path is contained under `parent`. Use this
/// when the path already exists (e.g. read from DB) and you only need a
/// containment check.
pub fn ensure_under(path: &Path, parent: &Path) -> bool {
    let p = match std::fs::canonicalize(path) {
        Ok(p) => p,
        Err(_) => return false,
    };
    let par = match std::fs::canonicalize(parent) {
        Ok(p) => p,
        Err(_) => return false,
    };
    p.starts_with(&par)
}
