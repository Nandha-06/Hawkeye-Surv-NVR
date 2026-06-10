use std::path::{Path, PathBuf};

pub fn is_safe_component(value: &str) -> bool {
    !value.is_empty()
        && !value.contains('\0')
        && !value.contains(['/', '\\'])
        && Path::new(value).components().count() == 1
        && matches!(
            Path::new(value).components().next(),
            Some(std::path::Component::Normal(_))
        )
}

/// Resolves `child` relative to `parent` and verifies the resulting path
/// is contained inside `parent`. Returns `None` if the path escapes
/// (e.g. contains `..` segments, an absolute path, or a symlink target
/// outside `parent`).
pub fn safe_join_under(parent: &Path, child: &str) -> Option<PathBuf> {
    if child.is_empty() || child.contains('\0') {
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
                if std::fs::create_dir_all(parent).is_err() {
                    return None;
                }
            }
            std::fs::canonicalize(parent).ok()?
        }
    };

    let resolved = if joined.exists() {
        std::fs::canonicalize(&joined).ok()?
    } else {
        // Resolve the closest existing ancestor so an intermediate symlink
        // cannot redirect creation outside `parent`.
        let mut ancestor = joined.as_path();
        let mut suffix = PathBuf::new();
        while !ancestor.exists() {
            suffix = Path::new(ancestor.file_name()?).join(suffix);
            ancestor = ancestor.parent()?;
        }
        std::fs::canonicalize(ancestor).ok()?.join(suffix)
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

#[cfg(test)]
mod tests {
    use super::{is_safe_component, safe_join_under};

    fn temp_dir() -> PathBuf {
        std::env::temp_dir().join(format!("hawkeye_path_safe_{}", rand::random::<u64>()))
    }

    use std::path::PathBuf;

    #[test]
    fn rejects_parent_traversal() {
        let temp = temp_dir();
        std::fs::create_dir_all(&temp).unwrap();
        assert!(safe_join_under(&temp, "../escape.txt").is_none());
        std::fs::remove_dir_all(temp).unwrap();
    }

    #[test]
    fn validates_single_path_components() {
        assert!(is_safe_component("camera-01"));
        assert!(!is_safe_component(""));
        assert!(!is_safe_component(".."));
        assert!(!is_safe_component("../camera"));
        assert!(!is_safe_component("nested/camera"));
        assert!(!is_safe_component(r"nested\camera"));
    }

    #[test]
    fn accepts_nonexistent_child_under_parent() {
        let temp = temp_dir();
        std::fs::create_dir_all(&temp).unwrap();
        let result = safe_join_under(&temp, "nested/file.txt").unwrap();
        assert!(result.starts_with(std::fs::canonicalize(&temp).unwrap()));
        std::fs::remove_dir_all(temp).unwrap();
    }

    #[cfg(windows)]
    #[test]
    fn rejects_backslash_parent_traversal() {
        let temp = temp_dir();
        std::fs::create_dir_all(&temp).unwrap();
        assert!(safe_join_under(&temp, r"..\escape.txt").is_none());
        assert!(safe_join_under(&temp, r"nested\file.txt").is_some());
        std::fs::remove_dir_all(temp).unwrap();
    }
}
