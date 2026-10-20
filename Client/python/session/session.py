"""High-level Python client session helpers."""

from __future__ import annotations

import hashlib
import logging
import socket
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.constants import (
    CONV2D_DEFAULT_STATS_MAX_SAMPLES,
    DEFAULT_CLIENT_NAME,
    DEFAULT_ARTIFACT_TRANSFER_TIMEOUT,
    DEFAULT_CLIENT_INFO_FAILURE_LIMIT,
    DEFAULT_CLIENT_INFO_INTERVAL,
    DEFAULT_DOWNLOAD_DIR,
    DEFAULT_MAX_PROTOBUF_MESSAGE_SIZE,
    DEFAULT_TCP_CONNECT_TIMEOUT,
    DEFAULT_TCP_PORT,
    METHOD_FREE_CONTENT,
    METHOD_GEMM,
    METHOD_GEMV,
    METHOD_CONV2D,
    STATUS_OK,
    WORKLOAD_SIZE_LARGE,
)
from core.float32_codec import pack_float32_values, unpack_float32_bytes
from discovery.mdns import discover_main_node
from transport.large_data_transfer import (
    deliver_bytes,
    fetch_artifact_to_file,
    fetch_download_over_socket,
    open_data_plane_socket,
)
from dataset.conv2d import build_conv2d_spec, generate_default_weight_bytes
from dataset.gemv import normalize_request_size, vector_length_for_size
from wire.runtime import (
    ClientResponse,
    GemmRequestPayload,
    GemvRequestPayload,
    MessageKind,
    Conv2dRequestPayload,
    build_heartbeat_ok,
    build_client_join,
    build_client_info_request,
    build_client_request,
    recv_message,
    send_message,
)


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RequestResult:
    """Decoded client response plus any float32-decoded output."""

    response: ClientResponse
    output_values: list[float]
    output_bytes: bytes
    accepted_task_id: str = ""
    local_request_id: str = ""
    request_accept_latency_ms: int = 0
    client_elapsed_ms: int = 0
    transmission_overhead_ms: int = 0
    saved_output_path: Path | None = None
    artifact_path: Path | None = None
    artifact_downloaded: bool = False
    artifact_checksum: str = ""


_INVALID_OUTPUT_PATH_CHARS = str.maketrans({char: "-" for char in '<>:"/\\|?*'})


class _Conv2dUploadCoordinator:
    """Own the pre-opened data socket that carries one conv2d upload+download.

    Use this alongside ``SuperWebClientSession.request_payload`` so that after
    the main node returns CLIENT_REQUEST_OK the client can immediately push
    its weight bytes over the data plane while the control socket still waits
    for CLIENT_RESPONSE. The same socket is then reused to pull the result
    artifact the main node produces, avoiding a second TCP setup.
    """

    def __init__(
        self,
        *,
        weight_bytes: bytes,
        weight_checksum: str,
        content_type: str,
        timeout: float,
    ) -> None:
        self._weight_bytes = weight_bytes
        self._weight_checksum = weight_checksum
        self._content_type = content_type
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._upload_thread: threading.Thread | None = None
        self._upload_error: BaseException | None = None
        self._upload_id = ""
        self._download_id = ""

    def on_request_ok(self, request_ok) -> None:
        """Open the data socket and start the upload once REQUEST_OK is in."""

        self._upload_id = request_ok.upload_id
        self._download_id = request_ok.download_id
        if not self._upload_id:
            raise RuntimeError("main did not allocate an upload slot for conv2d weight")
        if not request_ok.data_endpoint_host or not request_ok.data_endpoint_port:
            raise RuntimeError("main did not advertise a data-plane endpoint for conv2d")
        logger.info(
            "conv2d data-plane opening host=%s port=%s upload_id=%s download_id=%s upload_bytes=%s",
            request_ok.data_endpoint_host,
            request_ok.data_endpoint_port,
            self._upload_id,
            self._download_id or "<none>",
            len(self._weight_bytes),
        )
        self._sock = open_data_plane_socket(
            request_ok.data_endpoint_host,
            request_ok.data_endpoint_port,
            timeout=self._timeout,
        )
        self._upload_thread = threading.Thread(
            target=self._run_upload,
            name="superweb-client-upload",
            daemon=True,
        )
        self._upload_thread.start()

    def _run_upload(self) -> None:
        try:
            assert self._sock is not None
            deliver_bytes(
                self._sock,
                upload_id=self._upload_id,
                payload=self._weight_bytes,
                checksum=self._weight_checksum,
                content_type=self._content_type,
            )
            logger.info(
                "conv2d upload finished upload_id=%s bytes=%s",
                self._upload_id,
                len(self._weight_bytes),
            )
        except BaseException as exc:  # noqa: BLE001
            logger.error("conv2d upload failed upload_id=%s: %s", self._upload_id, exc)
            self._upload_error = exc

    def await_upload(self) -> None:
        if self._upload_thread is not None:
            self._upload_thread.join()
        if self._upload_error is not None:
            raise RuntimeError(f"conv2d weight upload failed: {self._upload_error}") from self._upload_error

    def materialize_output(self, response, default_build_output_path) -> tuple[bytes, Path | None, Path | None, bool, str]:
        """Finish the data-plane exchange once CLIENT_RESPONSE has arrived."""

        try:
            self.await_upload()
            if response.result_artifact is None and not self._download_id:
                output_bytes = response.output_vector
                if not output_bytes:
                    return b"", None, None, False, ""
                output_path = default_build_output_path(response)
                output_path.write_bytes(output_bytes)
                return output_bytes, output_path, None, False, ""

            assert self._sock is not None
            destination_path = default_build_output_path(
                response,
                content_type=response.result_artifact.content_type if response.result_artifact else "application/octet-stream",
            )
            download_id = self._download_id or (
                response.result_artifact.artifact_id if response.result_artifact else ""
            )
            _saved_path, checksum = fetch_download_over_socket(
                self._sock,
                download_id=download_id,
                destination_path=destination_path,
            )
            return b"", destination_path, destination_path, True, checksum
        finally:
            self.close()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _describe_stats_payload(response: ClientResponse) -> str:
    if response.method != METHOD_CONV2D:
        return "<none>"
    payload = response.conv2d_payload
    if payload is None or not payload.stats_element_count:
        return "<none>"
    count = payload.stats_element_count
    mean = payload.stats_sum / count if count else 0.0
    variance = max(payload.stats_sum_squares / count - mean * mean, 0.0) if count else 0.0
    return (
        f"element_count={count} sum={payload.stats_sum:.6f} "
        f"sum_squares={payload.stats_sum_squares:.6f} mean={mean:.6f} "
        f"variance={variance:.6f} samples={len(payload.stats_samples)}"
    )


