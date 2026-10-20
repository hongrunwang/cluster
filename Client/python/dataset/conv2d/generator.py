"""Deterministic Conv2D weight-tensor generation for clients.

Output is a flat little-endian float32 stream of length ``k * k * c_in * c_out``
with counter starting at zero, byte-identical to the cluster's
``compute_node/input_matrix/conv2d/generator.py`` weight stream.
"""

from __future__ import annotations

from core.float32_codec import unpack_float32_bytes
from ..splitmix import float32_chunk_from_counter
from .spec import Conv2dSpec, DEFAULT_WEIGHT_SEED, build_conv2d_spec


def generate_weight_bytes(
    spec: Conv2dSpec,
    *,
    seed: int = DEFAULT_WEIGHT_SEED,
) -> bytes:
    """Generate one Conv2D weight tensor as little-endian float32 bytes."""

    return float32_chunk_from_counter(spec.weight_value_count, 0, seed)


def generate_weight_values(
    spec: Conv2dSpec,
    *,
    seed: int = DEFAULT_WEIGHT_SEED,
) -> list[float]:
    """Generate one Conv2D weight tensor as Python floats."""

    return unpack_float32_bytes(generate_weight_bytes(spec, seed=seed))


def generate_default_weight_bytes(*, seed: int = DEFAULT_WEIGHT_SEED, size: str | None = None) -> bytes:
    """Generate the named-workload weight tensor as bytes."""

    return generate_weight_bytes(build_conv2d_spec(size=size), seed=seed)


def generate_default_weight_values(*, seed: int = DEFAULT_WEIGHT_SEED, size: str | None = None) -> list[float]:
    """Generate the named-workload weight tensor as Python floats."""

    return generate_weight_values(build_conv2d_spec(size=size), seed=seed)
