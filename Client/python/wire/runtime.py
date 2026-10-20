"""Client-local protobuf wire-format helpers for the superweb-cluster TCP runtime."""

from __future__ import annotations

import enum
import socket
import struct
import time
from dataclasses import InitVar, dataclass

from core.types import ComputeHardwarePerformance, ComputePerformanceSummary, HardwareProfile, MethodPerformanceSummary
from core.constants import (
    MAIN_NODE_NAME,
    METHOD_GEMM,
    METHOD_GEMV,
    METHOD_CONV2D,
    RUNTIME_MSG_CLIENT_JOIN,
    RUNTIME_MSG_CLIENT_INFO_REPLY,
    RUNTIME_MSG_CLIENT_INFO_REQUEST,
    RUNTIME_MSG_CLIENT_REQUEST,
    RUNTIME_MSG_CLIENT_REQUEST_OK,
    RUNTIME_MSG_CLIENT_RESPONSE,
    RUNTIME_MSG_ARTIFACT_RELEASE,
    RUNTIME_MSG_HEARTBEAT,
    RUNTIME_MSG_HEARTBEAT_OK,
    RUNTIME_MSG_REGISTER_OK,
    RUNTIME_MSG_REGISTER_WORKER,
    RUNTIME_MSG_TASK_ACCEPT,
    RUNTIME_MSG_TASK_ASSIGN,
    RUNTIME_MSG_TASK_FAIL,
    RUNTIME_MSG_TASK_RESULT,
    RUNTIME_MSG_WORKER_UPDATE,
    SUPERWEB_CLIENT_NAME,
)
from core.tracing import trace_function
from wire.control_plane import ArtifactDescriptor
from wire.runtime_codec import encode_envelope, parse_envelope

FRAME_HEADER = struct.Struct("!I")


def _initvar_or_default(value, default):
    """Recover InitVar defaults when a compatibility property shadows the field name."""

    return default if isinstance(value, property) else value


class MessageKind(enum.IntEnum):
    """Supported protobuf envelope types."""

    UNSPECIFIED = 0
    REGISTER_WORKER = 1
    REGISTER_OK = 2
    HEARTBEAT = 3
    HEARTBEAT_OK = 4
    CLIENT_JOIN = 5
    CLIENT_REQUEST = 6
    CLIENT_RESPONSE = 7
    TASK_ASSIGN = 8
    TASK_ACCEPT = 9
    TASK_FAIL = 10
    TASK_RESULT = 11
    ARTIFACT_RELEASE = 12
    WORKER_UPDATE = 13
    CLIENT_INFO_REQUEST = 14
    CLIENT_INFO_REPLY = 15
    CLIENT_REQUEST_OK = 16


@dataclass(slots=True)
class RegisterWorker:
    """Compute-node registration payload."""

    node_name: str
    hardware: HardwareProfile
    performance: ComputePerformanceSummary


@dataclass(slots=True)
class RegisterOk:
    """Main-node acceptance payload."""

    main_node_name: str
    main_node_ip: str
    main_node_port: int
    node_id: str


@dataclass(slots=True)
class Heartbeat:
    """One-way main-node heartbeat payload."""

    main_node_name: str
    unix_time_ms: int


@dataclass(slots=True)
class HeartbeatOk:
    """Worker acknowledgement for one heartbeat payload."""

    node_name: str
    heartbeat_unix_time_ms: int
    received_unix_time_ms: int
    active_task_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class ClientJoin:
    """Client registration payload."""

    client_name: str


@dataclass(slots=True)
class ClientInfoRequest:
    """Periodic liveness/info poll sent from one client to the main node."""

    client_id: str
    client_name: str
    timestamp_ms: int


@dataclass(slots=True)
class ClientInfoReply:
    """Main-node response describing whether one client still owns active work."""

    client_id: str
    request_timestamp_ms: int
    reply_timestamp_ms: int
    timeout_ms: int
    has_active_tasks: bool
    active_task_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class ClientRequestOk:
    """Early main-node acknowledgement carrying the server-assigned task id."""

    client_id: str
    task_id: str
    method: str
    size: str
    object_id: str
    accepted_timestamp_ms: int
    # Data-plane handshake fields assigned by main; empty means inline-only.
    upload_id: str = ""
    download_id: str = ""
    data_endpoint_host: str = ""
    data_endpoint_port: int = 0


@dataclass(slots=True)
class GemvRequestPayload:
    """Method-specific request payload for fixed-matrix vector multiplication."""

    vector_length: int = 0
    vector_data: bytes = b""


@dataclass(slots=True)
class Conv2dRequestPayload:
    """Method-specific request payload for conv2d."""

    tensor_h: int = 0
    tensor_w: int = 0
    channels_in: int = 0
    channels_out: int = 0
    kernel_size: int = 0
    padding: int = 0
    stride: int = 1
    client_response_mode: int = 0
    stats_max_samples: int = 0
    upload_size_bytes: int = 0
    upload_checksum: str = ""


@dataclass(slots=True)
class GemmRequestPayload:
    """Method-specific request payload for cuBLAS GEMM.

    Carries no matrix bytes: both A and B are pre-generated on every compute
    node from fixed seeds keyed by request size.
    """


@dataclass(slots=True)
class GemmTaskPayload:
    """Method-specific task payload for cuBLAS GEMM."""

    m_start: int = 0
    m_end: int = 0
    m: int = 0
    n: int = 0
    k: int = 0


@dataclass(slots=True)
class GemmResponsePayload:
    """Method-specific client response payload for cuBLAS GEMM."""

    output_length: int = 0
    output_vector: bytes = b""


@dataclass(slots=True)
class GemmResultPayload:
    """Method-specific task result payload for cuBLAS GEMM."""

    m_start: int = 0
    m_end: int = 0
    output_length: int = 0
    output_vector: bytes = b""


