"""Structured control-plane metadata shared with the cluster runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ArtifactDescriptor:
    """Description of one artifact published over the TCP data plane."""

    artifact_id: str
    content_type: str
    size_bytes: int
    checksum: str
    producer_node_id: str
    transfer_host: str
    transfer_port: int
    chunk_size: int
    ready: bool = True
