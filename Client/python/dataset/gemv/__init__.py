"""Client-local GEMV request-vector helpers."""

from .generator import (
    generate_default_request_vector_bytes,
    generate_default_request_vector_values,
    generate_request_vector_bytes,
    generate_request_vector_values,
)
from .spec import (
    DEFAULT_VECTOR_LENGTH,
    DEFAULT_VECTOR_SEED,
    LARGE_VECTOR_LENGTH,
    MID_VECTOR_LENGTH,
    RequestVectorSpec,
    SMALL_VECTOR_LENGTH,
    build_request_vector_spec,
    normalize_request_size,
    vector_length_for_size,
)
