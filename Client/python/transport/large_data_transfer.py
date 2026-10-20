"""Client-side fetch helpers for the artifact TCP data plane."""

from __future__ import annotations

import hashlib
import logging
import os
import socket
from pathlib import Path

from wire.control_plane import ArtifactDescriptor
from wire.data_plane import (
    CHUNK_HEADER,
    END_HEADER,
    ERROR_HEADER,
    INIT_HEADER,
    decode_chunk,
    decode_end,
    decode_error,
    decode_init,
    encode_chunk,
    encode_deliver,
    encode_download_request,
    encode_end,
)

DEFAULT_UPLOAD_CHUNK_SIZE = 512 * 1024


logger = logging.getLogger(__name__)


def _recv_exactly(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("peer closed data-plane socket mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_init_payload(sock: socket.socket, init_header: bytes) -> tuple[int, int, str, str]:
    _, _, _, size_bytes, chunk_size, checksum_len, content_type_len = INIT_HEADER.unpack(init_header)
    payload = _recv_exactly(sock, checksum_len + content_type_len)
    _, _, checksum, content_type = decode_init(init_header, payload)
    return size_bytes, chunk_size, checksum, content_type


def open_data_plane_socket(host: str, port: int, *, timeout: float) -> socket.socket:
    """Open one TCP data-plane socket to the main node.

    Use this right after receiving CLIENT_REQUEST_OK so the socket is already
    warmed up while the main node kicks off compute. The caller drives the
    frames (DELIVER for upload, REQUEST for download) and closes the socket
    once the exchange finishes.
    """

    logger.debug("opening data-plane socket to %s:%s timeout=%.1fs", host, port, timeout)
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    return sock


def deliver_bytes(
    sock: socket.socket,
    *,
    upload_id: str,
    payload: bytes,
    checksum: str = "",
    content_type: str = "application/octet-stream",
    chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
) -> None:
    """Push one in-memory payload to the main node over an open data socket.

    Sends DELIVER + CHUNK* + END in order. The caller must have already opened
    ``sock`` with ``open_data_plane_socket`` and must keep it open for any
    follow-up REQUEST frame the download path needs.
    """

    if not checksum:
        checksum = hashlib.sha256(payload).hexdigest()
    logger.debug(
        "data-plane DELIVER upload_id=%s size=%s chunk_size=%s content_type=%s",
        upload_id,
        len(payload),
        chunk_size,
        content_type,
    )
    sock.sendall(
        encode_deliver(
            upload_id=upload_id,
            size_bytes=len(payload),
            checksum=checksum,
            content_type=content_type,
        )
    )
    if chunk_size <= 0:
        chunk_size = DEFAULT_UPLOAD_CHUNK_SIZE
    offset = 0
    total = len(payload)
    chunks_sent = 0
    while offset < total:
        end = min(offset + chunk_size, total)
        sock.sendall(encode_chunk(offset=offset, data=payload[offset:end]))
        offset = end
        chunks_sent += 1
    sock.sendall(encode_end(size_bytes=total))
    logger.debug("data-plane DELIVER finished upload_id=%s chunks=%s", upload_id, chunks_sent)


def fetch_download_over_socket(
    sock: socket.socket,
    *,
    download_id: str,
    destination_path: Path,
) -> tuple[Path, str]:
    """Pull one pre-registered download over an already-open data socket.

    Sends REQUEST with ``download_id`` and then reads INIT + CHUNK* + END from
    the same socket. Use this after a prior DELIVER exchange on the same
    connection so the main node can deliver the compute result without the
    cost of opening a second socket.
    """

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination_path.with_suffix(destination_path.suffix + ".tmp")
    logger.debug(
        "data-plane REQUEST download_id=%s destination=%s",
        download_id,
        destination_path,
    )
    sock.sendall(encode_download_request(download_id))
    digest = hashlib.sha256()
    bytes_written = 0
    server_checksum = ""

    header = _recv_exactly(sock, 6)
    message_type = header[5]
    if message_type == 2:
        rest = _recv_exactly(sock, INIT_HEADER.size - 6)
        init_header = header + rest
        _size_bytes, _chunk_size, server_checksum, _content_type = _read_init_payload(sock, init_header)
    elif message_type == 5:
        rest = _recv_exactly(sock, ERROR_HEADER.size - 6)
        error_header = header + rest
        _, _, _, length = ERROR_HEADER.unpack(error_header)
        raise RuntimeError(decode_error(error_header, _recv_exactly(sock, length)))
    else:
        raise ValueError("unexpected first data-plane message type")

    with tmp_path.open("wb") as handle:
        while True:
            header = _recv_exactly(sock, 6)
            message_type = header[5]
            if message_type == 3:
                rest = _recv_exactly(sock, CHUNK_HEADER.size - 6)
                chunk_header = header + rest
                _, _, _, _offset, length = CHUNK_HEADER.unpack(chunk_header)
                _offset, payload = decode_chunk(chunk_header, _recv_exactly(sock, length))
                handle.write(payload)
                digest.update(payload)
                bytes_written += len(payload)
            elif message_type == 4:
                rest = _recv_exactly(sock, END_HEADER.size - 6)
                end_header = header + rest
                total_size = decode_end(end_header)
                if total_size != bytes_written:
                    raise ValueError("artifact end size mismatch")
                break
            elif message_type == 5:
                rest = _recv_exactly(sock, ERROR_HEADER.size - 6)
                error_header = header + rest
                _, _, _, length = ERROR_HEADER.unpack(error_header)
                raise RuntimeError(decode_error(error_header, _recv_exactly(sock, length)))
            else:
                raise ValueError("unexpected data-plane message type")

    checksum = digest.hexdigest()
    if checksum != server_checksum:
        logger.error(
            "data-plane download checksum mismatch download_id=%s local=%s server=%s",
            download_id,
            checksum,
            server_checksum,
        )
        raise ValueError("artifact checksum mismatch")
    os.replace(tmp_path, destination_path)
    logger.info(
        "data-plane download complete download_id=%s bytes=%s destination=%s server_checksum=%s local_checksum=%s",
        download_id,
        bytes_written,
        destination_path,
        server_checksum,
        checksum,
    )
    return destination_path, checksum


def fetch_artifact_to_file(
    descriptor: ArtifactDescriptor,
    destination_path: Path,
    *,
    timeout: float,
) -> tuple[Path, str]:
    """Download one artifact and verify its size and checksum."""

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination_path.with_suffix(destination_path.suffix + ".tmp")
    digest = hashlib.sha256()
    bytes_written = 0
    server_checksum = descriptor.checksum

    logger.info(
        "artifact fetch opening host=%s port=%s artifact_id=%s expected_bytes=%s",
        descriptor.transfer_host,
        descriptor.transfer_port,
        descriptor.artifact_id,
        descriptor.size_bytes,
    )
    with socket.create_connection((descriptor.transfer_host, descriptor.transfer_port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(encode_download_request(descriptor.artifact_id))

        header = _recv_exactly(sock, 6)
        message_type = header[5]
        if message_type == 2:
            rest = _recv_exactly(sock, INIT_HEADER.size - 6)
            init_header = header + rest
            _size_bytes, _chunk_size, server_checksum, _content_type = _read_init_payload(sock, init_header)
        elif message_type == 5:
            rest = _recv_exactly(sock, ERROR_HEADER.size - 6)
            error_header = header + rest
            _, _, _, length = ERROR_HEADER.unpack(error_header)
            raise RuntimeError(decode_error(error_header, _recv_exactly(sock, length)))
        else:
            raise ValueError("unexpected first data-plane message type")

        with tmp_path.open("wb") as handle:
            while True:
                header = _recv_exactly(sock, 6)
                message_type = header[5]
                if message_type == 3:
                    rest = _recv_exactly(sock, CHUNK_HEADER.size - 6)
                    chunk_header = header + rest
                    _, _, _, _offset, length = CHUNK_HEADER.unpack(chunk_header)
                    _offset, payload = decode_chunk(chunk_header, _recv_exactly(sock, length))
                    handle.write(payload)
                    digest.update(payload)
                    bytes_written += len(payload)
                elif message_type == 4:
                    rest = _recv_exactly(sock, END_HEADER.size - 6)
                    end_header = header + rest
                    total_size = decode_end(end_header)
                    if total_size != bytes_written:
                        raise ValueError("artifact end size mismatch")
                    break
                elif message_type == 5:
                    rest = _recv_exactly(sock, ERROR_HEADER.size - 6)
                    error_header = header + rest
                    _, _, _, length = ERROR_HEADER.unpack(error_header)
                    raise RuntimeError(decode_error(error_header, _recv_exactly(sock, length)))
                else:
                    raise ValueError("unexpected data-plane message type")

    checksum = digest.hexdigest()
    if checksum != server_checksum:
        logger.error(
            "artifact checksum mismatch artifact_id=%s local=%s server=%s",
            descriptor.artifact_id,
            checksum,
            server_checksum,
        )
        raise ValueError("artifact checksum mismatch")
    if bytes_written != descriptor.size_bytes:
        logger.error(
            "artifact size mismatch artifact_id=%s bytes_written=%s expected=%s",
            descriptor.artifact_id,
            bytes_written,
            descriptor.size_bytes,
        )
        raise ValueError("artifact size mismatch")
    os.replace(tmp_path, destination_path)
    logger.info(
        "artifact fetch complete artifact_id=%s bytes=%s destination=%s server_checksum=%s local_checksum=%s",
        descriptor.artifact_id,
        bytes_written,
        destination_path,
        server_checksum,
        checksum,
    )
    return destination_path, checksum
