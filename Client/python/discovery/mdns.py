"""Client-side discovery flow helpers."""

from __future__ import annotations

import logging
import socket
import struct
import time

from core.constants import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_DISCOVERY_ATTEMPTS,
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_DISCOVERY_RETRY_DELAY,
    DEFAULT_DISCOVERY_TIMEOUT,
    DEFAULT_MULTICAST_GROUP,
    DEFAULT_MULTICAST_TTL,
)
from core.types import DiscoveryResult
from wire.discovery_protocol import build_discover_message, parse_announce_message


logger = logging.getLogger(__name__)


def create_udp_socket(*, reuse_addr: bool = True, reuse_port: bool = False) -> socket.socket:
    """Create one UDP socket for client-side discovery."""

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    if reuse_addr:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if reuse_port and hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    return sock


def set_socket_timeout(sock: socket.socket, timeout: float) -> None:
    """Apply a timeout to one socket."""

    sock.settimeout(timeout)


def configure_multicast_sender(sock: socket.socket, ttl: int, interface_ip: str = "") -> None:
    """Configure one UDP socket for client multicast sends."""

    if not 0 <= ttl <= 255:
        raise ValueError("multicast TTL must be between 0 and 255")
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("B", ttl))
    if interface_ip:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))


def safe_close(sock: socket.socket | None) -> None:
    """Best-effort socket close."""

    if sock is None:
        return
    try:
        sock.close()
    except OSError:
        return


def resolve_local_ip(remote_host: str = "8.8.8.8", remote_port: int = 80) -> str:
    """Resolve the preferred outbound IPv4 address."""

    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect((remote_host, remote_port))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        safe_close(probe)


def resolve_multicast_interface_ip(group: str, port: int) -> str:
    """Resolve the interface IP to use for multicast sends."""

    primary_ip = resolve_local_ip()
    if not primary_ip.startswith("127."):
        return primary_ip
    fallback_ip = resolve_local_ip(remote_host=group, remote_port=max(port, 1))
    if not fallback_ip.startswith("127."):
        return fallback_ip
    return ""


def create_sender(
    *,
    multicast_group: str,
    udp_port: int,
    multicast_ttl: int,
    discovery_timeout: float,
) -> socket.socket:
    """Create a sender socket for one discovery attempt."""

    sock = create_udp_socket(reuse_addr=False)
    sock.bind(("", 0))
    interface_ip = resolve_multicast_interface_ip(multicast_group, udp_port)
    configure_multicast_sender(sock, multicast_ttl, interface_ip=interface_ip)
    set_socket_timeout(sock, discovery_timeout)
    return sock


def send_discover(sock: socket.socket, *, multicast_group: str, udp_port: int, node_name: str) -> None:
    """Send one mDNS discover query."""

    sock.sendto(build_discover_message(node_name), (multicast_group, udp_port))


def recv_packet(sock: socket.socket, buffer_size: int) -> tuple[tuple[str, int], bytes] | None:
    """Receive one UDP packet or return `None` on timeout."""

    try:
        data, addr = sock.recvfrom(buffer_size)
    except socket.timeout:
        return None
    return addr, data


def recv_announce(sock: socket.socket, *, buffer_size: int) -> DiscoveryResult:
    """Wait for one matching announce message."""

    original_timeout = sock.gettimeout()
    deadline = None if original_timeout is None else time.monotonic() + original_timeout

    try:
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return DiscoveryResult(success=False, source="mdns", message="Discovery timed out.")
                sock.settimeout(remaining)

            packet = recv_packet(sock, buffer_size)
            if packet is None:
                return DiscoveryResult(success=False, source="mdns", message="Discovery timed out.")

            addr, data = packet
            payload = parse_announce_message(data)
            if payload is None:
                continue

            return DiscoveryResult(
                success=True,
                peer_address=payload.host,
                peer_port=payload.port,
                source="mdns",
                message=f"Received main-node announcement from {payload.node_name} via {addr[0]}:{addr[1]}.",
            )
    finally:
        sock.settimeout(original_timeout)


def discover_main_node(
    node_name: str,
    *,
    multicast_group: str = DEFAULT_MULTICAST_GROUP,
    udp_port: int = DEFAULT_DISCOVERY_PORT,
    discovery_timeout: float = DEFAULT_DISCOVERY_TIMEOUT,
    discovery_attempts: int = DEFAULT_DISCOVERY_ATTEMPTS,
    discovery_retry_delay: float = DEFAULT_DISCOVERY_RETRY_DELAY,
    buffer_size: int = DEFAULT_BUFFER_SIZE,
    multicast_ttl: int = DEFAULT_MULTICAST_TTL,
) -> DiscoveryResult:
    """Try multiple mDNS discovery attempts and return the first main-node result."""

    if discovery_attempts <= 0:
        return DiscoveryResult(success=False, source="mdns", message="Discovery attempts must be greater than zero.")

    last_result = DiscoveryResult(success=False, source="mdns", message="Discovery did not run.")

    for attempt in range(1, discovery_attempts + 1):
        sock = None
        try:
            sock = create_sender(
                multicast_group=multicast_group,
                udp_port=udp_port,
                multicast_ttl=multicast_ttl,
                discovery_timeout=discovery_timeout,
            )
            logger.debug(
                "discovery attempt %s/%s sending DISCOVER to %s:%s",
                attempt,
                discovery_attempts,
                multicast_group,
                udp_port,
            )
            send_discover(sock, multicast_group=multicast_group, udp_port=udp_port, node_name=node_name)
            result = recv_announce(sock, buffer_size=buffer_size)
            if result.success:
                logger.info(
                    "discovery attempt %s/%s succeeded peer=%s:%s",
                    attempt,
                    discovery_attempts,
                    result.peer_address,
                    result.peer_port,
                )
                return result
            logger.warning(
                "discovery attempt %s/%s failed: %s",
                attempt,
                discovery_attempts,
                result.message,
            )
            last_result = DiscoveryResult(
                success=False,
                source="mdns",
                message=f"Discovery attempt {attempt}/{discovery_attempts} failed: {result.message}",
            )
        except OSError as exc:
            logger.warning(
                "discovery attempt %s/%s raised socket error: %s",
                attempt,
                discovery_attempts,
                exc,
            )
            last_result = DiscoveryResult(
                success=False,
                source="mdns",
                message=f"Unable to create or use the discovery sender socket: {exc}.",
            )
        finally:
            safe_close(sock)

        if attempt < discovery_attempts:
            time.sleep(discovery_retry_delay)

    return last_result

