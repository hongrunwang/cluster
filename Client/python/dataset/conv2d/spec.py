"""Conv2D dataset shape and metadata constants kept in sync with the cluster.

Dimensions and seeds mirror compute_node/input_matrix/conv2d/spec.py byte-for-byte
so client-generated weights are interchangeable with cluster-generated weights.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_INPUT_SEED = 0x123456789ABCDEF0
DEFAULT_WEIGHT_SEED = 0x0FEDCBA987654321
GENERATOR_ALGORITHM = "splitmix64_counter_to_float32_words_v1"

SMALL_H, SMALL_W = 256, 256
SMALL_CIN, SMALL_COUT = 32, 64
SMALL_K, SMALL_PAD = 3, 1
SMALL_STRIDE = 1

MID_H, MID_W = 1024, 1024
MID_CIN, MID_COUT = 128, 256
MID_K, MID_PAD = 3, 1
MID_STRIDE = 1

LARGE_H, LARGE_W = 2048, 2048
LARGE_CIN, LARGE_COUT = 128, 256
LARGE_K, LARGE_PAD = 3, 1
LARGE_STRIDE = 1

WORKLOAD_SIZE_SMALL = "small"
WORKLOAD_SIZE_MID = "mid"
WORKLOAD_SIZE_LARGE = "large"
LEGACY_SIZE_ALIASES = {
    "test": WORKLOAD_SIZE_SMALL,
    "medium": WORKLOAD_SIZE_MID,
    "runtime": WORKLOAD_SIZE_LARGE,
}


@dataclass(slots=True)
class Conv2dSpec:
    """Describe one Conv2D dataset shape."""

    name: str
    h: int
    w: int
    c_in: int
    c_out: int
    k: int
    pad: int
    stride: int = 1

    @property
    def input_bytes(self) -> int:
        """Return the byte size of the input feature map."""
        return self.h * self.w * self.c_in * 4

    @property
    def weight_bytes(self) -> int:
        """Return the byte size of the weight tensor."""
        return self.k * self.k * self.c_in * self.c_out * 4

    @property
    def weight_value_count(self) -> int:
        """Return the float32 element count of the weight tensor."""
        return self.k * self.k * self.c_in * self.c_out

    @property
    def output_h(self) -> int:
        """Return the output height implied by the convolution shape."""
        return (self.h + 2 * self.pad - self.k) // self.stride + 1

    @property
    def output_w(self) -> int:
        """Return the output width implied by the convolution shape."""
        return (self.w + 2 * self.pad - self.k) // self.stride + 1

    @property
    def output_bytes(self) -> int:
        """Return the byte size of the output tensor."""
        return self.output_h * self.output_w * self.c_out * 4


def normalize_conv2d_size(size: str | None, *, default: str = WORKLOAD_SIZE_SMALL) -> str:
    """Return the canonical Conv2D workload size label."""

    normalized = (size or "").strip().lower()
    if not normalized:
        return default
    return LEGACY_SIZE_ALIASES.get(normalized, normalized)


def get_small_conv2d_spec() -> Conv2dSpec:
    """Return the canonical small Conv2D dataset specification."""
    return Conv2dSpec(
        name=f"small-conv2d-{SMALL_H}x{SMALL_W}",
        h=SMALL_H,
        w=SMALL_W,
        c_in=SMALL_CIN,
        c_out=SMALL_COUT,
        k=SMALL_K,
        pad=SMALL_PAD,
        stride=SMALL_STRIDE,
    )


def get_mid_conv2d_spec() -> Conv2dSpec:
    """Return the canonical mid-sized Conv2D dataset specification."""
    return Conv2dSpec(
        name=f"mid-conv2d-{MID_H}x{MID_W}",
        h=MID_H,
        w=MID_W,
        c_in=MID_CIN,
        c_out=MID_COUT,
        k=MID_K,
        pad=MID_PAD,
        stride=MID_STRIDE,
    )


def get_large_conv2d_spec() -> Conv2dSpec:
    """Return the canonical large Conv2D dataset specification."""
    return Conv2dSpec(
        name=f"large-conv2d-{LARGE_H}x{LARGE_W}",
        h=LARGE_H,
        w=LARGE_W,
        c_in=LARGE_CIN,
        c_out=LARGE_COUT,
        k=LARGE_K,
        pad=LARGE_PAD,
        stride=LARGE_STRIDE,
    )


def build_conv2d_spec(*, size: str | None = None) -> Conv2dSpec:
    """Return the named Conv2D specification for one workload size."""

    normalized = normalize_conv2d_size(size)
    if normalized == WORKLOAD_SIZE_MID:
        return get_mid_conv2d_spec()
    if normalized == WORKLOAD_SIZE_LARGE:
        return get_large_conv2d_spec()
    return get_small_conv2d_spec()