@dataclass(slots=True)
class GemvTaskPayload:
    """Method-specific task payload for fixed-matrix vector multiplication."""

    row_start: int = 0
    row_end: int = 0
    vector_length: int = 0
    vector_data: bytes = b""


@dataclass(slots=True)
class Conv2dTaskPayload:
    """Method-specific task payload for conv2d."""

    start_oc: int = 0
    end_oc: int = 0
    tensor_h: int = 0
    tensor_w: int = 0
    channels_in: int = 0
    channels_out: int = 0
    kernel_size: int = 0
    padding: int = 0
    stride: int = 1
    weight_data: bytes = b""


@dataclass(slots=True)
class GemvResponsePayload:
    """Method-specific client response payload for fixed-matrix vector multiplication."""

    output_length: int = 0
    output_vector: bytes = b""


@dataclass(slots=True)
class Conv2dResponsePayload:
    """Method-specific client response payload for conv2d."""

    output_length: int = 0
    output_vector: bytes = b""
    result_artifact_id: str = ""
    stats_element_count: int = 0
    stats_sum: float = 0.0
    stats_sum_squares: float = 0.0
    stats_samples: tuple[float, ...] = ()


@dataclass(slots=True)
class GemvResultPayload:
    """Method-specific task result payload for fixed-matrix vector multiplication."""

    row_start: int = 0
    row_end: int = 0
    output_length: int = 0
    output_vector: bytes = b""


@dataclass(slots=True)
class Conv2dResultPayload:
    """Method-specific task result payload for conv2d."""

    start_oc: int = 0
    end_oc: int = 0
    output_h: int = 0
    output_w: int = 0
    output_length: int = 0
    output_vector: bytes = b""
    result_artifact_id: str = ""


@dataclass(slots=True)
class ClientRequest:
    """Client request sent to the main node with a typed method payload."""

    request_id: str
    client_name: str
    method: str
    size: str
    object_id: str
    stream_id: str
    timestamp_ms: int
    iteration_count: int
    request_payload: GemvRequestPayload | Conv2dRequestPayload | GemmRequestPayload | None = None
    vector_length: InitVar[int] = 0
    vector_data: InitVar[bytes] = b""
    tensor_h: InitVar[int] = 0
    tensor_w: InitVar[int] = 0
    channels_in: InitVar[int] = 0
    channels_out: InitVar[int] = 0
    kernel_size: InitVar[int] = 0
    padding: InitVar[int] = 0
    stride: InitVar[int] = 1

    def __post_init__(
        self,
        vector_length: int,
        vector_data: bytes,
        tensor_h: int,
        tensor_w: int,
        channels_in: int,
        channels_out: int,
        kernel_size: int,
        padding: int,
        stride: int,
    ) -> None:
        vector_length = _initvar_or_default(vector_length, 0)
        vector_data = _initvar_or_default(vector_data, b"")
        tensor_h = _initvar_or_default(tensor_h, 0)
        tensor_w = _initvar_or_default(tensor_w, 0)
        channels_in = _initvar_or_default(channels_in, 0)
        channels_out = _initvar_or_default(channels_out, 0)
        kernel_size = _initvar_or_default(kernel_size, 0)
        padding = _initvar_or_default(padding, 0)
        stride = _initvar_or_default(stride, 1)
        if self.request_payload is None:
            if self.method == METHOD_GEMM:
                self.request_payload = GemmRequestPayload()
            elif self.method == METHOD_CONV2D or any(
                value
                for value in (tensor_h, tensor_w, channels_in, channels_out, kernel_size, padding)
            ):
                self.request_payload = Conv2dRequestPayload(
                    tensor_h=tensor_h,
                    tensor_w=tensor_w,
                    channels_in=channels_in,
                    channels_out=channels_out,
                    kernel_size=kernel_size,
                    padding=padding,
                    stride=stride,
                )
            else:
                self.request_payload = GemvRequestPayload(
                    vector_length=vector_length,
                    vector_data=vector_data,
                )
        elif self.method == METHOD_GEMV and not isinstance(
            self.request_payload,
            GemvRequestPayload,
        ):
            raise ValueError("gemv requests require a matching payload")
        elif self.method == METHOD_CONV2D and not isinstance(
            self.request_payload,
            Conv2dRequestPayload,
        ):
            raise ValueError("conv2d requests require a matching payload")
        elif self.method == METHOD_GEMM and not isinstance(
            self.request_payload,
            GemmRequestPayload,
        ):
            raise ValueError("gemm requests require a matching payload")

    @property
    def gemv_payload(self) -> GemvRequestPayload | None:
        if isinstance(self.request_payload, GemvRequestPayload):
            return self.request_payload
        return None

    @property
    def gemm_payload(self) -> GemmRequestPayload | None:
        if isinstance(self.request_payload, GemmRequestPayload):
            return self.request_payload
        return None

    @property
    def conv2d_payload(self) -> Conv2dRequestPayload | None:
        if isinstance(self.request_payload, Conv2dRequestPayload):
            return self.request_payload
        return None

    @property
    def vector_length(self) -> int:
        payload = self.gemv_payload
        return payload.vector_length if payload is not None else 0

    @property
    def vector_data(self) -> bytes:
        payload = self.gemv_payload
        return payload.vector_data if payload is not None else b""

    @property
    def tensor_h(self) -> int:
        payload = self.conv2d_payload
        return payload.tensor_h if payload is not None else 0

    @property
    def tensor_w(self) -> int:
        payload = self.conv2d_payload
        return payload.tensor_w if payload is not None else 0

    @property
    def channels_in(self) -> int:
        payload = self.conv2d_payload
        return payload.channels_in if payload is not None else 0

    @property
    def channels_out(self) -> int:
        payload = self.conv2d_payload
        return payload.channels_out if payload is not None else 0

    @property
    def kernel_size(self) -> int:
        payload = self.conv2d_payload
        return payload.kernel_size if payload is not None else 0

    @property
    def padding(self) -> int:
        payload = self.conv2d_payload
        return payload.padding if payload is not None else 0

    @property
    def stride(self) -> int:
        payload = self.conv2d_payload
        return payload.stride if payload is not None else 1


