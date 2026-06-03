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
    Stores and queries L2-normalized 512-d face embeddings using Cosine Similarity (IndexFlatIP).
    """

    FACE_DIM = 512

    def __init__(self, db_dir: Path):
        self.db_dir = db_dir
        self.crops_dir = db_dir / "crops"
        self.meta_path = db_dir / "identities.json"
        self.index_path = db_dir / "face_index.bin"

        self.identities: list[Identity] = []
        self.index: Optional[faiss.IndexFlatIP] = None
        self._lock = threading.Lock()
        self._stranger_count = 0

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
                else:
                    _log(f"Loaded FAISS index with {self.index.ntotal} vectors.")
            except Exception as e:
                _log(f"Failed to load FAISS index: {e}. Rebuilding...")
                self.index = None

        if self.index is None:
            self.index = faiss.IndexFlatIP(self.FACE_DIM)
            if len(self.identities) > 0:
                _log("WARNING: Metadata has profiles but FAISS index is empty.")

        # Count existing strangers for ID continuity
        for ident in self.identities:
            if ident.id.startswith("stranger_"):
                try:
                    num = int(ident.id.split("_")[1])
                    self._stranger_count = max(self._stranger_count, num)
                except (ValueError, IndexError):
                    pass

    def save(self):
        """Persist identities metadata and FAISS index to disk."""
        with self._lock:
            try:
                with open(self.meta_path, "w") as f:
                    json.dump(
                        [{"id": i.id, "last_seen": i.last_seen, "crop_paths": i.crop_paths}
                         for i in self.identities],
                        f, indent=2
                    )
                if self.index is not None:
                    faiss.write_index(self.index, str(self.index_path))
            except Exception as e:
                _log(f"Error saving database: {e}")

    def match(self, face_emb: Optional[np.ndarray] = None, face_threshold: float = 0.60) -> tuple:
        """
        Match face embedding against FAISS database using inner product (Cosine Similarity).

        Returns:
            (label: str | None, score: float)
        """
        if face_emb is None or len(self.identities) == 0 or self.index.ntotal == 0:
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

            # Add vector to FAISS index
            self.index.add(row)

            # Add identity metadata entry
            self.identities.append(Identity(
                id=label,
                last_seen=timestamp,
                crop_paths=crop_paths,
            ))

            _log(f"Registered new stranger: {label} (face={'yes' if face_emb is not None else 'no'})")
            return label

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
