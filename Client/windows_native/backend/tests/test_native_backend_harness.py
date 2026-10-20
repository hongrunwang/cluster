"""Windows-only smoke tests for the WinUI3 native backend service layer.

These tests do not talk to the real main node. Instead they spin up a tiny
mock control plane plus data plane and drive the same C# ``NativeClientBackend``
class that the WinUI3 app uses.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parents[3]
PYTHON_ROOT = CLIENT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from core.constants import METHOD_CONV2D, METHOD_FREE_CONTENT  # noqa: E402
from dataset.conv2d import build_conv2d_spec, generate_default_weight_bytes  # noqa: E402
from wire.control_plane import ArtifactDescriptor  # noqa: E402
from wire.data_plane import (  # noqa: E402
    CHUNK_HEADER,
    DELIVER_HEADER,
    DOWNLOAD_REQUEST_HEADER,
    END_HEADER,
    decode_deliver,
    decode_download_request,
    encode_chunk,
    encode_end,
    encode_init,
)
from superweb_client.wire.runtime import (  # noqa: E402
    ClientResponse,
    Conv2dResponsePayload,
    MessageKind,
    ResponseTiming,
    RuntimeEnvelope,
    WorkerTiming,
    build_client_request_ok,
    build_client_response,
    recv_message,
    send_message,
)


HARNESS_PROJECT = CLIENT_ROOT / "windows_native" / "tests" / "harness" / "NativeBackendHarness.csproj"
HARNESS_EXE = (
    CLIENT_ROOT
    / "windows_native"
    / "tests"
    / "harness"
    / "bin"
    / "Debug"
    / "net8.0-windows10.0.19041.0"
    / "win-x64"
    / "NativeBackendHarness.exe"
)
NATIVE_DLL = CLIENT_ROOT / "windows_native" / "backend" / "build" / "Release" / "superweb_client_backend.dll"
MAX_MESSAGE_SIZE = 4 * 1024 * 1024


def _recv_exactly(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("short read while receiving mock data-plane frame")
        buf.extend(chunk)
    return bytes(buf)


class NativeBackendHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform != "win32":
            raise unittest.SkipTest("The native backend harness only runs on Windows.")
        if shutil.which("dotnet") is None:
            raise unittest.SkipTest("dotnet is required to build the native backend harness.")
        if not NATIVE_DLL.exists():
            raise unittest.SkipTest(f"Missing native backend DLL: {NATIVE_DLL}")

        build = subprocess.run(
            ["dotnet", "build", str(HARNESS_PROJECT)],
            cwd=CLIENT_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if build.returncode != 0:
            raise RuntimeError(
                "Failed to build the native backend harness.\n"
                f"stdout:\n{build.stdout}\n"
                f"stderr:\n{build.stderr}"
            )
        if not HARNESS_EXE.exists():
            raise RuntimeError(f"Harness build succeeded but executable is missing: {HARNESS_EXE}")

    def test_free_content_round_trip_via_harness(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        control_port = listener.getsockname()[1]
        server_errors: list[BaseException] = []

        def _serve() -> None:
            try:
                conn, _addr = listener.accept()
                with conn:
                    conn.settimeout(2.0)
                    join_message = recv_message(conn, max_size=MAX_MESSAGE_SIZE)
                    self.assertIsNotNone(join_message)
                    assert join_message is not None
                    self.assertEqual(join_message.kind, MessageKind.CLIENT_JOIN)

                    send_message(
                        conn,
                        build_client_response(
                            request_id="join",
                            status_code=200,
                            client_id="client-1",
                            worker_count=2,
                            client_count=1,
                        ),
                    )

                    request_message = recv_message(conn, max_size=MAX_MESSAGE_SIZE)
                    self.assertIsNotNone(request_message)
                    assert request_message is not None
                    self.assertEqual(request_message.kind, MessageKind.CLIENT_REQUEST)
                    self.assertEqual(request_message.client_request.method, METHOD_FREE_CONTENT)
                    self.assertEqual(request_message.client_request.vector_data, b"hello mock main")

                    send_message(
                        conn,
                        build_client_request_ok(
                            client_id="client-1",
                            task_id="free-1",
                            method=METHOD_FREE_CONTENT,
                            size="",
                            object_id="",
                            accepted_timestamp_ms=111,
                        ),
                    )
                    send_message(
                        conn,
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
            except BaseException as exc:  # noqa: BLE001
                server_errors.append(exc)
            finally:
                listener.close()

        server_thread = threading.Thread(target=_serve, daemon=True)
        server_thread.start()

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = self._run_harness(
                scenario="free-content",
                port=control_port,
                cwd=Path(temp_dir),
                content="hello mock main",
            )

        server_thread.join(timeout=3.0)
        self.assertFalse(server_thread.is_alive())
        if server_errors:
            raise server_errors[0]

        connection = payload["Connection"]
        summary = payload["Summary"]
        self.assertEqual(connection["ClientId"], "client-1")
        self.assertEqual(summary["StatusCode"], 200)
        self.assertEqual(summary["OutputText"], "superweb overview")
        self.assertFalse(summary["OutputSaved"])
        self.assertIsNone(summary["LocalOutputPath"])
        self.assertEqual(summary["AcceptedSummary"]["TaskId"], "free-1")

    def test_conv2d_full_round_trip_uploads_and_downloads_artifact(self) -> None:
        control_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        control_listener.bind(("127.0.0.1", 0))
        control_listener.listen(1)
        control_port = control_listener.getsockname()[1]

        data_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_listener.bind(("127.0.0.1", 0))
        data_listener.listen(1)
        data_port = data_listener.getsockname()[1]

        spec = build_conv2d_spec(size="small")
        expected_weight_bytes = generate_default_weight_bytes(size="small")
        expected_weight_checksum = hashlib.sha256(expected_weight_bytes).hexdigest()
        result_payload = (b"conv2d-result-" * 64)[:4096]
        result_checksum = hashlib.sha256(result_payload).hexdigest()
        upload_done = threading.Event()
        server_errors: list[BaseException] = []

        def _serve_data_plane() -> None:
            try:
                conn, _addr = data_listener.accept()
                with conn:
                    conn.settimeout(5.0)

                    deliver_header = _recv_exactly(conn, DELIVER_HEADER.size)
                    (
                        _magic,
                        _version,
                        message_type,
                        _size_bytes,
                        upload_id_len,
                        checksum_len,
                        content_type_len,
                    ) = DELIVER_HEADER.unpack(deliver_header)
                    self.assertEqual(message_type, 6)
                    deliver_payload = _recv_exactly(conn, upload_id_len + checksum_len + content_type_len)
                    upload_id, declared_size, checksum, content_type = decode_deliver(deliver_header, deliver_payload)
                    self.assertEqual(upload_id, "upload-1")
                    self.assertEqual(declared_size, len(expected_weight_bytes))
                    self.assertEqual(checksum, expected_weight_checksum)
                    self.assertEqual(content_type, "application/x-superweb-conv2d-weight")

                    received = bytearray()
                    while len(received) < len(expected_weight_bytes):
                        chunk_header = _recv_exactly(conn, CHUNK_HEADER.size)
                        _magic, _version, message_type, offset, length = CHUNK_HEADER.unpack(chunk_header)
                        self.assertEqual(message_type, 3)
                        self.assertEqual(offset, len(received))
                        received.extend(_recv_exactly(conn, length))

                    end_header = _recv_exactly(conn, END_HEADER.size)
                    _magic, _version, message_type, total_size = END_HEADER.unpack(end_header)
                    self.assertEqual(message_type, 4)
                    self.assertEqual(total_size, len(expected_weight_bytes))
                    self.assertEqual(bytes(received), expected_weight_bytes)
                    upload_done.set()

                    request_header = _recv_exactly(conn, DOWNLOAD_REQUEST_HEADER.size)
                    request_payload = _recv_exactly(conn, DOWNLOAD_REQUEST_HEADER.unpack(request_header)[3])
                    download_id = decode_download_request(request_header, request_payload)
                    self.assertEqual(download_id, "download-1")

                    conn.sendall(
                        encode_init(
                            size_bytes=len(result_payload),
                            chunk_size=1024,
                            checksum=result_checksum,
                            content_type="application/octet-stream",
                        )
                    )
                    for offset in range(0, len(result_payload), 1024):
                        conn.sendall(encode_chunk(offset=offset, data=result_payload[offset:offset + 1024]))
                    conn.sendall(encode_end(size_bytes=len(result_payload)))
            except BaseException as exc:  # noqa: BLE001
                server_errors.append(exc)
            finally:
                data_listener.close()

        def _serve_control_plane() -> None:
            try:
                conn, _addr = control_listener.accept()
                with conn:
                    conn.settimeout(5.0)
                    join_message = recv_message(conn, max_size=MAX_MESSAGE_SIZE)
                    self.assertIsNotNone(join_message)
                    assert join_message is not None
                    self.assertEqual(join_message.kind, MessageKind.CLIENT_JOIN)

                    send_message(
                        conn,
                        build_client_response(
                            request_id="join",
                            status_code=200,
                            client_id="client-1",
                            worker_count=1,
                            client_count=1,
                        ),
                    )

                    request_message = recv_message(conn, max_size=MAX_MESSAGE_SIZE)
                    self.assertIsNotNone(request_message)
                    assert request_message is not None
                    self.assertEqual(request_message.kind, MessageKind.CLIENT_REQUEST)
                    self.assertEqual(request_message.client_request.method, METHOD_CONV2D)
                    self.assertEqual(request_message.client_request.size, "small")

                    payload = request_message.client_request.conv2d_payload
                    assert payload is not None
                    self.assertEqual(payload.tensor_h, spec.h)
                    self.assertEqual(payload.tensor_w, spec.w)
                    self.assertEqual(payload.channels_in, spec.c_in)
                    self.assertEqual(payload.channels_out, spec.c_out)
                    self.assertEqual(payload.kernel_size, spec.k)
                    self.assertEqual(payload.padding, spec.pad)
                    self.assertEqual(payload.stride, spec.stride)
                    self.assertEqual(payload.client_response_mode, 0)
                    self.assertEqual(payload.stats_max_samples, 0)
                    self.assertEqual(payload.upload_size_bytes, len(expected_weight_bytes))
                    self.assertEqual(payload.upload_checksum, expected_weight_checksum)

                    send_message(
                        conn,
                        build_client_request_ok(
                            client_id="client-1",
                            task_id="conv2d-1",
                            method=METHOD_CONV2D,
                            size="small",
                            object_id="conv2d/small",
                            accepted_timestamp_ms=222,
                            upload_id="upload-1",
                            download_id="download-1",
                            data_endpoint_host="127.0.0.1",
                            data_endpoint_port=data_port,
                        ),
                    )

                    self.assertTrue(upload_done.wait(5.0))
                    send_message(
                        conn,
                        RuntimeEnvelope(
                            kind=MessageKind.CLIENT_RESPONSE,
                            client_response=ClientResponse(
                                request_id="conv2d-1",
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
                                task_id="conv2d-1",
                                elapsed_ms=1234,
                                response_payload=Conv2dResponsePayload(
                                    output_length=len(result_payload) // 4,
                                    output_vector=b"",
                                    result_artifact_id="artifact-1",
                                ),
                                result_artifact=ArtifactDescriptor(
                                    artifact_id="artifact-1",
                                    content_type="application/octet-stream",
                                    size_bytes=len(result_payload),
                                    checksum=result_checksum,
                                    producer_node_id="main-node",
                                    transfer_host="127.0.0.1",
                                    transfer_port=data_port,
                                    chunk_size=1024,
                                    ready=True,
                                ),
                                timing=ResponseTiming(
                                    dispatch_ms=20,
                                    task_window_ms=1200,
                                    aggregate_ms=14,
                                    workers=(
                                        WorkerTiming(
                                            node_id="node-a",
                                            task_id="conv2d-1",
                                            slice="oc[0:64)",
                                            wall_ms=1188,
                                            artifact_fetch_ms=7,
                                            computation_ms=1170,
                                            peripheral_ms=11,
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    )
            except BaseException as exc:  # noqa: BLE001
                server_errors.append(exc)
            finally:
                control_listener.close()

        data_thread = threading.Thread(target=_serve_data_plane, daemon=True)
        control_thread = threading.Thread(target=_serve_control_plane, daemon=True)
        data_thread.start()
        control_thread.start()

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = self._run_harness(
                scenario="conv2d-full",
                port=control_port,
                cwd=Path(temp_dir),
                size="small",
                download_name="winui-conv2d",
            )
            summary = payload["Summary"]
            self.assertIsNotNone(summary["LocalOutputPath"], json.dumps(summary, indent=2))
            local_output_path = Path(temp_dir) / summary["LocalOutputPath"]
            self.assertTrue(local_output_path.exists())
            self.assertEqual(local_output_path.name, "winui-conv2d.bin")
            self.assertEqual(local_output_path.read_bytes(), result_payload)

        control_thread.join(timeout=5.0)
        data_thread.join(timeout=5.0)
        self.assertFalse(control_thread.is_alive())
        self.assertFalse(data_thread.is_alive())
        if server_errors:
            raise server_errors[0]

        summary = payload["Summary"]
        self.assertEqual(summary["StatusCode"], 200)
        self.assertTrue(summary["OutputSaved"])
        self.assertEqual(summary["OutputChecksum"], result_checksum)
        self.assertEqual(summary["AcceptedSummary"]["UploadId"], "upload-1")
        self.assertEqual(summary["AcceptedSummary"]["DownloadId"], "download-1")
        self.assertEqual(summary["Timing"]["DispatchMs"], 20)
        self.assertEqual(summary["Timing"]["Workers"][0]["NodeId"], "node-a")

    def test_conv2d_stats_round_trip_reports_stats_without_download(self) -> None:
        control_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        control_listener.bind(("127.0.0.1", 0))
        control_listener.listen(1)
        control_port = control_listener.getsockname()[1]

        data_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_listener.bind(("127.0.0.1", 0))
        data_listener.listen(1)
        data_port = data_listener.getsockname()[1]

        expected_weight_bytes = generate_default_weight_bytes(size="small")
        expected_weight_checksum = hashlib.sha256(expected_weight_bytes).hexdigest()
        upload_done = threading.Event()
        server_errors: list[BaseException] = []

        def _serve_data_plane() -> None:
            try:
                conn, _addr = data_listener.accept()
                with conn:
                    conn.settimeout(5.0)
                    deliver_header = _recv_exactly(conn, DELIVER_HEADER.size)
                    (
                        _magic,
                        _version,
                        message_type,
                        _size_bytes,
                        upload_id_len,
                        checksum_len,
                        content_type_len,
                    ) = DELIVER_HEADER.unpack(deliver_header)
                    self.assertEqual(message_type, 6)
                    deliver_payload = _recv_exactly(conn, upload_id_len + checksum_len + content_type_len)
                    upload_id, declared_size, checksum, _content_type = decode_deliver(deliver_header, deliver_payload)
                    self.assertEqual(upload_id, "upload-2")
                    self.assertEqual(declared_size, len(expected_weight_bytes))
                    self.assertEqual(checksum, expected_weight_checksum)

                    received = bytearray()
                    while len(received) < len(expected_weight_bytes):
                        chunk_header = _recv_exactly(conn, CHUNK_HEADER.size)
                        _magic, _version, message_type, offset, length = CHUNK_HEADER.unpack(chunk_header)
                        self.assertEqual(message_type, 3)
                        self.assertEqual(offset, len(received))
                        received.extend(_recv_exactly(conn, length))

                    end_header = _recv_exactly(conn, END_HEADER.size)
                    _magic, _version, message_type, total_size = END_HEADER.unpack(end_header)
                    self.assertEqual(message_type, 4)
                    self.assertEqual(total_size, len(expected_weight_bytes))
                    self.assertEqual(bytes(received), expected_weight_bytes)
                    upload_done.set()
            except BaseException as exc:  # noqa: BLE001
                server_errors.append(exc)
            finally:
                data_listener.close()

        def _serve_control_plane() -> None:
            try:
                conn, _addr = control_listener.accept()
                with conn:
                    conn.settimeout(5.0)
                    join_message = recv_message(conn, max_size=MAX_MESSAGE_SIZE)
                    self.assertIsNotNone(join_message)
                    send_message(
                        conn,
                        build_client_response(
                            request_id="join",
                            status_code=200,
                            client_id="client-1",
                            worker_count=1,
                            client_count=1,
                        ),
                    )

                    request_message = recv_message(conn, max_size=MAX_MESSAGE_SIZE)
                    self.assertIsNotNone(request_message)
                    assert request_message is not None
                    self.assertEqual(request_message.kind, MessageKind.CLIENT_REQUEST)
                    self.assertEqual(request_message.client_request.method, METHOD_CONV2D)
                    payload = request_message.client_request.conv2d_payload
                    assert payload is not None
                    self.assertEqual(payload.client_response_mode, 1)
                    self.assertEqual(payload.stats_max_samples, 3)
                    self.assertEqual(payload.upload_size_bytes, len(expected_weight_bytes))
                    self.assertEqual(payload.upload_checksum, expected_weight_checksum)

                    send_message(
                        conn,
                        build_client_request_ok(
                            client_id="client-1",
                            task_id="conv2d-2",
                            method=METHOD_CONV2D,
                            size="small",
                            object_id="conv2d/small",
                            accepted_timestamp_ms=333,
                            upload_id="upload-2",
                            download_id="",
                            data_endpoint_host="127.0.0.1",
                            data_endpoint_port=data_port,
                        ),
                    )

                    self.assertTrue(upload_done.wait(5.0))
                    send_message(
                        conn,
                        RuntimeEnvelope(
                            kind=MessageKind.CLIENT_RESPONSE,
                            client_response=ClientResponse(
                                request_id="conv2d-2",
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
                                task_id="conv2d-2",
                                elapsed_ms=222,
                                response_payload=Conv2dResponsePayload(
                                    output_length=0,
                                    output_vector=b"",
                                    result_artifact_id="",
                                    stats_element_count=32,
                                    stats_sum=12.5,
                                    stats_sum_squares=9.75,
                                    stats_samples=(0.25, 0.5, 0.75),
                                ),
                                timing=ResponseTiming(
                                    dispatch_ms=9,
                                    task_window_ms=200,
                                    aggregate_ms=3,
                                    workers=(
                                        WorkerTiming(
                                            node_id="node-b",
                                            task_id="conv2d-2",
                                            slice="stats",
                                            wall_ms=197,
                                            computation_ms=190,
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    )
            except BaseException as exc:  # noqa: BLE001
                server_errors.append(exc)
            finally:
                control_listener.close()

        data_thread = threading.Thread(target=_serve_data_plane, daemon=True)
        control_thread = threading.Thread(target=_serve_control_plane, daemon=True)
        data_thread.start()
        control_thread.start()

        with tempfile.TemporaryDirectory() as temp_dir:
            payload = self._run_harness(
                scenario="conv2d-stats",
                port=control_port,
                cwd=Path(temp_dir),
                size="small",
                stats_max_samples="3",
            )

        control_thread.join(timeout=5.0)
        data_thread.join(timeout=5.0)
        self.assertFalse(control_thread.is_alive())
        self.assertFalse(data_thread.is_alive())
        if server_errors:
            raise server_errors[0]

        summary = payload["Summary"]
        self.assertEqual(summary["StatusCode"], 200)
        self.assertFalse(summary["OutputSaved"])
        self.assertIsNone(summary["LocalOutputPath"])
        self.assertEqual(summary["AcceptedSummary"]["UploadId"], "upload-2")
        self.assertEqual(summary["AcceptedSummary"]["DownloadId"], "")
        self.assertEqual(summary["Conv2dStats"]["StatsElementCount"], 32)
        self.assertEqual(summary["Conv2dStats"]["StatsSamples"], [0.25, 0.5, 0.75])
        self.assertIsNotNone(summary["Timing"], json.dumps(summary, indent=2))
        self.assertEqual(summary["Timing"]["Workers"][0]["NodeId"], "node-b")

    def _run_harness(self, *, scenario: str, port: int, cwd: Path, **options: str) -> dict[str, object]:
        command = [str(HARNESS_EXE), "--scenario", scenario, "--host", "127.0.0.1", "--port", str(port)]
        for key, value in options.items():
            option_name = "--" + key.replace("_", "-")
            command.extend([option_name, value])

        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Harness failed for scenario {scenario}.\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        return json.loads(result.stdout)


if __name__ == "__main__":
    unittest.main()