@dataclass(slots=True)
class WorkerTiming:
    """Per-worker timing breakdown observed by the main node plus self-reported compute split."""

    node_id: str = ""
    task_id: str = ""
    slice: str = ""
    wall_ms: int = 0
    artifact_fetch_ms: int = 0
    computation_ms: int = 0
    peripheral_ms: int = 0


@dataclass(slots=True)
class ResponseTiming:
    """Stage-by-stage timing breakdown for one client response."""

    dispatch_ms: int = 0
    task_window_ms: int = 0
    aggregate_ms: int = 0
    workers: tuple[WorkerTiming, ...] = ()


@dataclass(slots=True)
class ClientResponse:
    """Main-node response sent back to a client with a typed method payload."""

    request_id: str
    method: str
    size: str
    object_id: str
    stream_id: str
    timestamp_ms: int
    status_code: int
    error_message: str
    worker_count: int
    client_count: int
    client_id: str
    iteration_count: int
    task_id: str = ""
    elapsed_ms: int = 0
    response_payload: GemvResponsePayload | Conv2dResponsePayload | GemmResponsePayload | None = None
    result_artifact: ArtifactDescriptor | None = None
    timing: ResponseTiming | None = None
    output_length: InitVar[int] = 0
    output_vector: InitVar[bytes] = b""
    result_artifact_id: InitVar[str] = ""

    def __post_init__(self, output_length: int, output_vector: bytes, result_artifact_id: str) -> None:
        output_length = _initvar_or_default(output_length, 0)
        output_vector = _initvar_or_default(output_vector, b"")
        result_artifact_id = _initvar_or_default(result_artifact_id, "")
        if self.response_payload is None:
            if self.method == METHOD_GEMM:
                self.response_payload = GemmResponsePayload(
                    output_length=output_length,
                    output_vector=output_vector,
                )
            elif self.method == METHOD_CONV2D or result_artifact_id:
                self.response_payload = Conv2dResponsePayload(
                    output_length=output_length,
                    output_vector=output_vector,
                    result_artifact_id=result_artifact_id,
                )
            else:
                self.response_payload = GemvResponsePayload(
                    output_length=output_length,
                    output_vector=output_vector,
                )
        elif self.method == METHOD_GEMV and not isinstance(
            self.response_payload,
            GemvResponsePayload,
        ):
            raise ValueError("gemv responses require a matching payload")
        elif self.method == METHOD_CONV2D and not isinstance(
            self.response_payload,
            Conv2dResponsePayload,
        ):
            raise ValueError("conv2d responses require a matching payload")
        elif self.method == METHOD_GEMM and not isinstance(
            self.response_payload,
            GemmResponsePayload,
        ):
            raise ValueError("gemm responses require a matching payload")

    @property
    def gemv_payload(self) -> GemvResponsePayload | None:
        if isinstance(self.response_payload, GemvResponsePayload):
            return self.response_payload
        return None

    @property
    def gemm_payload(self) -> GemmResponsePayload | None:
        if isinstance(self.response_payload, GemmResponsePayload):
            return self.response_payload
        return None

    @property
    def conv2d_payload(self) -> Conv2dResponsePayload | None:
        if isinstance(self.response_payload, Conv2dResponsePayload):
            return self.response_payload
        return None

    @property
    def output_length(self) -> int:
        if self.gemv_payload is not None:
            return self.gemv_payload.output_length
        if self.conv2d_payload is not None:
            return self.conv2d_payload.output_length
        if self.gemm_payload is not None:
            return self.gemm_payload.output_length
        return 0

    @property
    def output_vector(self) -> bytes:
        if self.gemv_payload is not None:
            return self.gemv_payload.output_vector
        if self.conv2d_payload is not None:
            return self.conv2d_payload.output_vector
        if self.gemm_payload is not None:
            return self.gemm_payload.output_vector
        return b""

    @property
    def result_artifact_id(self) -> str:
        if self.result_artifact is not None:
            return self.result_artifact.artifact_id
        payload = self.conv2d_payload
        return payload.result_artifact_id if payload is not None else ""


@dataclass(slots=True)
class ArtifactRelease:
    """Main-node acknowledgement that one worker artifact can be deleted."""

    node_id: str
    task_id: str
    artifact_id: str
    timestamp_ms: int


@dataclass(slots=True)
class WorkerUpdate:
    """Idle-period worker performance refresh sent from compute node to main node."""

    node_id: str
    timestamp_ms: int
    performance: ComputePerformanceSummary


@dataclass(slots=True)
class RuntimeEnvelope:
    """Framed protobuf message used on the TCP runtime channel."""

    kind: MessageKind
    register_worker: RegisterWorker | None = None
    register_ok: RegisterOk | None = None
    heartbeat: Heartbeat | None = None
    heartbeat_ok: HeartbeatOk | None = None
    client_join: ClientJoin | None = None
    client_info_request: ClientInfoRequest | None = None
    client_info_reply: ClientInfoReply | None = None
    client_request_ok: ClientRequestOk | None = None
    client_request: ClientRequest | None = None
    client_response: ClientResponse | None = None
    task_assign: "TaskAssign" | None = None
    task_accept: "TaskAccept" | None = None
    task_fail: "TaskFail" | None = None
    task_result: "TaskResult" | None = None
    artifact_release: ArtifactRelease | None = None
    worker_update: WorkerUpdate | None = None


