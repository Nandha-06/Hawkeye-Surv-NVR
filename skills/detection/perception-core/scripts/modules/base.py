"""
Abstract base class for perception modules.

Each module receives a tracked person and the full frame, and returns
an optional embedding + match result. Modules self-gate: they decide
whether to run based on track state (age, size, confidence, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass
class ModuleResult:
    """Result from a perception module's processing of a single track."""
    embedding: Optional[np.ndarray] = None   # extracted feature vector
    matched_label: Optional[str] = None      # matched identity label (or None)
    similarity: float = 0.0                  # best cosine similarity score
    modality: str = ""                       # "body" or "face"
    crop: Optional[np.ndarray] = None        # aligned/processed crop (for saving)


class PerceptionModule(ABC):
    """
    Abstract base for pluggable perception modules (body re-ID, face recognition, etc.).

    Lifecycle:
        1. __init__() — called once, sets config
        2. load(providers) — loads ONNX models
        3. should_run(track) — gating check (per-track, per-frame)
        4. process(track, frame) — extract embedding + match
    """

    @abstractmethod
    def load(self, providers: list) -> None:
        """Load ONNX model(s). Called once during pipeline initialization."""
        ...

    @abstractmethod
    def should_run(self, track, frame_shape: tuple) -> bool:
        """
        Gating check: should this module process the given track this frame?

        Typical gates:
            - track.width >= min_person_width
            - track.confidence >= min_confidence
            - track.age % reid_every_n == 0  (or track has no label yet)
        """
        ...

    @abstractmethod
    def process(self, track, frame: np.ndarray) -> ModuleResult:
        """
        Process a tracked person: extract features and optionally match.

        Args:
            track: Track object with .bbox, .age, .label, etc.
            frame: Full BGR image (for cropping).

        Returns:
            ModuleResult with embedding, matched label, similarity, crop.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable module name for logging."""
        ...
