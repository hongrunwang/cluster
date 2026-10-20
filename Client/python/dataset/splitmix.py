"""Counter-based deterministic float32 chunk generation helpers.

NumPy is a hard client dependency (see requirements.txt). The project-level
bootstrap (client_setup.py / setup.py) installs it before the package is
imported, so the helper does not ship a pure-Python fallback.
"""

from __future__ import annotations

import numpy as np

MASK64 = 0xFFFFFFFFFFFFFFFF
_SPLITMIX64_GAMMA = 0x9E3779B97F4A7C15
_SPLITMIX64_MIX1 = 0xBF58476D1CE4E5B9
_SPLITMIX64_MIX2 = 0x94D049BB133111EB


def float32_chunk_from_counter(count: int, start_index: int, seed: int) -> bytes:
    """Generate one deterministic chunk with NumPy vectorized integer math."""

    counters = np.arange(start_index, start_index + count, dtype=np.uint64)
    values = np.uint64(seed) + (counters + np.uint64(1)) * np.uint64(_SPLITMIX64_GAMMA)
    values = np.bitwise_xor(values, np.right_shift(values, np.uint64(30)))
    values = values * np.uint64(_SPLITMIX64_MIX1)
    values = np.bitwise_xor(values, np.right_shift(values, np.uint64(27)))
    values = values * np.uint64(_SPLITMIX64_MIX2)
    values = np.bitwise_xor(values, np.right_shift(values, np.uint64(31)))

    lower = values.astype(np.uint32, copy=False)
    words = np.bitwise_or(
        np.left_shift(np.bitwise_and(lower, np.uint32(0x1)), np.uint32(31)),
        np.bitwise_or(
            np.uint32(0x3F000000),
            np.bitwise_and(np.right_shift(lower, np.uint32(1)), np.uint32(0x007FFFFF)),
        ),
    )
    return words.astype("<u4", copy=False).tobytes(order="C")