@dataclass(slots=True)
class TaskAssign:
    """Main-node instruction telling one worker to compute one method-specific slice."""

    request_id: str
    node_id: str
    task_id: str
    method: str
    size: str
    object_id: str
    stream_id: str
    timestamp_ms: int
    iteration_count: int
    task_payload: GemvTaskPayload | Conv2dTaskPayload | GemmTaskPayload | None = None
    row_start: InitVar[int] = 0
    row_end: InitVar[int] = 0
    vector_length: InitVar[int] = 0
    vector_data: InitVar[bytes] = b""
    start_oc: InitVar[int] = 0
    end_oc: InitVar[int] = 0
    tensor_h: InitVar[int] = 0
    tensor_w: InitVar[int] = 0
    channels_in: InitVar[int] = 0
    channels_out: InitVar[int] = 0
    kernel_size: InitVar[int] = 0
    padding: InitVar[int] = 0
    stride: InitVar[int] = 1
    weight_data: InitVar[bytes] = b""
    m_start: InitVar[int] = 0
    m_end: InitVar[int] = 0
    m: InitVar[int] = 0
    n: InitVar[int] = 0
    k: InitVar[int] = 0

    def __post_init__(
        self,
        row_start: int,
        row_end: int,
        vector_length: int,
        vector_data: bytes,
        start_oc: int,
        end_oc: int,
        tensor_h: int,
        tensor_w: int,
        channels_in: int,
        channels_out: int,
        kernel_size: int,
        padding: int,
        stride: int,
        weight_data: bytes,
        m_start: int,
        m_end: int,
        m: int,
        n: int,
        k: int,
    ) -> None:
        row_start = _initvar_or_default(row_start, 0)
        row_end = _initvar_or_default(row_end, 0)
        vector_length = _initvar_or_default(vector_length, 0)
        vector_data = _initvar_or_default(vector_data, b"")
        start_oc = _initvar_or_default(start_oc, 0)
        end_oc = _initvar_or_default(end_oc, 0)
        tensor_h = _initvar_or_default(tensor_h, 0)
        tensor_w = _initvar_or_default(tensor_w, 0)
        channels_in = _initvar_or_default(channels_in, 0)
        channels_out = _initvar_or_default(channels_out, 0)
        kernel_size = _initvar_or_default(kernel_size, 0)
        padding = _initvar_or_default(padding, 0)
        stride = _initvar_or_default(stride, 1)
        weight_data = _initvar_or_default(weight_data, b"")
        m_start = _initvar_or_default(m_start, 0)
        m_end = _initvar_or_default(m_end, 0)
        m = _initvar_or_default(m, 0)
        n = _initvar_or_default(n, 0)
        k = _initvar_or_default(k, 0)
        if self.task_payload is None:
            if self.method == METHOD_GEMM or any(value for value in (m_start, m_end, m, n, k)):
                self.task_payload = GemmTaskPayload(
                    m_start=m_start,
                    m_end=m_end,
                    m=m,
                    n=n,
                    k=k,
                )
            elif self.method == METHOD_CONV2D or any(
                value
                for value in (
                    start_oc,
                    end_oc,
                    tensor_h,
                    tensor_w,
                    channels_in,
                    channels_out,
                    kernel_size,
                    padding,
                    len(weight_data),
                )
            ):
                self.task_payload = Conv2dTaskPayload(
                    start_oc=start_oc,
                    end_oc=end_oc,
                    tensor_h=tensor_h,
                    tensor_w=tensor_w,
                    channels_in=channels_in,
                    channels_out=channels_out,
                    kernel_size=kernel_size,
                    padding=padding,
                    stride=stride,
                    weight_data=weight_data,
                )
            else:
                self.task_payload = GemvTaskPayload(
                    row_start=row_start,
                    row_end=row_end,
                    vector_length=vector_length,
                    vector_data=vector_data,
                )
        elif self.method == METHOD_GEMV and not isinstance(
            self.task_payload,
            GemvTaskPayload,
        ):
            raise ValueError("gemv tasks require a matching payload")
        elif self.method == METHOD_CONV2D and not isinstance(
            self.task_payload,
            Conv2dTaskPayload,
        ):
            raise ValueError("conv2d tasks require a matching payload")
        elif self.method == METHOD_GEMM and not isinstance(
            self.task_payload,
            GemmTaskPayload,
        ):
            raise ValueError("gemm tasks require a matching payload")

    @property
    def gemv_payload(self) -> GemvTaskPayload | None:
        if isinstance(self.task_payload, GemvTaskPayload):
            return self.task_payload
        return None

    @property
    def conv2d_payload(self) -> Conv2dTaskPayload | None:
        if isinstance(self.task_payload, Conv2dTaskPayload):
            return self.task_payload
        return None

    @property
    def gemm_payload(self) -> GemmTaskPayload | None:
        if isinstance(self.task_payload, GemmTaskPayload):
            return self.task_payload
        return None

    @property
    def row_start(self) -> int:
        payload = self.gemv_payload
        return payload.row_start if payload is not None else 0

    @property
    def row_end(self) -> int:
        payload = self.gemv_payload
        return payload.row_end if payload is not None else 0

    @property
    def vector_length(self) -> int:
        payload = self.gemv_payload
        return payload.vector_length if payload is not None else 0

    @property
    def vector_data(self) -> bytes:
        payload = self.gemv_payload
        return payload.vector_data if payload is not None else b""

    @property
    def start_oc(self) -> int:
        payload = self.conv2d_payload
        return payload.start_oc if payload is not None else 0

    @property
    def end_oc(self) -> int:
        payload = self.conv2d_payload
        return payload.end_oc if payload is not None else 0

    @property
    def tensor_h(self) -> int:
        payload = self.conv2d_payload
        return payload.tensor_h if payload is not None else 0

    @property
    def tensor_w(self) -> int:
        payload = self.conv2d_payload
        return payload.tensor_w if payload is not None else 0

    @property
    def channels_in(self) -> int:
        payload = self.conv2d_payload
        return payload.channels_in if payload is not None else 0

    @property
    def channels_out(self) -> int:
        payload = self.conv2d_payload
        return payload.channels_out if payload is not None else 0

    @property
    def kernel_size(self) -> int:
        payload = self.conv2d_payload
        return payload.kernel_size if payload is not None else 0

    @property
    def padding(self) -> int:
        payload = self.conv2d_payload
        return payload.padding if payload is not None else 0

    @property
    def stride(self) -> int:
        payload = self.conv2d_payload
        return payload.stride if payload is not None else 1

    @property
    def weight_data(self) -> bytes:
        payload = self.conv2d_payload
        return payload.weight_data if payload is not None else b""


