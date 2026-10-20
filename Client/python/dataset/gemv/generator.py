"""Deterministic GEMV request-vector generation for clients."""

from __future__ import annotations

from core.float32_codec import unpack_float32_bytes
from ..splitmix import float32_chunk_from_counter
from .spec import DEFAULT_VECTOR_SEED, RequestVectorSpec, build_request_vector_spec


def generate_request_vector_bytes(
    spec: RequestVectorSpec,
    *,
    seed: int = DEFAULT_VECTOR_SEED,
) -> bytes:
    """Generate one deterministic request vector as little-endian float32 bytes."""

    return float32_chunk_from_counter(spec.length, 0, seed)


def generate_request_vector_values(
    spec: RequestVectorSpec,
    *,
    seed: int = DEFAULT_VECTOR_SEED,
) -> list[float]:
    """Generate one deterministic request vector as Python floats."""

    return unpack_float32_bytes(generate_request_vector_bytes(spec, seed=seed))


def generate_default_request_vector_bytes(*, seed: int = DEFAULT_VECTOR_SEED, size: str | None = None) -> bytes:
    """Generate the default named request vector as bytes."""

    return generate_request_vector_bytes(build_request_vector_spec(size=size), seed=seed)


def generate_default_request_vector_values(*, seed: int = DEFAULT_VECTOR_SEED, size: str | None = None) -> list[float]:
    """Generate the default named request vector as Python floats."""

    return generate_request_vector_values(build_request_vector_spec(size=size), seed=seed)
