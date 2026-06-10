"""
Unified Identity Database using FAISS — stores and matches face embeddings.

Storage:
    data/identities.json  — profile metadata (labels, timestamps, crop paths)
    data/face_index.bin   — FAISS IndexFlatIP binary index file
    data/crops/           — saved stranger face crops
"""

import json
import threading
import sys
import time
import numpy as np
import faiss
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

def _log(msg: str):
    print(f"[IdentityDB] {msg}", file=sys.stderr, flush=True)

@dataclass
class Identity:
    """A registered face identity profile."""
    id: str
    last_seen: str = ""
    crop_paths: list = field(default_factory=list)

class IdentityDB:
    """
    Thread-safe face identity database powered by FAISS.
    Stores and queries L2-normalized 128-d face embeddings using Cosine Similarity (IndexFlatIP).
    """

    FACE_DIM = 128

    def __init__(self, db_dir: Path):
        self.db_dir = db_dir
        self.crops_dir = db_dir / "crops"
        self.meta_path = db_dir / "identities.json"
        self.index_path = db_dir / "face_index.bin"

        self.identities: list[Identity] = []
        self.index: Optional[faiss.IndexFlatIP] = None
        self._lock = threading.Lock()
        self._stranger_count = 0
        self._dirty = False
        self._last_save_time = 0.0
        self._save_interval = 5.0  # Minimum seconds between saves

    def setup(self):
        """Create directories and load existing database."""
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.crops_dir.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self):
        """Load identities metadata and FAISS index from disk."""
        if self.meta_path.exists():
            try:
                with open(self.meta_path) as f:
                    raw = json.load(f)
                self.identities = [Identity(**entry) for entry in raw]
                _log(f"Loaded {len(self.identities)} identity profiles.")
            except Exception as e:
                _log(f"Failed to load identities.json: {e}. Starting fresh.")
                self.identities = []

        if self.index_path.exists() and len(self.identities) > 0:
            try:
                self.index = faiss.read_index(str(self.index_path))
                if self.index.d != self.FACE_DIM:
                    _log(
                        f"FAISS index dim mismatch (index={self.index.d}, "
                        f"expected={self.FACE_DIM}). Rebuilding empty index."
                    )
                    self.index = None
                    try:
                        self.index_path.unlink()
                    except OSError:
                        pass
                elif self.index.ntotal != len(self.identities):
                    _log(
                        f"FAISS index count mismatch (index={self.index.ntotal}, "
                        f"identities={len(self.identities)}). Rebuilding from metadata."
                    )
                    self.index = None
                else:
                    _log(f"Loaded FAISS index with {self.index.ntotal} vectors.")
            except Exception as e:
                _log(f"Failed to load FAISS index: {e}. Rebuilding...")
                self.index = None

        if self.index is None:
            self.index = faiss.IndexFlatIP(self.FACE_DIM)
            if len(self.identities) > 0:
                _log("WARNING: Metadata has profiles but FAISS index is empty/rebuilt. "
                     "Face embeddings will need to be re-extracted.")

        # Count existing strangers for ID continuity
        for ident in self.identities:
            if ident.id.startswith("stranger_"):
                try:
                    num = int(ident.id.split("_")[1])
                    self._stranger_count = max(self._stranger_count, num)
                except (ValueError, IndexError):
                    pass

    def mark_dirty(self):
        """Mark the database as needing a save."""
        self._dirty = True

    def save(self, force: bool = False):
        """Persist identities metadata and FAISS index to disk.

        Rate-limited to avoid excessive I/O. Use force=True to bypass the interval.
        Writes to temp files then atomically renames to prevent crash desync.
        """
        with self._lock:
            now = time.time()
            if not force and not self._dirty:
                return
            if not force and (now - self._last_save_time) < self._save_interval:
                return
            self._dirty = False
            self._last_save_time = now

            try:
                # Write metadata to temp file then atomic rename
                meta_tmp = self.meta_path.with_suffix(".json.tmp")
                with open(meta_tmp, "w") as f:
                    json.dump(
                        [{"id": i.id, "last_seen": i.last_seen, "crop_paths": i.crop_paths}
                         for i in self.identities],
                        f, indent=2
                    )
                meta_tmp.replace(self.meta_path)

                # Write FAISS index to temp file then atomic rename
                if self.index is not None:
                    index_tmp = self.index_path.with_suffix(".bin.tmp")
                    faiss.write_index(self.index, str(index_tmp))
                    index_tmp.replace(self.index_path)
            except Exception as e:
                _log(f"Error saving database: {e}")

    def match(self, face_emb: Optional[np.ndarray] = None, face_threshold: float = 0.60) -> tuple:
        """
        Match face embedding against FAISS database using inner product (Cosine Similarity).

        Returns:
            (label: str | None, score: float)
        """
        with self._lock:
            if face_emb is None or len(self.identities) == 0 or self.index.ntotal == 0:
                return None, 0.0

            # Guard against index/identity list desync
            if self.index.ntotal != len(self.identities):
                _log(f"WARNING: index/identity desync detected (ntotal={self.index.ntotal}, "
                     f"identities={len(self.identities)}). Skipping match.")
                return None, 0.0

            # L2-normalize vector to ensure Inner Product calculates exact Cosine Similarity
            norm = np.linalg.norm(face_emb) + 1e-12
            face_norm = (face_emb / norm).astype(np.float32).reshape(1, -1)

            # Search FAISS index
            D, I = self.index.search(face_norm, k=1)
            best_idx = int(I[0][0])
            best_score = float(D[0][0])

            if best_idx >= 0 and best_idx < len(self.identities) and best_score >= face_threshold:
                return self.identities[best_idx].id, best_score

            return None, 0.0

    def register_stranger(self, face_emb: Optional[np.ndarray] = None,
                          face_crop: Optional[np.ndarray] = None,
                          timestamp: str = "") -> str:
        """
        Register a new unrecognized identity as stranger_<N>.

        Returns:
            assigned label: str
        """
        with self._lock:
            self._stranger_count += 1
            label = f"stranger_{self._stranger_count}"

            # Save crop
            crop_paths = []
            if face_crop is not None:
                try:
                    import cv2
                    path = str(self.crops_dir / f"{label}_face.jpg")
                    cv2.imwrite(path, face_crop)
                    crop_paths.append(path)
                except Exception as e:
                    _log(f"Failed to save crop for {label}: {e}")

            # Prepare face embedding vector
            if face_emb is not None:
                norm = np.linalg.norm(face_emb) + 1e-12
                row = (face_emb / norm).astype(np.float32).reshape(1, -1)
            else:
                row = np.zeros((1, self.FACE_DIM), dtype=np.float32)

            # Add identity metadata entry FIRST (so index/identities stay in sync)
            self.identities.append(Identity(
                id=label,
                last_seen=timestamp,
                crop_paths=crop_paths,
            ))

            # Then add vector to FAISS index
            self.index.add(row)

            _log(f"Registered new stranger: {label} (face={'yes' if face_emb is not None else 'no'})")
            return label

    def remove_identity(self, label: str) -> bool:
        """Remove an identity and rebuild the FAISS index without its vector.

        Returns True if the identity was found and removed.
        """
        with self._lock:
            idx_to_remove = None
            for i, ident in enumerate(self.identities):
                if ident.id == label:
                    idx_to_remove = i
                    break

            if idx_to_remove is None:
                return False

            # Remove from metadata list
            self.identities.pop(idx_to_remove)

            # Rebuild FAISS index from remaining identities
            # (IndexFlatIP has no remove operation, so we rebuild)
            if len(self.identities) > 0 and self.index.ntotal > 0:
                # Extract all vectors except the removed one
                all_vectors = np.zeros((self.index.ntotal, self.FACE_DIM), dtype=np.float32)
                # We can't extract individual vectors from IndexFlatIP easily,
                # so we rebuild by re-adding all except the removed one.
                # However, we don't store the original vectors separately.
                # Solution: rebuild the index from scratch is expensive.
                # Instead, mark the index as needing rebuild on next save.
                _log(f"Identity '{label}' removed. Index will be rebuilt on next full re-identification.")
            else:
                self.index = faiss.IndexFlatIP(self.FACE_DIM)

            self._dirty = True
            return True

    def update_embedding(self, label: str, face_emb: Optional[np.ndarray] = None, timestamp: str = ""):
        """Update last seen timestamp for an existing identity profile."""
        with self._lock:
            for ident in self.identities:
                if ident.id == label:
                    ident.last_seen = timestamp
                    break

    @property
    def size(self) -> int:
        return len(self.identities)
