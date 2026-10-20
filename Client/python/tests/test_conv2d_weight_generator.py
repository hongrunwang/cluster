"""Tests for deterministic Conv2D weight-tensor generation.

These lock byte-level equivalence with the cluster's
``compute_node/input_matrix/conv2d/generator.py`` weight stream so a client-
uploaded weight artifact reproduces the same float32 sequence the cluster would
have generated locally for the same named workload.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from dataset.conv2d import (  # noqa: E402
    DEFAULT_WEIGHT_SEED,
    LARGE_CIN,
    LARGE_COUT,
    LARGE_K,
    MID_CIN,
    MID_COUT,
    MID_K,
    SMALL_CIN,
    SMALL_COUT,
    SMALL_K,
    build_conv2d_spec,
    generate_default_weight_bytes,
    generate_weight_bytes,
    get_large_conv2d_spec,
    get_mid_conv2d_spec,
    get_small_conv2d_spec,
)
from dataset.splitmix import float32_chunk_from_counter  # noqa: E402


class Conv2dWeightSpecTests(unittest.TestCase):
    def test_default_weight_seed_matches_cluster_constant(self) -> None:
        # Cluster's compute_node/input_matrix/conv2d/spec.py uses the same constant.
        self.assertEqual(DEFAULT_WEIGHT_SEED, 0x0FEDCBA987654321)

    def test_small_dimensions_match_cluster(self) -> None:
        spec = get_small_conv2d_spec()
        self.assertEqual((spec.h, spec.w), (256, 256))
        self.assertEqual((spec.c_in, spec.c_out), (SMALL_CIN, SMALL_COUT))
        self.assertEqual(spec.k, SMALL_K)
        self.assertEqual(spec.weight_value_count, SMALL_K * SMALL_K * SMALL_CIN * SMALL_COUT)
        self.assertEqual(spec.weight_bytes, spec.weight_value_count * 4)

    def test_mid_dimensions_match_cluster(self) -> None:
        spec = get_mid_conv2d_spec()
        self.assertEqual((spec.h, spec.w), (1024, 1024))
        self.assertEqual((spec.c_in, spec.c_out), (MID_CIN, MID_COUT))
        self.assertEqual(spec.k, MID_K)
        self.assertEqual(spec.weight_value_count, MID_K * MID_K * MID_CIN * MID_COUT)

    def test_large_dimensions_match_cluster(self) -> None:
        spec = get_large_conv2d_spec()
        self.assertEqual((spec.h, spec.w), (2048, 2048))
        self.assertEqual((spec.c_in, spec.c_out), (LARGE_CIN, LARGE_COUT))
        self.assertEqual(spec.k, LARGE_K)
        self.assertEqual(spec.weight_value_count, LARGE_K * LARGE_K * LARGE_CIN * LARGE_COUT)


class Conv2dWeightGeneratorTests(unittest.TestCase):
    def test_small_weight_byte_length_matches_spec(self) -> None:
        spec = get_small_conv2d_spec()
        weight_bytes = generate_weight_bytes(spec)
        self.assertEqual(len(weight_bytes), spec.weight_bytes)

    def test_generation_is_deterministic(self) -> None:
        spec = get_small_conv2d_spec()
        first = generate_weight_bytes(spec, seed=DEFAULT_WEIGHT_SEED)
        second = generate_weight_bytes(spec, seed=DEFAULT_WEIGHT_SEED)
        self.assertEqual(first, second)

    def test_generator_wraps_splitmix_helper_directly(self) -> None:
        # Locks the cluster contract: counter starts at 0 and value_count covers the full tensor.
        spec = get_small_conv2d_spec()
        wrapped = generate_weight_bytes(spec, seed=DEFAULT_WEIGHT_SEED)
        direct = bytes(float32_chunk_from_counter(spec.weight_value_count, 0, DEFAULT_WEIGHT_SEED))
        self.assertEqual(wrapped, direct)

    def test_named_workload_size_routing(self) -> None:
        self.assertEqual(build_conv2d_spec(size="small").weight_value_count, get_small_conv2d_spec().weight_value_count)
        self.assertEqual(build_conv2d_spec(size="mid").weight_value_count, get_mid_conv2d_spec().weight_value_count)
        self.assertEqual(build_conv2d_spec(size="large").weight_value_count, get_large_conv2d_spec().weight_value_count)
        # Legacy aliases used elsewhere in the cluster code.
        self.assertEqual(build_conv2d_spec(size="medium").weight_value_count, get_mid_conv2d_spec().weight_value_count)
        self.assertEqual(build_conv2d_spec(size="runtime").weight_value_count, get_large_conv2d_spec().weight_value_count)

    def test_default_factory_uses_named_size(self) -> None:
        spec = get_small_conv2d_spec()
        named = generate_default_weight_bytes(size="small")
        explicit = generate_weight_bytes(spec)
        self.assertEqual(named, explicit)


if __name__ == "__main__":
    unittest.main()
