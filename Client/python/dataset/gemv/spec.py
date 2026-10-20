"""Deterministic client request-vector shape and metadata constants."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_VECTOR_LENGTH = 32_768
SMALL_VECTOR_LENGTH = 4_096
MID_VECTOR_LENGTH = 16_384
LARGE_VECTOR_LENGTH = DEFAULT_VECTOR_LENGTH
DEFAULT_VECTOR_SEED = 0x0FEDCBA987654321
GENERATOR_ALGORITHM = "splitmix64_counter_to_float32_words_v1"


@dataclass(slots=True)
class RequestVectorSpec:
    """Shape information for one generated request vector."""

    length: int

    @property
    def vector_bytes(self) -> int:
        """Return the encoded size of the request vector."""

        return self.length * 4


def normalize_request_size(size: str | None, *, default: str = "large") -> str:
    """Return the canonical request size label for GEMV vector generation."""

    normalized = (size or "").strip().lower()
    if not normalized:
        return default
    if normalized == "medium":
        return "mid"
    if normalized == "test":
        return "small"
    if normalized == "runtime":
        return "large"
    return normalized


def vector_length_for_size(size: str | None, *, default: str = "large") -> int:
    """Return the deterministic GEMV vector length for one named workload size."""

    normalized = normalize_request_size(size, default=default)
    if normalized == "small":
        return SMALL_VECTOR_LENGTH
    if normalized == "mid":
        return MID_VECTOR_LENGTH
    return LARGE_VECTOR_LENGTH


def build_request_vector_spec(*, length: int | None = None, size: str | None = None) -> RequestVectorSpec:
    """Return the default or overridden request-vector shape."""

    resolved_length = vector_length_for_size(size) if length is None else length
    if resolved_length <= 0:
        raise ValueError("length must be positive")
    return RequestVectorSpec(length=resolved_length)
