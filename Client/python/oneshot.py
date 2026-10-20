"""One-shot CLI entry point for sending a single fixed-matrix-vector request."""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from core.constants import (  # noqa: E402
    DEFAULT_BUFFER_SIZE,
    DEFAULT_DISCOVERY_ATTEMPTS,
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_DISCOVERY_RETRY_DELAY,
    DEFAULT_DISCOVERY_TIMEOUT,
    DEFAULT_MULTICAST_GROUP,
    DEFAULT_MULTICAST_TTL,
    DEFAULT_TCP_CONNECT_TIMEOUT,
    DEFAULT_TCP_PORT,
    STATUS_OK,
)
from core.logging_setup import configure_logging, current_log_path, get_logger  # noqa: E402
from session.session import connect_session, resolve_main_node_endpoint  # noqa: E402
from dataset.gemv import (  # noqa: E402
    DEFAULT_VECTOR_LENGTH,
    DEFAULT_VECTOR_SEED,
    generate_default_request_vector_values,
)


logger = get_logger("oneshot")


def build_parser() -> argparse.ArgumentParser:
    """Build the terminal client CLI."""

    parser = argparse.ArgumentParser(description="superweb-cluster fixed-matrix-vector terminal client.")
    parser.add_argument("host", nargs="?", help="Optional main-node host or IP override.")
    parser.add_argument(
        "--port",
        type=int,
        help="Optional main-node TCP port override. Defaults to the discovered port or 52020.",
    )
    parser.add_argument("--client-name", default="terminal superweb client", help="Client name sent to the main node.")
    parser.add_argument("--request-id", help="Explicit request id. Defaults to a timestamp-based id.")
    parser.add_argument("--object-id", default="", help="Optional object id field sent with CLIENT_REQUEST.")
    parser.add_argument("--stream-id", default="", help="Optional stream id field sent with CLIENT_REQUEST.")
    parser.add_argument(
        "--iteration-count",
        type=int,
        default=1,
        help="How many times the cluster should execute the same request math before responding.",
    )
    parser.add_argument("--vector-seed", type=lambda raw: int(raw, 0), default=DEFAULT_VECTOR_SEED, help="Seed used for the deterministic 32K request vector.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TCP_CONNECT_TIMEOUT, help="Socket timeout in seconds. Defaults to 30.")
    parser.add_argument("--preview-count", type=int, default=8, help="How many output floats to print from the response head.")
    parser.add_argument("--save-output", help="Optional path for saving the response output vector bytes.")
    parser.add_argument("--multicast-group", default=DEFAULT_MULTICAST_GROUP, help="mDNS multicast group.")
    parser.add_argument("--udp-port", type=int, default=DEFAULT_DISCOVERY_PORT, help="mDNS UDP port.")
    parser.add_argument(
        "--discovery-timeout",
        type=float,
        default=DEFAULT_DISCOVERY_TIMEOUT,
        help="Seconds to wait for one mDNS discovery reply.",
    )
    parser.add_argument(
        "--discover-attempts",
        type=int,
        default=DEFAULT_DISCOVERY_ATTEMPTS,
        help="How many mDNS discovery attempts to make before failing.",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_DISCOVERY_RETRY_DELAY,
        help="Seconds to wait between mDNS discovery attempts.",
    )
    parser.add_argument("--buffer-size", type=int, default=DEFAULT_BUFFER_SIZE, help="UDP receive buffer size.")
    parser.add_argument("--multicast-ttl", type=int, default=DEFAULT_MULTICAST_TTL, help="mDNS sender TTL.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Write DEBUG-level trace into the per-session client log file.",
    )
    return parser


def _request_id_from_clock() -> str:
    return f"client-{int(time.time() * 1000)}"


def _preview(values: list[float], count: int) -> str:
    preview_count = max(0, min(count, len(values)))
    return "[" + ", ".join(f"{value:.6f}" for value in values[:preview_count]) + "]"


def main() -> int:
    """Run one terminal client request."""

    args = build_parser().parse_args()
    if args.verbose:
        configure_logging(verbose=True, role="terminal-oneshot")
        log_path = current_log_path()
        if log_path is not None:
            print(f"logging session events to {log_path}", flush=True)
        logger.info(
            "one-shot terminal client start host=%s port=%s client_name=%s",
            args.host or "<auto>",
            args.port if args.port is not None else "<auto>",
            args.client_name,
        )

    try:
        host, port, source = resolve_main_node_endpoint(
            host=args.host,
            port=args.port,
            client_name=args.client_name,
            multicast_group=args.multicast_group,
            udp_port=args.udp_port,
            discovery_timeout=args.discovery_timeout,
            discovery_attempts=args.discover_attempts,
            discovery_retry_delay=args.retry_delay,
            buffer_size=args.buffer_size,
            multicast_ttl=args.multicast_ttl,
        )
    except RuntimeError as exc:
        print(f"unable to resolve superweb-cluster main node: {exc}", flush=True)
        logger.error("main-node resolution failed: %s", exc)
        return 1

    request_id = args.request_id or _request_id_from_clock()
    vector_values = generate_default_request_vector_values(seed=args.vector_seed)

    print(
        f"connecting to superweb-cluster main node {host}:{port} via {source} "
        f"with vector_length={DEFAULT_VECTOR_LENGTH} seed=0x{args.vector_seed:016X}",
        flush=True,
    )

    session = None
    try:
        session = connect_session(host, port, timeout=args.timeout)
        join_response = session.join(args.client_name)
        print(
            f"CLIENT_JOIN status_code={join_response.status_code} "
            f"client_id={join_response.client_id or '<empty>'} "
            f"worker_count={join_response.worker_count} client_count={join_response.client_count}",
            flush=True,
        )

        result = session.request_gemv(
            client_name=args.client_name,
            request_id=request_id,
            vector_values=vector_values,
            iteration_count=args.iteration_count,
            object_id=args.object_id,
            stream_id=args.stream_id,
        )
        response = result.response
        print(
            f"CLIENT_RESPONSE request_id={response.request_id} status_code={response.status_code} "
            f"output_length={response.output_length} client_id={response.client_id or '<empty>'} "
            f"iteration_count={response.iteration_count} worker_count={response.worker_count} "
            f"client_count={response.client_count}",
            flush=True,
        )
        if response.error_message:
            print(f"error_message={response.error_message}", flush=True)
        if response.status_code == STATUS_OK:
            if result.saved_output_path is not None:
                print(f"downloaded_to={result.saved_output_path}", flush=True)
            print(f"output_preview={_preview(result.output_values, args.preview_count)}", flush=True)
        if args.save_output and (
            result.saved_output_path is not None
            or result.output_bytes
            or response.output_vector
        ):
            output_path = Path(args.save_output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if result.saved_output_path is not None and result.saved_output_path.exists():
                shutil.copyfile(result.saved_output_path, output_path)
            else:
                output_path.write_bytes(result.output_bytes or response.output_vector)
            print(f"saved response bytes to {output_path}", flush=True)
        return 0 if response.status_code == STATUS_OK else 1
    except (OSError, RuntimeError, ValueError, ConnectionError) as exc:
        print(f"client session failed: {exc}", flush=True)
        logger.error("one-shot client session failed: %s", exc)
        return 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
