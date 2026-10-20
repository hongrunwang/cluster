"""Deterministic data-generation package mirroring the cluster's input_matrix layout.

Per-method generators live in `gemv/` and `conv2d/` so client and cluster can
emit byte-identical artifacts from the same SplitMix64 counter stream.
"""