def _describe_response_timing(response: ClientResponse) -> str:
    timing = response.timing
    if timing is None:
        return "<none>"
    worker_summaries: list[str] = []
    for worker in timing.workers:
        suffix = ""
        if worker.artifact_fetch_ms:
            suffix += f"+fetch:{worker.artifact_fetch_ms}ms"
        if worker.computation_ms:
            suffix += f"+compute:{worker.computation_ms}ms"
        if worker.peripheral_ms:
            suffix += f"+peripheral:{worker.peripheral_ms}ms"
        slice_label = worker.slice or "<slice?>"
        node_label = worker.node_id or "<node?>"
        worker_summaries.append(f"{node_label}|{slice_label}=wall:{worker.wall_ms}ms{suffix}")
    workers_field = ",".join(worker_summaries) if worker_summaries else "<none>"
    return (
        f"dispatch={timing.dispatch_ms}ms task_window={timing.task_window_ms}ms "
        f"aggregate={timing.aggregate_ms}ms workers=[{workers_field}]"
    )


def _sanitize_output_component(value: str, fallback: str) -> str:
    sanitized = value.translate(_INVALID_OUTPUT_PATH_CHARS).strip(" .-_")
    return sanitized or fallback


class SuperWebClientSession:
    """Simple TCP session for client join and request/response exchange."""

    def __init__(
        self,
        sock: socket.socket,
        *,
        max_message_size: int = DEFAULT_MAX_PROTOBUF_MESSAGE_SIZE,
        artifact_timeout: float = DEFAULT_ARTIFACT_TRANSFER_TIMEOUT,
        artifact_download_dir: Path | None = None,
    ) -> None:
        self.sock = sock
        self.max_message_size = max_message_size
        self.artifact_timeout = artifact_timeout
        self.artifact_download_dir = DEFAULT_DOWNLOAD_DIR if artifact_download_dir is None else Path(artifact_download_dir)
        self.client_name = DEFAULT_CLIENT_NAME
        self.client_id = ""
        self._send_lock = threading.Lock()
        self._condition = threading.Condition()
        self._closed_reason: str | None = None
        self._response_queue = deque()
        self._request_ok_queue_by_local_id: dict[str, deque] = defaultdict(deque)
        self._awaiting_request_ok_local_ids = deque()
        self._info_reply_queue: dict[int, deque] = defaultdict(deque)
        self._pending_task_ids: set[str] = set()
        self._local_request_id_by_task_id: dict[str, str] = {}
        self._preferred_output_name_by_local_request_id: dict[str, str] = {}
        self._preferred_output_name_by_task_id: dict[str, str] = {}
        self._info_poll_failures = 0
        self._info_request_in_flight_ms: int | None = None
        self._stop_event = threading.Event()
        self._system_dead = False
        self._reader_thread = threading.Thread(target=self._reader_loop, name="superweb-client-reader", daemon=True)
        self._reader_thread.start()
        self._info_poll_thread: threading.Thread | None = None
        try:
            peer = self.sock.getpeername()
            local = self.sock.getsockname()
            logger.info("session opened peer=%s:%s local=%s:%s", peer[0], peer[1], local[0], local[1])
        except OSError:
            logger.info("session opened peer=<unknown> local=<unknown>")

    def close(self) -> None:
        """Close the underlying TCP socket."""

        self._stop_event.set()
        try:
            self.sock.close()
        except OSError:
            pass
        finally:
            with self._condition:
                if self._closed_reason is None:
                    self._closed_reason = "client session closed"
                self._condition.notify_all()
        logger.info("session closed reason=%s", self._closed_reason)

    def _ensure_alive(self) -> None:
        if self._system_dead:
            raise ConnectionError("main node info polling failed repeatedly; treating the system as dead")
        if self._closed_reason is not None:
            raise ConnectionError(self._closed_reason)

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                message = recv_message(self.sock, max_size=self.max_message_size)
            except socket.timeout:
                continue
            except (OSError, ValueError, ConnectionError) as exc:
                if self._stop_event.is_set():
                    logger.debug("reader loop exiting after shutdown: %s", exc)
                    with self._condition:
                        if self._closed_reason is None:
                            self._closed_reason = "client session closed"
                        self._condition.notify_all()
                    return
                logger.warning("reader loop aborting: %s", exc)
                with self._condition:
                    if self._closed_reason is None:
                        self._closed_reason = str(exc)
                    self._condition.notify_all()
                return

            if message is None:
                logger.warning("main node closed the TCP session")
                with self._condition:
                    if self._closed_reason is None:
                        self._closed_reason = "main node closed the TCP session"
                    self._condition.notify_all()
                return

            if message.kind == MessageKind.HEARTBEAT and message.heartbeat is not None:
                logger.debug("heartbeat received unix_time_ms=%s", message.heartbeat.unix_time_ms)
                with self._send_lock:
                    send_message(
                        self.sock,
                        build_heartbeat_ok(
                            node_name=self.client_name,
                            heartbeat_unix_time_ms=message.heartbeat.unix_time_ms,
                        ),
                    )
                continue

            with self._condition:
                if message.kind == MessageKind.CLIENT_RESPONSE and message.client_response is not None:
                    logger.debug(
                        "queued CLIENT_RESPONSE task_id=%s status=%s",
                        message.client_response.task_id or "<none>",
                        message.client_response.status_code,
                    )
                    self._response_queue.append(message.client_response)
                elif message.kind == MessageKind.CLIENT_REQUEST_OK and message.client_request_ok is not None:
                    if not self._awaiting_request_ok_local_ids:
                        logger.error(
                            "received CLIENT_REQUEST_OK task_id=%s with no pending request",
                            message.client_request_ok.task_id,
                        )
                        if self._closed_reason is None:
                            self._closed_reason = "unexpected client request acknowledgement without a pending request"
                    else:
                        local_request_id = self._awaiting_request_ok_local_ids.popleft()
                        logger.debug(
                            "CLIENT_REQUEST_OK local_request_id=%s task_id=%s",
                            local_request_id,
                            message.client_request_ok.task_id,
                        )
                        self._request_ok_queue_by_local_id[local_request_id].append(message.client_request_ok)
                elif message.kind == MessageKind.CLIENT_INFO_REPLY and message.client_info_reply is not None:
                    logger.debug(
                        "CLIENT_INFO_REPLY request_timestamp_ms=%s",
                        message.client_info_reply.request_timestamp_ms,
                    )
                    self._info_reply_queue[message.client_info_reply.request_timestamp_ms].append(message.client_info_reply)
                    self._info_poll_failures = 0
                    if self._info_request_in_flight_ms == message.client_info_reply.request_timestamp_ms:
                        self._info_request_in_flight_ms = None
                else:
                    logger.error("unexpected runtime message kind=%s", message.kind.name)
                    if self._closed_reason is None:
                        self._closed_reason = f"unexpected runtime message: {message.kind.name}"
                self._condition.notify_all()

    def _wait_for(self, timeout: float | None, pop_message, description: str):
        deadline = None if timeout is None else (time.monotonic() + timeout)
        with self._condition:
            while True:
                message = pop_message()
                if message is not None:
                    return message
                if self._closed_reason is not None:
                    raise ConnectionError(self._closed_reason)
                if deadline is None:
                    self._condition.wait(0.5)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout(f"timed out waiting for {description}")
                self._condition.wait(remaining)

    def _pop_client_response(self):
        return self._response_queue.popleft() if self._response_queue else None

    def _pop_client_request_ok(self, local_request_id: str):
        queue = self._request_ok_queue_by_local_id.get(local_request_id)
        if not queue:
            return None
        message = queue.popleft()
        if not queue:
            self._request_ok_queue_by_local_id.pop(local_request_id, None)
        return message

    def _pop_client_info_reply(self, request_timestamp_ms: int):
        queue = self._info_reply_queue.get(request_timestamp_ms)
        if not queue:
            return None
        message = queue.popleft()
        if not queue:
            self._info_reply_queue.pop(request_timestamp_ms, None)
        return message

    def _start_info_polling(self) -> None:
        if self._info_poll_thread is not None and self._info_poll_thread.is_alive():
            return
        self._info_poll_thread = threading.Thread(
            target=self._client_info_poll_loop,
            name="superweb-client-info",
            daemon=True,
        )
        self._info_poll_thread.start()

    def _client_info_poll_loop(self) -> None:
        while not self._stop_event.wait(DEFAULT_CLIENT_INFO_INTERVAL):
            should_close = False
            with self._condition:
                has_pending_tasks = bool(self._pending_task_ids)
                if not self.client_id or not has_pending_tasks:
                    self._info_request_in_flight_ms = None
                    self._info_poll_failures = 0
                    continue
                if self._info_request_in_flight_ms is not None:
                    self._info_poll_failures += 1
                    self._info_request_in_flight_ms = None
                    logger.warning(
                        "CLIENT_INFO_REPLY missed (consecutive=%s limit=%s)",
                        self._info_poll_failures,
                        DEFAULT_CLIENT_INFO_FAILURE_LIMIT,
                    )
                if self._info_poll_failures >= DEFAULT_CLIENT_INFO_FAILURE_LIMIT:
                    self._system_dead = True
                    if self._closed_reason is None:
                        self._closed_reason = (
                            f"client info polling failed {DEFAULT_CLIENT_INFO_FAILURE_LIMIT} consecutive times"
                        )
                    logger.error(
                        "system considered dead after %s consecutive liveness failures",
                        DEFAULT_CLIENT_INFO_FAILURE_LIMIT,
                    )
                    self._condition.notify_all()
                    should_close = True
                request_timestamp_ms = int(time.time() * 1000)
                self._info_request_in_flight_ms = request_timestamp_ms
            if should_close:
                self.close()
                return
            try:
                with self._send_lock:
                    send_message(
                        self.sock,
                        build_client_info_request(
                            client_id=self.client_id,
                            client_name=self.client_name,
                            timestamp_ms=request_timestamp_ms,
                        ),
                    )
                logger.debug(
                    "CLIENT_INFO_REQUEST sent timestamp_ms=%s pending_tasks=%s",
                    request_timestamp_ms,
                    len(self._pending_task_ids),
                )
            except (OSError, ValueError, ConnectionError) as exc:
                logger.warning("CLIENT_INFO_REQUEST send failed: %s", exc)
                with self._condition:
                    self._info_request_in_flight_ms = None
                    self._info_poll_failures += 1
                    if self._info_poll_failures >= DEFAULT_CLIENT_INFO_FAILURE_LIMIT:
                        self._system_dead = True
                        if self._closed_reason is None:
                            self._closed_reason = (
                                f"client info polling failed {DEFAULT_CLIENT_INFO_FAILURE_LIMIT} consecutive times"
                            )
                        self._condition.notify_all()
                        should_close = True
                if should_close:
                    self.close()
                    return

    def _mark_task_started(self, local_request_id: str, task_id: str) -> None:
        with self._condition:
            self._pending_task_ids.add(task_id)
            self._local_request_id_by_task_id[task_id] = local_request_id
            preferred_output_name = self._preferred_output_name_by_local_request_id.pop(local_request_id, "")
            if preferred_output_name:
                self._preferred_output_name_by_task_id[task_id] = preferred_output_name
        self._start_info_polling()

    def _mark_task_finished(self, task_id: str) -> None:
        with self._condition:
            self._pending_task_ids.discard(task_id)
            self._local_request_id_by_task_id.pop(task_id, None)
            self._preferred_output_name_by_task_id.pop(task_id, None)
            if not self._pending_task_ids:
                self._info_request_in_flight_ms = None
                self._info_poll_failures = 0

    def _wait_for_client_request_ok(self, local_request_id: str, *, timeout: float | None):
        return self._wait_for(
            timeout,
            lambda: self._pop_client_request_ok(local_request_id),
            f"client request acknowledgement for {local_request_id}",
        )

    def _wait_for_client_info_reply(self, request_timestamp_ms: int, *, timeout: float | None):
        return self._wait_for(
            timeout,
            lambda: self._pop_client_info_reply(request_timestamp_ms),
            f"client info reply for {request_timestamp_ms}",
        )

    def _has_pending_requests(self) -> bool:
        with self._condition:
            return bool(self._pending_task_ids)

    def join(self, client_name: str = DEFAULT_CLIENT_NAME) -> ClientResponse:
        """Send CLIENT_JOIN and expect the initial CLIENT_RESPONSE."""

        self._ensure_alive()
        self.client_name = client_name
        logger.info("sending CLIENT_JOIN client_name=%s", client_name)
        with self._send_lock:
            send_message(self.sock, build_client_join(client_name))
        response = self.expect_client_response()
        if response.status_code != STATUS_OK:
            logger.error(
                "CLIENT_JOIN rejected status=%s error_message=%s",
                response.status_code,
                response.error_message or "<none>",
            )
            raise RuntimeError(response.error_message or "CLIENT_JOIN failed")
        self.client_id = response.client_id
        logger.info(
            "CLIENT_JOIN accepted client_id=%s worker_count=%s client_count=%s",
            response.client_id or "<empty>",
            response.worker_count,
            response.client_count,
        )
        return response

    def request_gemv(
        self,
        *,
        client_name: str,
        request_id: str,
        vector_values: list[float],
        size: str = WORKLOAD_SIZE_LARGE,
        iteration_count: int = 1,
        object_id: str = "",
        stream_id: str = "",
        timestamp_ms: int | None = None,
        on_task_accepted: Callable[[str], None] | None = None,
    ) -> RequestResult:
        """Send one fixed-matrix-vector request and return the decoded output vector."""

        normalized_size = normalize_request_size(size, default=WORKLOAD_SIZE_LARGE)
        expected_vector_length = vector_length_for_size(normalized_size)
        if len(vector_values) != expected_vector_length:
            raise ValueError(
                f"gemv vector length {len(vector_values)} does not match requested size "
                f"{normalized_size} ({expected_vector_length})"
            )
        vector_data = pack_float32_values(vector_values)
        return self.request_payload(
            client_name=client_name,
            request_id=request_id,
            method=METHOD_GEMV,
            size=normalized_size,
            request_payload=GemvRequestPayload(
                vector_length=len(vector_values),
                vector_data=vector_data,
            ),
            iteration_count=iteration_count,
            object_id=object_id,
            stream_id=stream_id,
            timestamp_ms=timestamp_ms,
            decode_output_as_float32=True,
            on_task_accepted=on_task_accepted,
        )

    def request_gemm(
        self,
        *,
        client_name: str,
        request_id: str,
        size: str = WORKLOAD_SIZE_LARGE,
        iteration_count: int = 1,
        object_id: str = "",
        stream_id: str = "",
        timestamp_ms: int | None = None,
        on_task_accepted: Callable[[str], None] | None = None,
    ) -> RequestResult:
        """Send one cuBLAS GEMM request; the client supplies no matrix bytes.

        Both A and B are pre-generated on every compute node from fixed seeds
        keyed by the request size, so the control-plane payload is empty.
        The only large byte flow is the merged C going back from main to the
        client (inline when small; artifact when it exceeds max_message_size).
        """
        normalized_size = (size or "large").strip().lower() or "large"
        if normalized_size not in {"small", "mid", "large"}:
            raise ValueError("gemm size must be 'small', 'mid', or 'large'")
        resolved_object_id = object_id or f"gemm/{normalized_size}"
        return self.request_payload(
            client_name=client_name,
            request_id=request_id,
            method=METHOD_GEMM,
            size=normalized_size,
            request_payload=GemmRequestPayload(),
            iteration_count=iteration_count,
            object_id=resolved_object_id,
            stream_id=stream_id,
            timestamp_ms=timestamp_ms,
            decode_output_as_float32=True,
            on_task_accepted=on_task_accepted,
        )

    def request_conv2d(
        self,
        *,
        client_name: str,
        request_id: str,
        size: str = WORKLOAD_SIZE_LARGE,
        object_id: str | None = None,
        local_download_name: str | None = None,
        iteration_count: int = 1,
        stream_id: str = "",
        timestamp_ms: int | None = None,
        client_response_mode: int = 0,
        stats_max_samples: int = CONV2D_DEFAULT_STATS_MAX_SAMPLES,
        on_task_accepted: Callable[[str], None] | None = None,
    ) -> RequestResult:
        """Send one conv2d request that resolves to a named workload."""

        normalized_size = normalize_request_size(size, default=WORKLOAD_SIZE_LARGE)
        if normalized_size not in {"small", "mid", "large"}:
            raise ValueError("conv2d size must be 'small', 'mid', or 'large'")
        resolved_object_id = object_id or f"conv2d/{normalized_size}"

        spec = build_conv2d_spec(size=normalized_size)
        weight_bytes = generate_default_weight_bytes(size=normalized_size)
        weight_checksum = hashlib.sha256(weight_bytes).hexdigest()
        payload = Conv2dRequestPayload(
            tensor_h=spec.h,
            tensor_w=spec.w,
            channels_in=spec.c_in,
            channels_out=spec.c_out,
            kernel_size=spec.k,
            padding=spec.pad,
            stride=spec.stride,
            client_response_mode=client_response_mode,
            stats_max_samples=stats_max_samples,
            upload_size_bytes=len(weight_bytes),
            upload_checksum=weight_checksum,
        )
        coordinator = _Conv2dUploadCoordinator(
            weight_bytes=weight_bytes,
            weight_checksum=weight_checksum,
            content_type="application/x-superweb-conv2d-weight",
            timeout=self.artifact_timeout,
        )
        return self.request_payload(
            client_name=client_name,
            request_id=request_id,
            method=METHOD_CONV2D,
            size=normalized_size,
            request_payload=payload,
            iteration_count=iteration_count,
            object_id=resolved_object_id,
            preferred_output_name=local_download_name or "",
            stream_id=stream_id,
            timestamp_ms=timestamp_ms,
            decode_output_as_float32=False,
            on_task_accepted=on_task_accepted,
            data_plane_coordinator=coordinator,
        )

    def request_free_content(
        self,
        *,
        client_name: str,
        request_id: str,
        content: str,
        iteration_count: int = 1,
        object_id: str = "",
        stream_id: str = "",
        timestamp_ms: int | None = None,
        method: str = METHOD_FREE_CONTENT,
        on_task_accepted: Callable[[str], None] | None = None,
    ) -> RequestResult:
        """Send one free-form UTF-8 payload request."""

        payload = content.encode("utf-8")
        return self.request_payload(
            client_name=client_name,
            request_id=request_id,
            method=method,
            size="",
            request_payload=GemvRequestPayload(
                vector_length=len(payload),
                vector_data=payload,
            ),
            iteration_count=iteration_count,
            object_id=object_id,
            stream_id=stream_id,
            timestamp_ms=timestamp_ms,
            decode_output_as_float32=False,
            on_task_accepted=on_task_accepted,
        )

    def request_payload(
        self,
        *,
        client_name: str,
        request_id: str,
        method: str,
        size: str,
        request_payload,
        iteration_count: int = 1,
        object_id: str = "",
        preferred_output_name: str = "",
        stream_id: str = "",
        timestamp_ms: int | None = None,
        decode_output_as_float32: bool,
        on_task_accepted: Callable[[str], None] | None = None,
        data_plane_coordinator: "_Conv2dUploadCoordinator | None" = None,
    ) -> RequestResult:
        """Send one structured request and optionally decode float32 output."""

        self._ensure_alive()
        self.client_name = client_name
        local_request_id = request_id
        request_started_at = time.monotonic()
        logger.info(
            "sending CLIENT_REQUEST method=%s size=%s local_request_id=%s object_id=%s stream_id=%s iteration_count=%s",
            method,
            size or "<empty>",
            local_request_id,
            object_id or "<empty>",
            stream_id or "<empty>",
            iteration_count,
        )
        with self._condition:
            self._awaiting_request_ok_local_ids.append(local_request_id)
            if preferred_output_name:
                self._preferred_output_name_by_local_request_id[local_request_id] = preferred_output_name
        try:
            with self._send_lock:
                send_message(
                    self.sock,
                    build_client_request(
                        client_name=client_name,
                        request_id="",
                        method=method,
                        size=size,
                        vector_data=(request_payload.vector_data if hasattr(request_payload, "vector_data") else b""),
                        object_id=object_id,
                        stream_id=stream_id,
                        timestamp_ms=timestamp_ms,
                        iteration_count=iteration_count,
                        request_payload=request_payload,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("CLIENT_REQUEST send failed local_request_id=%s: %s", local_request_id, exc)
            with self._condition:
                try:
                    self._awaiting_request_ok_local_ids.remove(local_request_id)
                except ValueError:
                    pass
                self._preferred_output_name_by_local_request_id.pop(local_request_id, None)
            raise

        try:
            request_ok = self._wait_for_client_request_ok(local_request_id, timeout=None)
            task_accepted_at = time.monotonic()
            logger.info(
                "CLIENT_REQUEST_OK local_request_id=%s task_id=%s accept_latency_ms=%s",
                local_request_id,
                request_ok.task_id,
                max(0, int(round((task_accepted_at - request_started_at) * 1000))),
            )
            self._mark_task_started(local_request_id, request_ok.task_id)
            if data_plane_coordinator is not None:
                try:
                    data_plane_coordinator.on_request_ok(request_ok)
                except BaseException:
                    logger.exception("data-plane coordinator on_request_ok failed task_id=%s", request_ok.task_id)
                    data_plane_coordinator.close()
                    raise
            if on_task_accepted is not None:
                on_task_accepted(request_ok.task_id)
            try:
                response = self._wait_for(
                    None,
                    self._pop_client_response,
                    "client response",
                )
            except BaseException:
                logger.exception("wait for CLIENT_RESPONSE failed task_id=%s", request_ok.task_id)
                if data_plane_coordinator is not None:
                    data_plane_coordinator.close()
                raise
            task_id = response.task_id or request_ok.task_id
            try:
                if data_plane_coordinator is not None:
                    output_bytes, saved_output_path, artifact_path, artifact_downloaded, artifact_checksum = (
                        data_plane_coordinator.materialize_output(
                            response,
                            self._build_output_path,
                        )
                    )
                else:
                    output_bytes, saved_output_path, artifact_path, artifact_downloaded, artifact_checksum = self._materialize_response_output(response)
            finally:
                if task_id:
                    self._mark_task_finished(task_id)
            response_completed_at = time.monotonic()
        except Exception:  # noqa: BLE001
            with self._condition:
                self._preferred_output_name_by_local_request_id.pop(local_request_id, None)
            raise
        output_values: list[float] = []
        if decode_output_as_float32 and output_bytes:
            output_values = unpack_float32_bytes(output_bytes)
        request_accept_latency_ms = max(0, int(round((task_accepted_at - request_started_at) * 1000)))
        client_elapsed_ms = max(0, int(round((response_completed_at - task_accepted_at) * 1000)))
        transmission_overhead_ms = max(0, client_elapsed_ms - max(0, int(response.elapsed_ms or 0)))
        stats_descriptor = _describe_stats_payload(response)
        timing_descriptor = _describe_response_timing(response)
        logger.info(
            "CLIENT_RESPONSE task_id=%s status=%s method=%s size=%s elapsed_ms=%s client_elapsed_ms=%s transmission_overhead_ms=%s saved_output=%s artifact=%s checksum=%s stats=%s timing=%s",
            response.task_id or "<none>",
            response.status_code,
            response.method or "<none>",
            response.size or "<empty>",
            response.elapsed_ms,
            client_elapsed_ms,
            transmission_overhead_ms,
            saved_output_path if saved_output_path is not None else "<none>",
            artifact_path if artifact_path is not None else "<none>",
            artifact_checksum or "<none>",
            stats_descriptor,
            timing_descriptor,
        )
        return RequestResult(
            response=response,
            output_values=output_values,
            output_bytes=output_bytes,
            accepted_task_id=request_ok.task_id,
            local_request_id=local_request_id,
            request_accept_latency_ms=request_accept_latency_ms,
            client_elapsed_ms=client_elapsed_ms,
            transmission_overhead_ms=transmission_overhead_ms,
            saved_output_path=saved_output_path,
            artifact_path=artifact_path,
            artifact_downloaded=artifact_downloaded,
            artifact_checksum=artifact_checksum,
        )

    def _build_output_path(self, response: ClientResponse, *, content_type: str | None = None) -> Path:
        self.artifact_download_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".txt" if (content_type or "").startswith("text/") or response.method == METHOD_FREE_CONTENT else ".bin"
        preferred_output_name = self._resolve_preferred_output_name(response)
        if preferred_output_name:
            base_name, suffix = self._preferred_output_basename(preferred_output_name, suffix)
        else:
            method_component = _sanitize_output_component(response.method or "response", "response")
            request_component = _sanitize_output_component(self._resolve_local_request_id(response), "response")
            base_name = f"{method_component}-{request_component}"
        candidate = self.artifact_download_dir / f"{base_name}{suffix}"
        if not candidate.exists():
            return candidate
        for index in range(1, 10_000):
            candidate = self.artifact_download_dir / f"{base_name}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        raise RuntimeError("unable to allocate a unique output path in the client download directory")

    def _resolve_local_request_id(self, response: ClientResponse) -> str:
        task_id = response.task_id or response.request_id
        if task_id:
            local_request_id = self._local_request_id_by_task_id.get(task_id)
            if local_request_id:
                return local_request_id
        return response.request_id or response.task_id or "response"

    def _resolve_preferred_output_name(self, response: ClientResponse) -> str:
        task_id = response.task_id or response.request_id
        if not task_id:
            return ""
        return self._preferred_output_name_by_task_id.get(task_id, "")

    def _preferred_output_basename(self, preferred_output_name: str, default_suffix: str) -> tuple[str, str]:
        leaf_name = Path(preferred_output_name).name.strip()
        if not leaf_name:
            return "response", default_suffix
        explicit_suffix = Path(leaf_name).suffix.lower()
        if explicit_suffix in {".bin", ".txt"}:
            base_name = _sanitize_output_component(Path(leaf_name).stem, "response")
            return base_name, explicit_suffix
        return _sanitize_output_component(leaf_name, "response"), default_suffix

    def _materialize_response_output(self, response: ClientResponse) -> tuple[bytes, Path | None, Path | None, bool, str]:
        if response.result_artifact is None:
            output_bytes = response.output_vector
            if not output_bytes:
                return b"", None, None, False, ""
            if response.method == METHOD_FREE_CONTENT:
                return output_bytes, None, None, False, ""
            output_path = self._build_output_path(response)
            output_path.write_bytes(output_bytes)
            return output_bytes, output_path, None, False, ""

        artifact_path = self._build_output_path(
            response,
            content_type=response.result_artifact.content_type,
        )
        _saved_path, checksum = fetch_artifact_to_file(
            response.result_artifact,
            artifact_path,
            timeout=self.artifact_timeout,
        )
        return b"", artifact_path, artifact_path, True, checksum

    def expect_client_response(self) -> ClientResponse:
        """Read one CLIENT_RESPONSE from the socket."""

        self._ensure_alive()
        response = self._wait_for(
            None,
            self._pop_client_response,
            "client response",
        )
        task_id = response.task_id or response.request_id
        if task_id and task_id in self._local_request_id_by_task_id:
            self._mark_task_finished(task_id)
        return response


def resolve_main_node_endpoint(
    *,
    host: str | None,
    port: int | None,
    client_name: str,
    multicast_group: str,
    udp_port: int,
    discovery_timeout: float,
    discovery_attempts: int,
    discovery_retry_delay: float,
    buffer_size: int,
    multicast_ttl: int,
) -> tuple[str, int, str]:
    """Return the explicit or discovered main-node endpoint."""

    if host:
        resolved_port = port if port is not None else DEFAULT_TCP_PORT
        logger.info("main-node endpoint via explicit override %s:%s", host, resolved_port)
        return host, resolved_port, "explicit"

    logger.info(
        "main-node discovery starting client_name=%s group=%s port=%s attempts=%s timeout=%.1fs",
        client_name,
        multicast_group,
        udp_port,
        discovery_attempts,
        discovery_timeout,
    )
    result = discover_main_node(
        client_name,
        multicast_group=multicast_group,
        udp_port=udp_port,
        discovery_timeout=discovery_timeout,
        discovery_attempts=discovery_attempts,
        discovery_retry_delay=discovery_retry_delay,
        buffer_size=buffer_size,
        multicast_ttl=multicast_ttl,
    )
    if not result.success or not result.peer_address:
        logger.error("main-node discovery failed: %s", result.message or "<no detail>")
        raise RuntimeError(result.message or "superweb-cluster main node discovery failed")

    resolved_port = port if port is not None else result.peer_port or DEFAULT_TCP_PORT
    logger.info(
        "main-node discovery succeeded peer=%s:%s source=%s",
        result.peer_address,
        resolved_port,
        result.source,
    )
    return result.peer_address, resolved_port, result.source


def connect_session(
    host: str,
    port: int,
    *,
    timeout: float = DEFAULT_TCP_CONNECT_TIMEOUT,
    artifact_timeout: float = DEFAULT_ARTIFACT_TRANSFER_TIMEOUT,
    artifact_download_dir: Path | None = None,
) -> SuperWebClientSession:
    """Open one TCP session to a superweb-cluster main node."""

    logger.info("opening TCP session to %s:%s timeout=%.1fs", host, port, timeout)
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        logger.error("TCP connect to %s:%s failed: %s", host, port, exc)
        raise
    sock.settimeout(timeout)
    return SuperWebClientSession(
        sock,
        artifact_timeout=artifact_timeout,
        artifact_download_dir=artifact_download_dir,
    )
