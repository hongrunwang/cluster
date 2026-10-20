"""Tests for the client-side DELIVER / push-then-pull data-plane helpers.

Covers:
  - ``encode_deliver`` / ``decode_deliver`` round trip and length-mismatch guard.
  - ``deliver_bytes`` framing over an in-memory socketpair.
  - ``fetch_download_over_socket`` pulling a download once the server emits
    INIT + CHUNK + END.
  - ``_Conv2dUploadCoordinator.on_request_ok`` refusing a main node that fails
    to advertise the upload slot.
"""

from __future__ import annotations

import hashlib
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from session.session import _Conv2dUploadCoordinator  # noqa: E402
from transport.large_data_transfer import (  # noqa: E402
    deliver_bytes,
    fetch_download_over_socket,
)
from wire.data_plane import (  # noqa: E402
    CHUNK_HEADER,
    DELIVER_HEADER,
    DOWNLOAD_REQUEST_HEADER,
    END_HEADER,
    MSG_CHUNK,
    MSG_DELIVER,
    MSG_DOWNLOAD_REQUEST,
    MSG_END,
    decode_deliver,
    encode_deliver,
    encode_chunk,
    encode_end,
    encode_init,
)


def _recv_exactly(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("short read")
        buf.extend(chunk)
    return bytes(buf)


class DeliverCodecTests(unittest.TestCase):
    def test_encode_decode_round_trip(self) -> None:
        raw = encode_deliver(
            upload_id="client-upload-1",
            size_bytes=2048,
            checksum="b" * 64,
            content_type="application/x-superweb-conv2d-weight",
        )
        header = raw[: DELIVER_HEADER.size]
        body = raw[DELIVER_HEADER.size:]
        upload_id, size_bytes, checksum, content_type = decode_deliver(header, body)
        self.assertEqual(upload_id, "client-upload-1")
        self.assertEqual(size_bytes, 2048)
        self.assertEqual(checksum, "b" * 64)
        self.assertEqual(content_type, "application/x-superweb-conv2d-weight")

    def test_decode_rejects_mismatched_payload_length(self) -> None:
        raw = encode_deliver(
            upload_id="u",
            size_bytes=1,
            checksum="",
            content_type="",
        )
        header = raw[: DELIVER_HEADER.size]
        body = raw[DELIVER_HEADER.size:] + b"extra"
        with self.assertRaises(ValueError):
            decode_deliver(header, body)


class DeliverBytesFramingTests(unittest.TestCase):
    def test_deliver_bytes_sends_deliver_then_chunks_then_end(self) -> None:
        left, right = socket.socketpair()
        try:
            payload = b"weight-bytes-" * 200
            expected_checksum = hashlib.sha256(payload).hexdigest()

            sender = threading.Thread(
                target=lambda: deliver_bytes(
                    left,
                    upload_id="u-42",
                    payload=payload,
                    checksum=expected_checksum,
                    content_type="application/x-superweb-conv2d-weight",
                    chunk_size=512,
                ),
                daemon=True,
            )
            sender.start()

            deliver_header = _recv_exactly(right, DELIVER_HEADER.size)
            _, _, message_type, size_bytes, upload_id_len, checksum_len, content_type_len = DELIVER_HEADER.unpack(
                deliver_header
            )
            self.assertEqual(message_type, MSG_DELIVER)
            self.assertEqual(size_bytes, len(payload))
            deliver_payload = _recv_exactly(right, upload_id_len + checksum_len + content_type_len)
            upload_id, _, checksum, content_type = decode_deliver(deliver_header, deliver_payload)
            self.assertEqual(upload_id, "u-42")
            self.assertEqual(checksum, expected_checksum)
            self.assertEqual(content_type, "application/x-superweb-conv2d-weight")

            received = bytearray()
            total_expected_chunks = (len(payload) + 511) // 512
            for _ in range(total_expected_chunks):
                chunk_header = _recv_exactly(right, CHUNK_HEADER.size)
                _, _, chunk_type, offset, length = CHUNK_HEADER.unpack(chunk_header)
                self.assertEqual(chunk_type, MSG_CHUNK)
                self.assertEqual(offset, len(received))
                received.extend(_recv_exactly(right, length))
            self.assertEqual(bytes(received), payload)

            end_header = _recv_exactly(right, END_HEADER.size)
            _, _, end_type, total = END_HEADER.unpack(end_header)
            self.assertEqual(end_type, MSG_END)
            self.assertEqual(total, len(payload))

            sender.join(timeout=2.0)
            self.assertFalse(sender.is_alive())
        finally:
            left.close()
            right.close()

    def test_deliver_bytes_computes_checksum_when_omitted(self) -> None:
        left, right = socket.socketpair()
        try:
            payload = b"checksum-please-compute" * 32
            expected_checksum = hashlib.sha256(payload).hexdigest()

            sender = threading.Thread(
                target=lambda: deliver_bytes(
                    left,
                    upload_id="u-checksum",
                    payload=payload,
                    chunk_size=4096,
                ),
                daemon=True,
            )
            sender.start()

            deliver_header = _recv_exactly(right, DELIVER_HEADER.size)
            _, _, _, _, upload_id_len, checksum_len, content_type_len = DELIVER_HEADER.unpack(deliver_header)
            deliver_payload = _recv_exactly(right, upload_id_len + checksum_len + content_type_len)
            _, _, checksum, _ = decode_deliver(deliver_header, deliver_payload)
            self.assertEqual(checksum, expected_checksum)

            # Drain remaining CHUNK + END so the sender thread exits cleanly.
            chunk_header = _recv_exactly(right, CHUNK_HEADER.size)
            _, _, _, _, length = CHUNK_HEADER.unpack(chunk_header)
            _recv_exactly(right, length)
            _recv_exactly(right, END_HEADER.size)
            sender.join(timeout=2.0)
        finally:
            left.close()
            right.close()


class FetchDownloadOverSocketTests(unittest.TestCase):
    def test_fetch_download_over_socket_reads_init_chunk_end(self) -> None:
        left, right = socket.socketpair()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                payload = b"download-bytes-" * 300
                checksum = hashlib.sha256(payload).hexdigest()
                destination_path = Path(temp_dir) / "output.bin"

                def _serve() -> None:
                    request_header = _recv_exactly(right, DOWNLOAD_REQUEST_HEADER.size)
                    _, _, message_type, length = DOWNLOAD_REQUEST_HEADER.unpack(request_header)
                    assert message_type == MSG_DOWNLOAD_REQUEST
                    _recv_exactly(right, length)
                    right.sendall(
                        encode_init(
                            size_bytes=len(payload),
                            chunk_size=1024,
                            checksum=checksum,
                            content_type="application/octet-stream",
                        )
                    )
                    for offset in range(0, len(payload), 1024):
                        right.sendall(encode_chunk(offset=offset, data=payload[offset:offset + 1024]))
                    right.sendall(encode_end(size_bytes=len(payload)))

                server_thread = threading.Thread(target=_serve, daemon=True)
                server_thread.start()

                returned_path, returned_checksum = fetch_download_over_socket(
                    left,
                    download_id="dl-1",
                    destination_path=destination_path,
                )
                self.assertEqual(returned_path, destination_path)
                self.assertEqual(returned_checksum, checksum)
                self.assertEqual(destination_path.read_bytes(), payload)
                server_thread.join(timeout=2.0)
        finally:
            left.close()
            right.close()


class Conv2dUploadCoordinatorTests(unittest.TestCase):
    def test_on_request_ok_raises_when_upload_id_missing(self) -> None:
        coordinator = _Conv2dUploadCoordinator(
            weight_bytes=b"weights",
            weight_checksum=hashlib.sha256(b"weights").hexdigest(),
            content_type="application/x-superweb-conv2d-weight",
            timeout=1.0,
        )

        class _RequestOk:
            upload_id = ""
            download_id = ""
            data_endpoint_host = "127.0.0.1"
            data_endpoint_port = 40000

        with self.assertRaises(RuntimeError):
            coordinator.on_request_ok(_RequestOk())
        coordinator.close()

    def test_on_request_ok_raises_when_endpoint_missing(self) -> None:
        coordinator = _Conv2dUploadCoordinator(
            weight_bytes=b"weights",
            weight_checksum=hashlib.sha256(b"weights").hexdigest(),
            content_type="application/x-superweb-conv2d-weight",
            timeout=1.0,
        )

        class _RequestOk:
            upload_id = "u-1"
            download_id = "d-1"
            data_endpoint_host = ""
            data_endpoint_port = 0

        with self.assertRaises(RuntimeError):
            coordinator.on_request_ok(_RequestOk())
        coordinator.close()


if __name__ == "__main__":
    unittest.main()
