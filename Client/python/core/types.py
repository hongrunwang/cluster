"""Client-local shared dataclasses mirrored from superweb-cluster."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DiscoveryResult:
    """Outcome of discovery or manual fallback."""

    success: bool
    peer_address: str | None = None
    peer_port: int | None = None
    source: str = "discovery"
    message: str = ""


@dataclass(slots=True)
class HardwareProfile:
    """Host hardware information shared during compute registration."""

    hostname: str
    local_ip: str
    mac_address: str
    system: str
    release: str
    machine: str
    processor: str
    logical_cpu_count: int
    memory_bytes: int


@dataclass(slots=True)
class ComputeHardwarePerformance:
    """One ranked backend result reported by a compute node at registration time."""

    hardware_type: str
    effective_gflops: float
    rank: int


@dataclass(slots=True)
class MethodPerformanceSummary:
    """One method-scoped benchmark summary reported by a compute node."""

    method: str
    hardware_count: int = 0
    ranked_hardware: list[ComputeHardwarePerformance] = field(default_factory=list)


@dataclass(slots=True)
class ComputePerformanceSummary:
    """Compact benchmark summary sent from a compute node to the main node."""

    hardware_count: int = 0
    ranked_hardware: list[ComputeHardwarePerformance] = field(default_factory=list)
    method_summaries: list[MethodPerformanceSummary] = field(default_factory=list)