@dataclass(slots=True)
class TaskAccept:
    """Worker acknowledgement that one assigned task was accepted."""

    request_id: str
    node_id: str
    task_id: str
    timestamp_ms: int
    status_code: int


@dataclass(slots=True)
class TaskFail:
    """Worker error result for one assigned task."""

    request_id: str
    node_id: str
    task_id: str
    timestamp_ms: int
    status_code: int
    error_message: str


@dataclass(slots=True)
class TaskResult:
    """Worker-computed method-specific task result."""

    request_id: str
    node_id: str
    task_id: str
    timestamp_ms: int
    status_code: int
    iteration_count: int
    result_payload: GemvResultPayload | Conv2dResultPayload | GemmResultPayload | None = None
    result_artifact: ArtifactDescriptor | None = None
    row_start: InitVar[int] = 0
    row_end: InitVar[int] = 0
    output_length: InitVar[int] = 0
    output_vector: InitVar[bytes] = b""
    start_oc: InitVar[int] = 0
    end_oc: InitVar[int] = 0
    output_h: InitVar[int] = 0
    output_w: InitVar[int] = 0
    result_artifact_id: InitVar[str] = ""
    m_start: InitVar[int] = 0
    m_end: InitVar[int] = 0
    method: InitVar[str] = ""

    def __post_init__(
        self,
        row_start: int,
        row_end: int,
        output_length: int,
        output_vector: bytes,
        start_oc: int,
        end_oc: int,
        output_h: int,
        output_w: int,
        result_artifact_id: str,
        m_start: int,
        m_end: int,
        method: str,
    ) -> None:
        row_start = _initvar_or_default(row_start, 0)
        row_end = _initvar_or_default(row_end, 0)
        output_length = _initvar_or_default(output_length, 0)
        output_vector = _initvar_or_default(output_vector, b"")
        start_oc = _initvar_or_default(start_oc, 0)
        end_oc = _initvar_or_default(end_oc, 0)
        output_h = _initvar_or_default(output_h, 0)
        output_w = _initvar_or_default(output_w, 0)
        result_artifact_id = _initvar_or_default(result_artifact_id, "")
        m_start = _initvar_or_default(m_start, 0)
        m_end = _initvar_or_default(m_end, 0)
        method = _initvar_or_default(method, "")
        if self.result_payload is None:
            if method == METHOD_GEMM or (m_end > m_start and method != METHOD_GEMV and method != METHOD_CONV2D):
                self.result_payload = GemmResultPayload(
                    m_start=m_start,
                    m_end=m_end,
                    output_length=output_length,
                    output_vector=output_vector,
                )
            elif any(value for value in (start_oc, end_oc, output_h, output_w)) or result_artifact_id:
                self.result_payload = Conv2dResultPayload(
                    start_oc=start_oc,
                    end_oc=end_oc,
                    output_h=output_h,
                    output_w=output_w,
                    output_length=output_length,
                    output_vector=output_vector,
                    result_artifact_id=result_artifact_id,
                )
            else:
                self.result_payload = GemvResultPayload(
                    row_start=row_start,
                    row_end=row_end,
                    output_length=output_length,
                    output_vector=output_vector,
                )

    @property
    def gemv_payload(self) -> GemvResultPayload | None:
        if isinstance(self.result_payload, GemvResultPayload):
            return self.result_payload
        return None

    @property
    def conv2d_payload(self) -> Conv2dResultPayload | None:
        if isinstance(self.result_payload, Conv2dResultPayload):
            return self.result_payload
        return None

    @property
    def gemm_payload(self) -> GemmResultPayload | None:
        if isinstance(self.result_payload, GemmResultPayload):
            return self.result_payload
        return None

    @property
    def row_start(self) -> int:
        payload = self.gemv_payload
        return payload.row_start if payload is not None else 0

    @property
    def row_end(self) -> int:
        payload = self.gemv_payload
        return payload.row_end if payload is not None else 0

    @property
    def m_start(self) -> int:
        payload = self.gemm_payload
        return payload.m_start if payload is not None else 0

    @property
    def m_end(self) -> int:
        payload = self.gemm_payload
        return payload.m_end if payload is not None else 0

    @property
    def output_length(self) -> int:
        if self.gemv_payload is not None:
            return self.gemv_payload.output_length
        if self.conv2d_payload is not None:
            return self.conv2d_payload.output_length
        if self.gemm_payload is not None:
            return self.gemm_payload.output_length
        return 0

    @property
    def output_vector(self) -> bytes:
        if self.gemv_payload is not None:
            return self.gemv_payload.output_vector
        if self.conv2d_payload is not None:
            return self.conv2d_payload.output_vector
        if self.gemm_payload is not None:
            return self.gemm_payload.output_vector
        return b""

    @property
    def start_oc(self) -> int:
        payload = self.conv2d_payload
        return payload.start_oc if payload is not None else 0

    @property
    def end_oc(self) -> int:
        payload = self.conv2d_payload
        return payload.end_oc if payload is not None else 0

    @property
    def output_h(self) -> int:
        payload = self.conv2d_payload
        return payload.output_h if payload is not None else 0

    @property
    def output_w(self) -> int:
        payload = self.conv2d_payload
        return payload.output_w if payload is not None else 0

    @property
    def result_artifact_id(self) -> str:
        if self.result_artifact is not None:
            return self.result_artifact.artifact_id
        payload = self.conv2d_payload
        return payload.result_artifact_id if payload is not None else ""


