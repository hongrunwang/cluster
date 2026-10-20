"""Tests for deterministic 32K request-vector generation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from dataset.gemv import (  # noqa: E402
    DEFAULT_VECTOR_LENGTH,
    DEFAULT_VECTOR_SEED,
    SMALL_VECTOR_LENGTH,
    build_request_vector_spec,
    generate_default_request_vector_bytes,
    generate_default_request_vector_values,
)


class RequestVectorGeneratorTests(unittest.TestCase):
    def test_default_vector_shape_matches_32k_request_contract(self) -> None:
        spec = build_request_vector_spec()
        self.assertEqual(spec.length, DEFAULT_VECTOR_LENGTH)
        self.assertEqual(spec.vector_bytes, DEFAULT_VECTOR_LENGTH * 4)

    def test_generation_is_deterministic_for_same_seed(self) -> None:
        first = generate_default_request_vector_bytes(seed=DEFAULT_VECTOR_SEED)
        second = generate_default_request_vector_bytes(seed=DEFAULT_VECTOR_SEED)
        self.assertEqual(first, second)
        self.assertEqual(len(first), DEFAULT_VECTOR_LENGTH * 4)

    def test_small_request_shape_matches_lightweight_contract(self) -> None:
        spec = build_request_vector_spec(size="small")
        self.assertEqual(spec.length, SMALL_VECTOR_LENGTH)
        self.assertEqual(spec.vector_bytes, SMALL_VECTOR_LENGTH * 4)

    def test_value_generation_matches_byte_generation(self) -> None:
        values = generate_default_request_vector_values(seed=DEFAULT_VECTOR_SEED)
        self.assertEqual(len(values), DEFAULT_VECTOR_LENGTH)
        self.assertAlmostEqual(values[0], generate_default_request_vector_values(seed=DEFAULT_VECTOR_SEED)[0])


if __name__ == "__main__":
    unittest.main()
