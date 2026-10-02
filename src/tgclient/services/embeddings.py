"""Local text embeddings for meaning-based search. Nothing leaves the computer.

fastembed (ONNX runtime) is an optional dependency (`pip install -e .[semantic]`); without it
search stays keyword-only. The model is downloaded once into the app's data dir on first use.
Calls are blocking: run them in a worker thread.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


class Embedder(Protocol):
    model: str

    def load(self) -> None: ...
    def embed_passages(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def semantic_available() -> bool:
    return importlib.util.find_spec("fastembed") is not None


class FastEmbedder:
    def __init__(self, model: str, cache_dir: Path) -> None:
        self.model = model
        self._cache_dir = cache_dir
        self._impl = None
        self._lock = threading.Lock()  # one ONNX session, used from one worker at a time

    def load(self) -> None:
        """Download (first time) and load the model. Blocking, can take a while."""
        with self._lock:
            if self._impl is None:
                from fastembed import TextEmbedding

                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._impl = TextEmbedding(self.model, cache_dir=str(self._cache_dir))

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        self.load()
        with self._lock:
            assert self._impl is not None
            vectors = np.array(list(self._impl.passage_embed(texts)), dtype=np.float32)
        return normalize(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        self.load()
        with self._lock:
            assert self._impl is not None
            vector = np.array(list(self._impl.query_embed(text)), dtype=np.float32)[0]
        return normalize(vector)


def normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norms, 1e-9)
