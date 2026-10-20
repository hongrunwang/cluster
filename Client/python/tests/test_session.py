"""Tests for client session persistence helpers."""

from __future__ import annotations

import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from core.constants import (  # noqa: E402
    DEFAULT_DOWNLOAD_DIR,
    METHOD_FREE_CONTENT,
    METHOD_GEMV,
    METHOD_CONV2D,
)
from session.session import SuperWebClientSession  # noqa: E402
from wire.control_plane import ArtifactDescriptor  # noqa: E402
from wire.runtime import (  # noqa: E402
    ClientResponse,
    Conv2dRequestPayload,
    MessageKind,
    build_client_info_reply,
    build_client_request_ok,
    build_client_response,
    build_heartbeat,
    recv_message,
    send_message,
)


class SessionPersistenceTests(unittest.TestCase):
    def test_default_download_dir_is_client_download(self) -> None:
        left, right = socket.socketpair()
        try:
            session = SuperWebClientSession(left)
            self.assertEqual(session.artifact_download_dir, DEFAULT_DOWNLOAD_DIR)
        finally:
            left.close()
            right.close()

    def test_materialize_inband_output_writes_to_download_dir(self) -> None:
        left, right = socket.socketpair()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                session = SuperWebClientSession(left, artifact_download_dir=Path(temp_dir))
                response = ClientResponse(
                    request_id="fixed/req:1",
                    method=METHOD_GEMV,
                    size="small",
                    object_id="",
                    stream_id="",
                    timestamp_ms=0,
                    status_code=200,
                    error_message="",
                    worker_count=1,
                    client_count=1,
                    client_id="client-1",
                    iteration_count=1,
                    output_length=1,
                    output_vector=b"\x00\x00\x80?",
                )

                output_bytes, saved_output_path, artifact_path, artifact_downloaded, checksum = session._materialize_response_output(response)

                self.assertEqual(output_bytes, b"\x00\x00\x80?")
                self.assertIsNotNone(saved_output_path)
                self.assertIsNone(artifact_path)
                self.assertFalse(artifact_downloaded)
                self.assertEqual(checksum, "")
                assert saved_output_path is not None
                self.assertEqual(saved_output_path.read_bytes(), b"\x00\x00\x80?")
                self.assertEqual(saved_output_path.parent, Path(temp_dir))
                self.assertNotIn("/", saved_output_path.name)
                self.assertNotIn(":", saved_output_path.name)
        finally:
            left.close()
            right.close()

    def test_materialize_free_content_output_keeps_text_in_memory_only(self) -> None:
        left, right = socket.socketpair()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                session = SuperWebClientSession(left, artifact_download_dir=Path(temp_dir))
                response = ClientResponse(
                    request_id="free-1",
                    method=METHOD_FREE_CONTENT,
                    size="",
                    object_id="",
                    stream_id="",
                    timestamp_ms=0,
                    status_code=200,
                    error_message="",
                    worker_count=2,
                    client_count=1,
                    client_id="client-1",
                    iteration_count=1,
                    output_length=24,
                    output_vector=b"superweb system overview",
                )

                output_bytes, saved_output_path, artifact_path, artifact_downloaded, checksum = session._materialize_response_output(response)

                self.assertEqual(output_bytes, b"superweb system overview")
                self.assertIsNone(saved_output_path)
                self.assertIsNone(artifact_path)
                self.assertFalse(artifact_downloaded)
                self.assertEqual(checksum, "")
                self.assertEqual(list(Path(temp_dir).iterdir()), [])
        finally:
            left.close()
            right.close()

    def test_materialize_artifact_output_fetches_into_download_dir(self) -> None:
        left, right = socket.socketpair()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                session = SuperWebClientSession(left, artifact_download_dir=Path(temp_dir))
                response = ClientResponse(
                    request_id="conv-1",
                    method=METHOD_CONV2D,
                    size="small",
                    object_id="conv2d/small",
                    stream_id="",
                    timestamp_ms=0,
                    status_code=200,
                    error_message="",
                    worker_count=2,
                    client_count=1,
                    client_id="client-1",
                    iteration_count=1,
                    output_length=2,
                    result_artifact_id="artifact-1",
                    result_artifact=ArtifactDescriptor(
                        artifact_id="artifact-1",
                        content_type="application/octet-stream",
                        size_bytes=8,
                        checksum="checksum",
                        producer_node_id="main-node",
                        transfer_host="127.0.0.1",
                        transfer_port=53000,
                        chunk_size=262144,
                        ready=True,
                    ),
                )

                with patch("session.session.fetch_artifact_to_file") as fetch_mock:
                    def _write_artifact(_, destination_path: Path, *, timeout: float) -> tuple[Path, str]:
                        destination_path.write_bytes(b"artifact")
                        return destination_path, "mock-checksum"

                    fetch_mock.side_effect = _write_artifact
                    output_bytes, saved_output_path, artifact_path, artifact_downloaded, checksum = session._materialize_response_output(response)

                self.assertEqual(output_bytes, b"")
                self.assertTrue(artifact_downloaded)
                self.assertEqual(checksum, "mock-checksum")
                self.assertIsNotNone(saved_output_path)
                self.assertEqual(saved_output_path, artifact_path)
                assert saved_output_path is not None
                self.assertEqual(saved_output_path.read_bytes(), b"artifact")
                self.assertEqual(saved_output_path.parent, Path(temp_dir))
        finally:
            left.close()
            right.close()

    def test_materialize_artifact_output_uses_preferred_local_download_name(self) -> None:
        left, right = socket.socketpair()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                session = SuperWebClientSession(left, artifact_download_dir=Path(temp_dir))
                session._preferred_output_name_by_task_id["conv2d-5"] = "iloveyou"
                response = ClientResponse(
                    request_id="conv-1",
                    method=METHOD_CONV2D,
                    size="small",
                    object_id="conv2d/small",
                    stream_id="",
                    timestamp_ms=0,
                    status_code=200,
                    error_message="",
                    worker_count=1,
                    client_count=1,
                    client_id="client-1",
                    iteration_count=1,
                    task_id="conv2d-5",
                    output_length=2,
                    result_artifact_id="artifact-1",
                    result_artifact=ArtifactDescriptor(
                        artifact_id="artifact-1",
                        content_type="application/octet-stream",
                        size_bytes=8,
                        checksum="checksum",
                        producer_node_id="main-node",
                        transfer_host="127.0.0.1",
                        transfer_port=53000,
                        chunk_size=262144,
                        ready=True,
                    ),
                )

                with patch("session.session.fetch_artifact_to_file") as fetch_mock:
                    def _write_artifact(_, destination_path: Path, *, timeout: float) -> tuple[Path, str]:
                        destination_path.write_bytes(b"artifact")
                        return destination_path, "mock-checksum"

                    fetch_mock.side_effect = _write_artifact
                    output_bytes, saved_output_path, artifact_path, artifact_downloaded, checksum = session._materialize_response_output(response)

                self.assertEqual(output_bytes, b"")
                self.assertTrue(artifact_downloaded)
                self.assertEqual(checksum, "mock-checksum")
                assert saved_output_path is not None
                self.assertEqual(saved_output_path.name, "iloveyou.bin")
                self.assertEqual(saved_output_path, artifact_path)
                self.assertEqual(saved_output_path.read_bytes(), b"artifact")
        finally:
            left.close()
            right.close()

    def test_request_payload_computes_client_elapsed_and_transmission_overhead(self) -> None:
        left, right = socket.socketpair()
        try:
            session = SuperWebClientSession(left)
            response = ClientResponse(
                request_id="req-1",
                method=METHOD_CONV2D,
                size="small",
                object_id="conv2d/small",
                stream_id="",
                timestamp_ms=0,
                status_code=200,
                error_message="",
                worker_count=1,
                client_count=1,
                client_id="client-1",
                iteration_count=1,
                task_id="conv2d-5",
                elapsed_ms=5400,
            )

            with (
                patch.object(
                    session,
                    "_wait_for_client_request_ok",
                    return_value=mock.Mock(task_id="conv2d-5"),
                ),
                patch.object(session, "_wait_for", return_value=response),
                patch.object(session, "_materialize_response_output", return_value=(b"", None, None, False, "")),
                patch("session.session.time.monotonic", side_effect=[10.0, 10.125, 15.625]),
            ):
                result = session.request_payload(
                    client_name="client-1",
                    request_id="local-1",
                    method=METHOD_CONV2D,
                    size="small",
                    request_payload=Conv2dRequestPayload(
                        tensor_h=256,
                        tensor_w=256,
                        channels_in=32,
                        channels_out=64,
                        kernel_size=3,
                        padding=1,
                        stride=1,
                    ),
                    object_id="conv2d/small",
                    preferred_output_name="iloveyou",
                    decode_output_as_float32=False,
                )

            self.assertEqual(result.request_accept_latency_ms, 125)
            self.assertEqual(result.client_elapsed_ms, 5500)
            self.assertEqual(result.transmission_overhead_ms, 100)
        finally:
            left.close()
            right.close()

    def test_request_free_content_round_trip_keeps_text_and_non_negative_overhead(self) -> None:
        left, right = socket.socketpair()
        try:
            left.settimeout(1.0)
            right.settimeout(1.0)
            session = SuperWebClientSession(left)
            session.client_id = "client-1"
            captured: dict[str, object] = {}

            def _request() -> None:
                captured["result"] = session.request_free_content(
                    client_name="client-1",
                    request_id="local-1",
                    content="hello",
                )

            requester = threading.Thread(target=_request, daemon=True)
            requester.start()

            request_message = recv_message(right, max_size=4 * 1024 * 1024)
            self.assertIsNotNone(request_message)
            assert request_message is not None
            self.assertEqual(request_message.kind, MessageKind.CLIENT_REQUEST)
            self.assertEqual(request_message.client_request.method, METHOD_FREE_CONTENT)
            self.assertEqual(request_message.client_request.vector_data, b"hello")

            send_message(
                right,
                build_client_request_ok(
                    client_id="client-1",
                    task_id="free-1",
                    method=METHOD_FREE_CONTENT,
                    size="",
                    object_id="",
                    accepted_timestamp_ms=100,
                ),
            )
            send_message(
                right,
                build_client_response(
                    request_id="free-1",
                    status_code=200,
                    method=METHOD_FREE_CONTENT,
                    output_vector=b"superweb overview",
                    output_length=len(b"superweb overview"),
                    task_id="free-1",
                    elapsed_ms=12,
                    client_id="client-1",
                ),
            )

            requester.join(timeout=2.0)
            self.assertFalse(requester.is_alive())
            result = captured["result"]
            self.assertEqual(result.output_bytes, b"superweb overview")
            self.assertIsNone(result.saved_output_path)
            self.assertGreaterEqual(result.transmission_overhead_ms, 0)
        finally:
            left.close()
            right.close()

    def test_expect_client_response_replies_to_heartbeat_while_waiting(self) -> None:
        left, right = socket.socketpair()
        try:
            left.settimeout(1.0)
            right.settimeout(1.0)
            session = SuperWebClientSession(left)
            session.client_name = "heartbeat test client"
            captured = {}

            def _wait_for_response() -> None:
                captured["response"] = session.expect_client_response()

            waiter = threading.Thread(target=_wait_for_response, daemon=True)
            waiter.start()

            send_message(right, build_heartbeat("main node", unix_time_ms=123456))
            heartbeat_ok = recv_message(right, max_size=4 * 1024 * 1024)
            self.assertIsNotNone(heartbeat_ok)
            assert heartbeat_ok is not None
            self.assertEqual(heartbeat_ok.heartbeat_ok.node_name, "heartbeat test client")
            self.assertEqual(heartbeat_ok.heartbeat_ok.heartbeat_unix_time_ms, 123456)

            send_message(
                right,
                    build_client_response(
                        request_id="req-1",
                        status_code=200,
                        method="gemv",
                        size="small",
                        output_vector=b"\x00\x00\x80?",
                        output_length=1,
                ),
            )

            waiter.join(timeout=1.0)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(captured["response"].request_id, "req-1")
        finally:
            left.close()
            right.close()

    def test_wait_for_client_info_reply_routes_reply_by_request_timestamp(self) -> None:
        left, right = socket.socketpair()
        try:
            left.settimeout(1.0)
            right.settimeout(1.0)
            session = SuperWebClientSession(left)

            send_message(
                right,
                build_client_info_reply(
                    client_id="client-9",
                    request_timestamp_ms=555,
                    reply_timestamp_ms=777,
                    timeout_ms=5000,
                    has_active_tasks=True,
                    active_task_ids=["gemv-1"],
                ),
            )

            reply = session._wait_for_client_info_reply(555, timeout=1.0)
            self.assertEqual(reply.client_id, "client-9")
            self.assertEqual(reply.timeout_ms, 5000)
            self.assertEqual(reply.active_task_ids, ("gemv-1",))
        finally:
            left.close()
            right.close()

    def test_join_does_not_poll_for_info_before_any_request(self) -> None:
        left, right = socket.socketpair()
        try:
            left.settimeout(1.0)
            right.settimeout(1.0)
            session = SuperWebClientSession(left)
            captured: dict[str, ClientResponse] = {}

            def _join() -> None:
                captured["response"] = session.join("idle poll client")

            joiner = threading.Thread(target=_join, daemon=True)
            joiner.start()

            join_message = recv_message(right, max_size=4 * 1024 * 1024)
            self.assertIsNotNone(join_message)
            assert join_message is not None
            self.assertEqual(join_message.kind, MessageKind.CLIENT_JOIN)
            send_message(
                right,
                build_client_response(
                    request_id="join",
                    status_code=200,
                    size="",
                    client_id="client-1",
                    worker_count=0,
                    client_count=1,
                ),
            )

            joiner.join(timeout=1.0)
            self.assertFalse(joiner.is_alive())
            self.assertEqual(captured["response"].client_id, "client-1")

            right.settimeout(0.2)
            with self.assertRaises(socket.timeout):
                recv_message(right, max_size=4 * 1024 * 1024)
        finally:
            left.close()
            right.close()

    def test_request_info_polling_marks_system_dead_after_consecutive_misses(self) -> None:
        left, right = socket.socketpair()
        try:
            left.settimeout(1.0)
            right.settimeout(1.0)
            session = SuperWebClientSession(left)
            join_response: dict[str, ClientResponse] = {}

            def _join() -> None:
                join_response["response"] = session.join("polling client")

            joiner = threading.Thread(target=_join, daemon=True)
            joiner.start()
            join_message = recv_message(right, max_size=4 * 1024 * 1024)
            self.assertIsNotNone(join_message)
            send_message(
                right,
                build_client_response(
                    request_id="join",
                    status_code=200,
                    size="",
                    client_id="client-1",
                    worker_count=1,
                    client_count=1,
                ),
            )
            joiner.join(timeout=1.0)
            self.assertFalse(joiner.is_alive())
            self.assertEqual(join_response["response"].client_id, "client-1")

            with patch("session.session.DEFAULT_CLIENT_INFO_INTERVAL", 0.05), patch(
                "session.session.DEFAULT_CLIENT_INFO_FAILURE_LIMIT",
                2,
            ):
                failure: dict[str, Exception] = {}

                def _request() -> None:
                    try:
                        session.request_free_content(
                            client_name="polling client",
                            request_id="req-1",
                            content="hello",
                        )
                    except Exception as exc:  # noqa: BLE001
                        failure["exc"] = exc

                requester = threading.Thread(target=_request, daemon=True)
                requester.start()

                request_message = recv_message(right, max_size=4 * 1024 * 1024)
                self.assertIsNotNone(request_message)
                assert request_message is not None
                self.assertEqual(request_message.kind, MessageKind.CLIENT_REQUEST)
                self.assertEqual(request_message.client_request.request_id, "")

                send_message(
                    right,
                    build_client_request_ok(
                        client_id="client-1",
                        task_id="free-1",
                        method="free_content",
                        size="",
                        object_id="",
                        accepted_timestamp_ms=100,
                    ),
                )

                info_request_1 = recv_message(right, max_size=4 * 1024 * 1024)
                info_request_2 = recv_message(right, max_size=4 * 1024 * 1024)
                self.assertIsNotNone(info_request_1)
                self.assertIsNotNone(info_request_2)
                assert info_request_1 is not None
                assert info_request_2 is not None
                self.assertEqual(info_request_1.kind, MessageKind.CLIENT_INFO_REQUEST)
                self.assertEqual(info_request_2.kind, MessageKind.CLIENT_INFO_REQUEST)

                requester.join(timeout=2.0)
                self.assertFalse(requester.is_alive())
                self.assertIn("exc", failure)
                self.assertIsInstance(failure["exc"], ConnectionError)
                self.assertIn("client info polling failed", str(failure["exc"]))
        finally:
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
