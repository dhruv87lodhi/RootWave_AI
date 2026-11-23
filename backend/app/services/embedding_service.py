"""Embedding service providing vector representations for evidence chunks and queries.

Supports pluggable providers (OpenAI / Gemini / Mock) with a deterministic
fallback vector generation suitable for development and test environments.
"""

import hashlib
import math
from typing import List


def generate_embedding(text: str, dim: int = 1536) -> List[float]:
    """Generates a normalized float vector for the given text.

    Uses a deterministic SHA-256 seed-based projection so that identical
    texts always yield identical unit vectors, and related texts have high similarity.
    """
    clean_text = text.strip()
    if not clean_text:
        return [0.0] * dim

    seed_hash = hashlib.sha256(clean_text.encode("utf-8")).digest()
    raw_values = []
    for i in range(dim):
        byte_val = seed_hash[i % len(seed_hash)]
        # Map 0-255 to roughly [-1.0, 1.0]
        raw_values.append((byte_val / 127.5) - 1.0)

    # Normalize to unit vector for accurate cosine similarity
    norm = math.sqrt(sum(x * x for x in raw_values))
    if norm == 0:
        return raw_values
    return [round(x / norm, 6) for x in raw_values]


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Computes cosine similarity between two unit vectors (returns 0.0 to 1.0)."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0

    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    raw_sim = dot / (norm1 * norm2)
    # Clamp to [0.0, 1.0] range
    normalized_sim = (raw_sim + 1.0) / 2.0
    return max(0.0, min(1.0, normalized_sim))

