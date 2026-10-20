"""Tests for the synced client runtime protocol helpers."""

from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from core.constants import (  # noqa: E402
    METHOD_GEMV,
    METHOD_FREE_CONTENT,
    METHOD_CONV2D,
    SUPERWEB_CLIENT_NAME,
)
from wire.control_plane import ArtifactDescriptor  # noqa: E402
from dataset.gemv import build_request_vector_spec, generate_default_request_vector_bytes  # noqa: E402
from wire.runtime import (  # noqa: E402
    MessageKind,
    GemvRequestPayload,
    Conv2dRequestPayload,
    Conv2dResponsePayload,
    Conv2dResultPayload,
    build_client_info_reply,
    build_client_info_request,
    build_client_request_ok,
    build_heartbeat_ok,
    build_client_join,
    build_client_request,
    build_client_response,
    build_task_accept,
    build_task_assign,
    build_task_fail,
    build_task_result,
    encode_envelope,
    parse_envelope,
    recv_message,
    send_message,
)


class RuntimeProtocolTests(unittest.TestCase):
    def test_heartbeat_ok_round_trip(self) -> None:
        heartbeat_ok = parse_envelope(
            encode_envelope(
                build_heartbeat_ok(
                    "worker-1",
                    123456,
                    123999,
                    active_task_ids=["task-1"],
                )
            )
        )

        self.assertEqual(heartbeat_ok.kind, MessageKind.HEARTBEAT_OK)
        self.assertIsNotNone(heartbeat_ok.heartbeat_ok)
        assert heartbeat_ok.heartbeat_ok is not None
        self.assertEqual(heartbeat_ok.heartbeat_ok.node_name, "worker-1")
        self.assertEqual(heartbeat_ok.heartbeat_ok.active_task_ids, ("task-1",))

    def test_client_join_request_round_trip(self) -> None:
        vector_spec = build_request_vector_spec()
        vector_data = generate_default_request_vector_bytes()

        join = parse_envelope(encode_envelope(build_client_join(SUPERWEB_CLIENT_NAME)))
        request = parse_envelope(
            encode_envelope(
                build_client_request(
                    client_name=SUPERWEB_CLIENT_NAME,
                    request_id="req-1",
                    method=METHOD_GEMV,
                    size="large",
                    vector_data=vector_data,
                    object_id="matrix-A",
                    stream_id="stream-1",
                    vector_length=vector_spec.length,
                    timestamp_ms=1234,
                    iteration_count=9,
                )
            )
        )

        self.assertEqual(join.kind, MessageKind.CLIENT_JOIN)
        self.assertEqual(join.client_join.client_name, SUPERWEB_CLIENT_NAME)
        self.assertEqual(request.kind, MessageKind.CLIENT_REQUEST)
        self.assertEqual(request.client_request.request_id, "req-1")
        self.assertEqual(request.client_request.method, METHOD_GEMV)
        self.assertEqual(request.client_request.size, "large")
        self.assertEqual(request.client_request.vector_length, vector_spec.length)
        self.assertEqual(request.client_request.iteration_count, 9)
        self.assertEqual(request.client_request.vector_data, vector_data)
        self.assertIsInstance(request.client_request.request_payload, GemvRequestPayload)

    def test_client_info_round_trip(self) -> None:
        info_request = parse_envelope(
            encode_envelope(
                build_client_info_request(
                    client_id="client-7",
                    client_name="windows-terminal",
                    timestamp_ms=123456789,
                )
            )
        )
        info_reply = parse_envelope(
            encode_envelope(
                build_client_info_reply(
                    client_id="client-7",
                    request_timestamp_ms=123456789,
                    reply_timestamp_ms=123456999,
                    timeout_ms=5000,
                    has_active_tasks=True,
                    active_task_ids=["gemv-1", "conv2d-2"],
                )
            )
        )

        self.assertEqual(info_request.kind, MessageKind.CLIENT_INFO_REQUEST)
        self.assertEqual(info_request.client_info_request.client_id, "client-7")
        self.assertEqual(info_request.client_info_request.client_name, "windows-terminal")
        self.assertEqual(info_reply.kind, MessageKind.CLIENT_INFO_REPLY)
        self.assertEqual(info_reply.client_info_reply.client_id, "client-7")
        self.assertTrue(info_reply.client_info_reply.has_active_tasks)
        self.assertEqual(info_reply.client_info_reply.active_task_ids, ("gemv-1", "conv2d-2"))

    def test_client_request_ok_round_trip(self) -> None:
        request_ok = parse_envelope(
            encode_envelope(
                build_client_request_ok(
                    client_id="client-7",
                    task_id="conv2d-1",
                    method=METHOD_CONV2D,
                    size="large",
                    object_id="conv2d/large",
                    accepted_timestamp_ms=987654321,
                )
            )
        )

        self.assertEqual(request_ok.kind, MessageKind.CLIENT_REQUEST_OK)
        self.assertEqual(request_ok.client_request_ok.client_id, "client-7")
        self.assertEqual(request_ok.client_request_ok.task_id, "conv2d-1")
        self.assertEqual(request_ok.client_request_ok.method, METHOD_CONV2D)
        self.assertEqual(request_ok.client_request_ok.size, "large")

    def test_spatial_request_response_round_trip(self) -> None:
        request = parse_envelope(
            encode_envelope(
                build_client_request(
                    client_name=SUPERWEB_CLIENT_NAME,
                    request_id="conv-1",
                    method=METHOD_CONV2D,
                    size="small",
                    vector_data=b"",
                    object_id="conv2d/small",
                    iteration_count=2,
                    request_payload=Conv2dRequestPayload(),
                )
            )
        )
        response = parse_envelope(
            encode_envelope(
                build_client_response(
                    request_id="conv-1",
                    status_code=200,
                    method=METHOD_CONV2D,
                    size="small",
                    task_id="conv2d-1",
                    elapsed_ms=4321,
                    response_payload=Conv2dResponsePayload(
                        output_length=1024,
                        output_vector=b"",
                        result_artifact_id="artifact-1",
                    ),
                    result_artifact=ArtifactDescriptor(
                        artifact_id="artifact-1",
                        content_type="application/octet-stream",
                        size_bytes=4096,
                        checksum="abc123",
                        producer_node_id="main-node",
                        transfer_host="127.0.0.1",
                        transfer_port=53000,
                        chunk_size=262144,
                        ready=True,
                    ),
                    iteration_count=2,
                )
            )
        )

        self.assertEqual(request.client_request.method, METHOD_CONV2D)
        self.assertIsInstance(request.client_request.request_payload, Conv2dRequestPayload)
        self.assertEqual(request.client_request.object_id, "conv2d/small")
        self.assertEqual(request.client_request.size, "small")
        self.assertEqual(response.client_response.result_artifact_id, "artifact-1")
        self.assertIsNotNone(response.client_response.result_artifact)
        self.assertEqual(response.client_response.result_artifact.transfer_port, 53000)
        self.assertEqual(response.client_response.task_id, "conv2d-1")
        self.assertEqual(response.client_response.elapsed_ms, 4321)
        self.assertEqual(response.client_response.size, "small")

    def test_client_response_round_trip(self) -> None:
        response = parse_envelope(
            encode_envelope(
                build_client_response(
                    request_id="req-1",
                    status_code=200,
                    method=METHOD_GEMV,
                    size="large",
                    task_id="gemv-1",
                    elapsed_ms=120,
                    output_vector=b"\x00\x00\x80?",
                    output_length=1,
                    worker_count=2,
                    client_count=1,
                    client_id="client-1",
                    iteration_count=9,
                )
            )
        )

        self.assertEqual(response.kind, MessageKind.CLIENT_RESPONSE)
        self.assertEqual(response.client_response.request_id, "req-1")
        self.assertEqual(response.client_response.size, "large")
        self.assertEqual(response.client_response.output_length, 1)
        self.assertEqual(response.client_response.worker_count, 2)
        self.assertEqual(response.client_response.client_id, "client-1")
        self.assertEqual(response.client_response.iteration_count, 9)
        self.assertEqual(response.client_response.task_id, "gemv-1")
        self.assertEqual(response.client_response.elapsed_ms, 120)

    def test_free_content_request_round_trip(self) -> None:
        payload = "hello from termux".encode("utf-8")
        request = parse_envelope(
            encode_envelope(
                build_client_request(
                    client_name=SUPERWEB_CLIENT_NAME,
                    request_id="text-1",
                    method=METHOD_FREE_CONTENT,
                    size="",
                    vector_data=payload,
                    vector_length=len(payload),
                    iteration_count=3,
                )
            )
        )

        self.assertEqual(request.kind, MessageKind.CLIENT_REQUEST)
        self.assertEqual(request.client_request.method, METHOD_FREE_CONTENT)
        self.assertEqual(request.client_request.vector_length, len(payload))
        self.assertEqual(request.client_request.iteration_count, 3)
        self.assertEqual(request.client_request.vector_data, payload)

    def test_task_messages_round_trip(self) -> None:
        vector_data = generate_default_request_vector_bytes()
        task_assign = parse_envelope(
            encode_envelope(
                build_task_assign(
                    request_id="req-1",
                    node_id="worker-1",
                    task_id="task-1",
                    method=METHOD_GEMV,
                    size="large",
                    row_start=128,
                    row_end=256,
                    vector_data=vector_data,
                    object_id="matrix-A",
                    stream_id="stream-1",
                    vector_length=32_768,
                    timestamp_ms=1234,
                    iteration_count=12,
                )
            )
        )
        task_accept = parse_envelope(
            encode_envelope(
                build_task_accept(
                    request_id="req-1",
                    node_id="worker-1",
                    task_id="task-1",
                    status_code=202,
                    timestamp_ms=1235,
                )
            )
        )
        task_fail = parse_envelope(
            encode_envelope(
                build_task_fail(
                    request_id="req-1",
                    node_id="worker-1",
                    task_id="task-1",
                    status_code=500,
                    error_message="kernel launch failed",
                    timestamp_ms=1236,
                )
            )
        )
        task_result = parse_envelope(
            encode_envelope(
                build_task_result(
                    request_id="req-1",
                    node_id="worker-1",
                    task_id="task-1",
                    status_code=200,
                    row_start=128,
                    row_end=256,
                    output_vector=b"\x00\x00\x80?\x00\x00\x00@",
                    output_length=2,
                    timestamp_ms=1237,
                    iteration_count=12,
                )
            )
        )

        self.assertEqual(task_assign.kind, MessageKind.TASK_ASSIGN)
        self.assertEqual(task_assign.task_assign.size, "large")
        self.assertEqual(task_assign.task_assign.row_start, 128)
        self.assertEqual(task_assign.task_assign.row_end, 256)
        self.assertEqual(task_assign.task_assign.iteration_count, 12)
        self.assertEqual(task_assign.task_assign.vector_data, vector_data)
        self.assertEqual(task_accept.kind, MessageKind.TASK_ACCEPT)
        self.assertEqual(task_accept.task_accept.status_code, 202)
        self.assertEqual(task_fail.kind, MessageKind.TASK_FAIL)
        self.assertEqual(task_fail.task_fail.error_message, "kernel launch failed")
        self.assertEqual(task_result.kind, MessageKind.TASK_RESULT)
        self.assertEqual(task_result.task_result.output_length, 2)
        self.assertEqual(task_result.task_result.iteration_count, 12)

        conv2d_result = parse_envelope(
            encode_envelope(
                build_task_result(
                    request_id="conv-1",
                    node_id="worker-2",
                    task_id="conv-1:worker-2",
                    status_code=200,
                    iteration_count=3,
                    result_payload=Conv2dResultPayload(
                        start_oc=8,
                        end_oc=16,
                        output_h=32,
                        output_w=32,
                        output_length=8192,
                        output_vector=b"",
                        result_artifact_id="artifact-2",
                    ),
                    result_artifact=ArtifactDescriptor(
                        artifact_id="artifact-2",
                        content_type="application/octet-stream",
                        size_bytes=32768,
                        checksum="def456",
                        producer_node_id="worker-2",
                        transfer_host="10.0.0.2",
                        transfer_port=54000,
                        chunk_size=262144,
                        ready=True,
                    ),
                )
            )
        )
        self.assertEqual(conv2d_result.task_result.start_oc, 8)
        self.assertEqual(conv2d_result.task_result.end_oc, 16)
        self.assertEqual(conv2d_result.task_result.result_artifact_id, "artifact-2")
        self.assertEqual(conv2d_result.task_result.result_artifact.transfer_host, "10.0.0.2")

    def test_framed_send_receive_round_trip(self) -> None:
        left, right = socket.socketpair()
        try:
            left.settimeout(1.0)
            right.settimeout(1.0)

            send_message(
                left,
                build_client_request(
                    client_name=SUPERWEB_CLIENT_NAME,
                    request_id="req-1",
                    method=METHOD_GEMV,
                    size="large",
                    vector_data=generate_default_request_vector_bytes(),
                    iteration_count=4,
                ),
            )
            received = recv_message(right, max_size=4 * 1024 * 1024)

            self.assertIsNotNone(received)
            assert received is not None
            self.assertEqual(received.kind, MessageKind.CLIENT_REQUEST)
            self.assertEqual(received.client_request.vector_length, 32_768)
            self.assertEqual(received.client_request.iteration_count, 4)
        finally:
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