def _recv_exactly(sock: socket.socket, size: int) -> bytes | None:
    remaining = size
    chunks: list[bytes] = []

    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            if chunks:
                raise ConnectionError("peer closed the TCP session mid-frame")
            return None
        chunks.append(chunk)
        remaining -= len(chunk)

    return b"".join(chunks)


@trace_function
def send_message(sock: socket.socket, message: RuntimeEnvelope) -> None:
    """Send one length-prefixed protobuf message."""

    payload = encode_envelope(message)
    sock.sendall(FRAME_HEADER.pack(len(payload)))
    sock.sendall(payload)


@trace_function
def recv_message(sock: socket.socket, *, max_size: int) -> RuntimeEnvelope | None:
    """Receive one length-prefixed protobuf message."""

    header = _recv_exactly(sock, FRAME_HEADER.size)
    if header is None:
        return None

    (payload_size,) = FRAME_HEADER.unpack(header)
    if payload_size <= 0 or payload_size > max_size:
        raise ValueError(f"invalid protobuf message size: {payload_size}")

    payload = _recv_exactly(sock, payload_size)
    if payload is None:
        return None

    return parse_envelope(payload)


@trace_function
def build_register_worker(
    node_name: str,
    hardware: HardwareProfile,
    performance: ComputePerformanceSummary | None = None,
) -> RuntimeEnvelope:
    """Create a compute-node registration message."""

    if performance is None:
        performance = ComputePerformanceSummary()

    return RuntimeEnvelope(
        kind=MessageKind.REGISTER_WORKER,
        register_worker=RegisterWorker(node_name=node_name, hardware=hardware, performance=performance),
    )


@trace_function
def build_register_ok(
    main_node_ip: str,
    main_node_port: int,
    *,
    node_id: str = "",
    main_node_name: str = MAIN_NODE_NAME,
) -> RuntimeEnvelope:
    """Create a main-node registration-ack message."""

    return RuntimeEnvelope(
        kind=MessageKind.REGISTER_OK,
        register_ok=RegisterOk(
            main_node_name=main_node_name,
            main_node_ip=main_node_ip,
            main_node_port=main_node_port,
            node_id=node_id,
        ),
    )


@trace_function
def build_heartbeat(main_node_name: str = MAIN_NODE_NAME, unix_time_ms: int | None = None) -> RuntimeEnvelope:
    """Create a main-node heartbeat message."""

    if unix_time_ms is None:
        unix_time_ms = int(time.time() * 1000)

    return RuntimeEnvelope(
        kind=MessageKind.HEARTBEAT,
        heartbeat=Heartbeat(main_node_name=main_node_name, unix_time_ms=unix_time_ms),
    )


@trace_function
def build_heartbeat_ok(
    node_name: str,
    heartbeat_unix_time_ms: int,
    received_unix_time_ms: int | None = None,
    *,
    active_task_ids: list[str] | tuple[str, ...] | None = None,
) -> RuntimeEnvelope:
    """Create a worker heartbeat acknowledgement."""

    if received_unix_time_ms is None:
        received_unix_time_ms = int(time.time() * 1000)
    if active_task_ids is None:
        active_task_ids = ()

    return RuntimeEnvelope(
        kind=MessageKind.HEARTBEAT_OK,
        heartbeat_ok=HeartbeatOk(
            node_name=node_name,
            heartbeat_unix_time_ms=heartbeat_unix_time_ms,
            received_unix_time_ms=received_unix_time_ms,
            active_task_ids=tuple(active_task_ids),
        ),
    )


@trace_function
def build_client_join(client_name: str = SUPERWEB_CLIENT_NAME) -> RuntimeEnvelope:
    """Create a client join message."""

    return RuntimeEnvelope(kind=MessageKind.CLIENT_JOIN, client_join=ClientJoin(client_name=client_name))


@trace_function
def build_client_info_request(
    *,
    client_id: str,
    client_name: str,
    timestamp_ms: int | None = None,
) -> RuntimeEnvelope:
    """Create one client-info polling request."""

    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    return RuntimeEnvelope(
        kind=MessageKind.CLIENT_INFO_REQUEST,
        client_info_request=ClientInfoRequest(
            client_id=client_id,
            client_name=client_name,
            timestamp_ms=timestamp_ms,
        ),
    )


@trace_function
def build_client_info_reply(
    *,
    client_id: str,
    request_timestamp_ms: int,
    timeout_ms: int,
    has_active_tasks: bool,
    active_task_ids: list[str] | tuple[str, ...] | None = None,
    reply_timestamp_ms: int | None = None,
) -> RuntimeEnvelope:
    """Create one main-node reply to a client-info poll."""

    if reply_timestamp_ms is None:
        reply_timestamp_ms = int(time.time() * 1000)
    if active_task_ids is None:
        active_task_ids = ()
    return RuntimeEnvelope(
        kind=MessageKind.CLIENT_INFO_REPLY,
        client_info_reply=ClientInfoReply(
            client_id=client_id,
            request_timestamp_ms=request_timestamp_ms,
            reply_timestamp_ms=reply_timestamp_ms,
            timeout_ms=timeout_ms,
            has_active_tasks=has_active_tasks,
            active_task_ids=tuple(active_task_ids),
        ),
    )


