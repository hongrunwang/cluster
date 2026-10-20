"""Binary framing helpers for large artifact transfer."""

from __future__ import annotations

import struct

MAGIC = b"SWAD"
PROTOCOL_VERSION = 1

MSG_DOWNLOAD_REQUEST = 1
MSG_INIT = 2
MSG_CHUNK = 3
MSG_END = 4
MSG_ERROR = 5
MSG_DELIVER = 6

DOWNLOAD_REQUEST_HEADER = struct.Struct("!4sBBI")
INIT_HEADER = struct.Struct("!4sBBQIII")
CHUNK_HEADER = struct.Struct("!4sBBQI")
END_HEADER = struct.Struct("!4sBBQ")
ERROR_HEADER = struct.Struct("!4sBBI")
DELIVER_HEADER = struct.Struct("!4sBBQIII")


def encode_download_request(artifact_id: str) -> bytes:
    artifact_id_bytes = artifact_id.encode("utf-8")
    return DOWNLOAD_REQUEST_HEADER.pack(MAGIC, PROTOCOL_VERSION, MSG_DOWNLOAD_REQUEST, len(artifact_id_bytes)) + artifact_id_bytes


def decode_download_request(header: bytes, payload: bytes) -> str:
    magic, version, message_type, length = DOWNLOAD_REQUEST_HEADER.unpack(header)
    if magic != MAGIC or version != PROTOCOL_VERSION or message_type != MSG_DOWNLOAD_REQUEST:
        raise ValueError("invalid artifact download-request header")
    if len(payload) != length:
        raise ValueError("artifact download-request payload length mismatch")
    return payload.decode("utf-8")


def encode_init(*, size_bytes: int, chunk_size: int, checksum: str, content_type: str) -> bytes:
    checksum_bytes = checksum.encode("utf-8")
    content_type_bytes = content_type.encode("utf-8")
    header = INIT_HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        MSG_INIT,
        size_bytes,
        chunk_size,
        len(checksum_bytes),
        len(content_type_bytes),
    )
    return header + checksum_bytes + content_type_bytes


def decode_init(header: bytes, payload: bytes) -> tuple[int, int, str, str]:
    magic, version, message_type, size_bytes, chunk_size, checksum_len, content_type_len = INIT_HEADER.unpack(header)
    if magic != MAGIC or version != PROTOCOL_VERSION or message_type != MSG_INIT:
        raise ValueError("invalid artifact init header")
    expected_len = checksum_len + content_type_len
    if len(payload) != expected_len:
        raise ValueError("artifact init payload length mismatch")
    checksum_bytes = payload[:checksum_len]
    content_type_bytes = payload[checksum_len:]
    return size_bytes, chunk_size, checksum_bytes.decode("utf-8"), content_type_bytes.decode("utf-8")


def encode_chunk(*, offset: int, data: bytes) -> bytes:
    return CHUNK_HEADER.pack(MAGIC, PROTOCOL_VERSION, MSG_CHUNK, offset, len(data)) + data


def decode_chunk(header: bytes, payload: bytes) -> tuple[int, bytes]:
    magic, version, message_type, offset, length = CHUNK_HEADER.unpack(header)
    if magic != MAGIC or version != PROTOCOL_VERSION or message_type != MSG_CHUNK:
        raise ValueError("invalid artifact chunk header")
    if len(payload) != length:
        raise ValueError("artifact chunk payload length mismatch")
    return offset, payload


def encode_end(*, size_bytes: int) -> bytes:
    return END_HEADER.pack(MAGIC, PROTOCOL_VERSION, MSG_END, size_bytes)


def decode_end(header: bytes) -> int:
    magic, version, message_type, size_bytes = END_HEADER.unpack(header)
    if magic != MAGIC or version != PROTOCOL_VERSION or message_type != MSG_END:
        raise ValueError("invalid artifact end header")
    return size_bytes


def encode_deliver(
    *,
    upload_id: str,
    size_bytes: int,
    checksum: str,
    content_type: str,
) -> bytes:
    upload_id_bytes = upload_id.encode("utf-8")
    checksum_bytes = checksum.encode("utf-8")
    content_type_bytes = content_type.encode("utf-8")
    header = DELIVER_HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        MSG_DELIVER,
        size_bytes,
        len(upload_id_bytes),
        len(checksum_bytes),
        len(content_type_bytes),
    )
    return header + upload_id_bytes + checksum_bytes + content_type_bytes


def decode_deliver(header: bytes, payload: bytes) -> tuple[str, int, str, str]:
    magic, version, message_type, size_bytes, upload_id_len, checksum_len, content_type_len = DELIVER_HEADER.unpack(header)
    if magic != MAGIC or version != PROTOCOL_VERSION or message_type != MSG_DELIVER:
        raise ValueError("invalid artifact deliver header")
    expected_len = upload_id_len + checksum_len + content_type_len
    if len(payload) != expected_len:
        raise ValueError("artifact deliver payload length mismatch")
    upload_id = payload[:upload_id_len].decode("utf-8")
    checksum = payload[upload_id_len:upload_id_len + checksum_len].decode("utf-8")
    content_type = payload[upload_id_len + checksum_len:].decode("utf-8")
    return upload_id, size_bytes, checksum, content_type


def encode_error(message: str) -> bytes:
    payload = message.encode("utf-8")
    return ERROR_HEADER.pack(MAGIC, PROTOCOL_VERSION, MSG_ERROR, len(payload)) + payload


def decode_error(header: bytes, payload: bytes) -> str:
    magic, version, message_type, length = ERROR_HEADER.unpack(header)
    if magic != MAGIC or version != PROTOCOL_VERSION or message_type != MSG_ERROR:
        raise ValueError("invalid artifact error header")
    if len(payload) != length:
        raise ValueError("artifact error payload length mismatch")
    return payload.decode("utf-8")