@trace_function
def build_client_request_ok(
    *,
    client_id: str,
    task_id: str,
    method: str,
    size: str = "",
    object_id: str,
    accepted_timestamp_ms: int | None = None,
    upload_id: str = "",
    download_id: str = "",
    data_endpoint_host: str = "",
    data_endpoint_port: int = 0,
) -> RuntimeEnvelope:
    """Create one main-node acknowledgement carrying the assigned task id."""

    if accepted_timestamp_ms is None:
        accepted_timestamp_ms = int(time.time() * 1000)
    return RuntimeEnvelope(
        kind=MessageKind.CLIENT_REQUEST_OK,
        client_request_ok=ClientRequestOk(
            client_id=client_id,
            task_id=task_id,
            method=method,
            size=size,
            object_id=object_id,
            accepted_timestamp_ms=accepted_timestamp_ms,
            upload_id=upload_id,
            download_id=download_id,
            data_endpoint_host=data_endpoint_host,
            data_endpoint_port=data_endpoint_port,
        ),
    )


@trace_function
def build_client_request(
    client_name: str,
    request_id: str,
    method: str,
    vector_data: bytes,
    *,
    size: str = "",
    object_id: str = "",
    stream_id: str = "",
    timestamp_ms: int | None = None,
    vector_length: int | None = None,
    iteration_count: int = 1,
    tensor_h: int = 0,
    tensor_w: int = 0,
    channels_in: int = 0,
    channels_out: int = 0,
    kernel_size: int = 0,
    padding: int = 0,
    stride: int = 1,
    request_payload: GemvRequestPayload | Conv2dRequestPayload | GemmRequestPayload | None = None,
) -> RuntimeEnvelope:
    """Create a client request message."""

    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if vector_length is None:
        vector_length = len(vector_data) // 4
    if iteration_count <= 0:
        raise ValueError("iteration_count must be positive")

    return RuntimeEnvelope(
        kind=MessageKind.CLIENT_REQUEST,
        client_request=ClientRequest(
            request_id=request_id,
            client_name=client_name,
            method=method,
            size=size,
            object_id=object_id,
            stream_id=stream_id,
            timestamp_ms=timestamp_ms,
            iteration_count=iteration_count,
            request_payload=request_payload,
            vector_length=vector_length,
            vector_data=vector_data,
            tensor_h=tensor_h,
            tensor_w=tensor_w,
            channels_in=channels_in,
            channels_out=channels_out,
            kernel_size=kernel_size,
            padding=padding,
            stride=stride,
        ),
    )


@trace_function
def build_client_response(
    request_id: str,
    status_code: int,
    *,
    method: str = "",
    size: str = "",
    object_id: str = "",
    stream_id: str = "",
    error_message: str = "",
    output_vector: bytes = b"",
    timestamp_ms: int | None = None,
    output_length: int | None = None,
    worker_count: int = 0,
    client_count: int = 0,
    client_id: str = "",
    iteration_count: int = 1,
    task_id: str = "",
    elapsed_ms: int = 0,
    result_artifact_id: str = "",
    result_artifact: ArtifactDescriptor | None = None,
    response_payload: GemvResponsePayload | Conv2dResponsePayload | GemmResponsePayload | None = None,
    timing: ResponseTiming | None = None,
) -> RuntimeEnvelope:
    """Create a main-node response to a client join or request."""

    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if output_length is None:
        output_length = len(output_vector) // 4
    if iteration_count <= 0:
        raise ValueError("iteration_count must be positive")

    return RuntimeEnvelope(
        kind=MessageKind.CLIENT_RESPONSE,
        client_response=ClientResponse(
            request_id=request_id,
            method=method,
            size=size,
            object_id=object_id,
            stream_id=stream_id,
            timestamp_ms=timestamp_ms,
            status_code=status_code,
            error_message=error_message,
            worker_count=worker_count,
            client_count=client_count,
            client_id=client_id,
            iteration_count=iteration_count,
            task_id=task_id,
            elapsed_ms=elapsed_ms,
            response_payload=response_payload,
            result_artifact=result_artifact,
            timing=timing,
            result_artifact_id=result_artifact_id,
            output_length=output_length,
            output_vector=output_vector,
        ),
    )


@trace_function
def build_task_assign(
    request_id: str,
    node_id: str,
    task_id: str,
    method: str,
    row_start: int = 0,
    row_end: int = 0,
    vector_data: bytes = b"",
    *,
    size: str = "",
    object_id: str = "",
    stream_id: str = "",
    timestamp_ms: int | None = None,
    vector_length: int | None = None,
    iteration_count: int = 1,
    start_oc: int = 0,
    end_oc: int = 0,
    tensor_h: int = 0,
    tensor_w: int = 0,
    channels_in: int = 0,
    channels_out: int = 0,
    kernel_size: int = 0,
    padding: int = 0,
    stride: int = 1,
    weight_data: bytes = b"",
    m_start: int = 0,
    m_end: int = 0,
    m: int = 0,
    n: int = 0,
    k: int = 0,
    task_payload: GemvTaskPayload | Conv2dTaskPayload | GemmTaskPayload | None = None,
) -> RuntimeEnvelope:
    """Create one task assignment message for a worker."""

    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if vector_length is None:
        vector_length = len(vector_data) // 4
    if iteration_count <= 0:
        raise ValueError("iteration_count must be positive")

    return RuntimeEnvelope(
        kind=MessageKind.TASK_ASSIGN,
        task_assign=TaskAssign(
            request_id=request_id,
            node_id=node_id,
            task_id=task_id,
            method=method,
            size=size,
            object_id=object_id,
            stream_id=stream_id,
            timestamp_ms=timestamp_ms,
            iteration_count=iteration_count,
            task_payload=task_payload,
            row_start=row_start,
            row_end=row_end,
            vector_length=vector_length,
            vector_data=vector_data,
            start_oc=start_oc,
            end_oc=end_oc,
            tensor_h=tensor_h,
            tensor_w=tensor_w,
            channels_in=channels_in,
            channels_out=channels_out,
            kernel_size=kernel_size,
            padding=padding,
            stride=stride,
            weight_data=weight_data,
            m_start=m_start,
            m_end=m_end,
            m=m,
            n=n,
            k=k,
        ),
    )


@trace_function
def build_task_accept(
    request_id: str,
    node_id: str,
    task_id: str,
    status_code: int,
    *,
    timestamp_ms: int | None = None,
) -> RuntimeEnvelope:
    """Create a task-accept acknowledgement from a worker."""

    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    return RuntimeEnvelope(
        kind=MessageKind.TASK_ACCEPT,
        task_accept=TaskAccept(
            request_id=request_id,
            node_id=node_id,
            task_id=task_id,
            timestamp_ms=timestamp_ms,
            status_code=status_code,
        ),
    )


@trace_function
def build_task_fail(
    request_id: str,
    node_id: str,
    task_id: str,
    status_code: int,
    error_message: str,
    *,
    timestamp_ms: int | None = None,
) -> RuntimeEnvelope:
    """Create a task-failure message from a worker."""

    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    return RuntimeEnvelope(
        kind=MessageKind.TASK_FAIL,
        task_fail=TaskFail(
            request_id=request_id,
            node_id=node_id,
            task_id=task_id,
            timestamp_ms=timestamp_ms,
            status_code=status_code,
            error_message=error_message,
        ),
    )


@trace_function
def build_task_result(
    request_id: str,
    node_id: str,
    task_id: str,
    status_code: int,
    row_start: int = 0,
    row_end: int = 0,
    output_vector: bytes = b"",
    *,
    timestamp_ms: int | None = None,
    output_length: int | None = None,
    iteration_count: int = 1,
    start_oc: int = 0,
    end_oc: int = 0,
    output_h: int = 0,
    output_w: int = 0,
    result_artifact_id: str = "",
    result_artifact: ArtifactDescriptor | None = None,
    result_payload: GemvResultPayload | Conv2dResultPayload | GemmResultPayload | None = None,
    m_start: int = 0,
    m_end: int = 0,
    method: str = "",
) -> RuntimeEnvelope:
    """Create a task-result message from a worker."""

    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if output_length is None:
        output_length = len(output_vector) // 4
    if iteration_count <= 0:
        raise ValueError("iteration_count must be positive")

    return RuntimeEnvelope(
        kind=MessageKind.TASK_RESULT,
        task_result=TaskResult(
            request_id=request_id,
            node_id=node_id,
            task_id=task_id,
            timestamp_ms=timestamp_ms,
            status_code=status_code,
            iteration_count=iteration_count,
            result_payload=result_payload,
            result_artifact=result_artifact,
            row_start=row_start,
            row_end=row_end,
            output_length=output_length,
            output_vector=output_vector,
            start_oc=start_oc,
            end_oc=end_oc,
            output_h=output_h,
            output_w=output_w,
            result_artifact_id=result_artifact_id,
            m_start=m_start,
            m_end=m_end,
            method=method,
        ),
    )


def describe_message_kind(kind: MessageKind) -> str:
    """Return the user-facing label for a runtime message kind."""

    if kind == MessageKind.REGISTER_WORKER:
        return RUNTIME_MSG_REGISTER_WORKER
    if kind == MessageKind.REGISTER_OK:
        return RUNTIME_MSG_REGISTER_OK
    if kind == MessageKind.HEARTBEAT:
        return RUNTIME_MSG_HEARTBEAT
    if kind == MessageKind.HEARTBEAT_OK:
        return RUNTIME_MSG_HEARTBEAT_OK
    if kind == MessageKind.CLIENT_JOIN:
        return RUNTIME_MSG_CLIENT_JOIN
    if kind == MessageKind.CLIENT_INFO_REQUEST:
        return RUNTIME_MSG_CLIENT_INFO_REQUEST
    if kind == MessageKind.CLIENT_INFO_REPLY:
        return RUNTIME_MSG_CLIENT_INFO_REPLY
    if kind == MessageKind.CLIENT_REQUEST:
        return RUNTIME_MSG_CLIENT_REQUEST
    if kind == MessageKind.CLIENT_REQUEST_OK:
        return RUNTIME_MSG_CLIENT_REQUEST_OK
    if kind == MessageKind.CLIENT_RESPONSE:
        return RUNTIME_MSG_CLIENT_RESPONSE
    if kind == MessageKind.TASK_ASSIGN:
        return RUNTIME_MSG_TASK_ASSIGN
    if kind == MessageKind.TASK_ACCEPT:
        return RUNTIME_MSG_TASK_ACCEPT
    if kind == MessageKind.TASK_FAIL:
        return RUNTIME_MSG_TASK_FAIL
    if kind == MessageKind.TASK_RESULT:
        return RUNTIME_MSG_TASK_RESULT
    if kind == MessageKind.ARTIFACT_RELEASE:
        return RUNTIME_MSG_ARTIFACT_RELEASE
    if kind == MessageKind.WORKER_UPDATE:
        return RUNTIME_MSG_WORKER_UPDATE
    return kind.name


