"""High-level interactive driver loop shared by every terminal launcher."""

from __future__ import annotations

import argparse
import itertools
import logging
from pathlib import Path

from core.constants import (
    CONV2D_CLIENT_RESPONSE_FULL,
    CONV2D_CLIENT_RESPONSE_STATS_ONLY,
    CONV2D_DEFAULT_STATS_MAX_SAMPLES,
    DEFAULT_BUFFER_SIZE,
    DEFAULT_CLIENT_NAME,
    DEFAULT_DISCOVERY_ATTEMPTS,
    DEFAULT_DISCOVERY_PORT,
    DEFAULT_DISCOVERY_RETRY_DELAY,
    DEFAULT_DISCOVERY_TIMEOUT,
    DEFAULT_DOWNLOAD_DIR,
    DEFAULT_MULTICAST_GROUP,
    DEFAULT_MULTICAST_TTL,
    DEFAULT_TCP_CONNECT_TIMEOUT,
    DEFAULT_TCP_PORT,
    METHOD_GEMM,
    METHOD_GEMV,
    METHOD_FREE_CONTENT,
    METHOD_CONV2D,
    WORKLOAD_SIZE_LARGE,
)
from core.logging_setup import configure_logging, current_log_path
from .session import RequestResult, SuperWebClientSession, connect_session, resolve_main_node_endpoint
from dataset.gemv import (
    DEFAULT_VECTOR_SEED,
    generate_default_request_vector_values,
    normalize_request_size,
    vector_length_for_size,
)
from wire.discovery_protocol import normalize_manual_address


logger = logging.getLogger(__name__)

_LOCAL_REQUEST_COUNTER = itertools.count(1)


def build_terminal_parser(description: str, *, default_client_name: str = DEFAULT_CLIENT_NAME) -> argparse.ArgumentParser:
    """Build one CLI parser for the interactive terminal clients."""

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("host", nargs="?", help="Optional main-node host or IP override.")
    parser.add_argument("--port", type=int, help="Optional main-node TCP port override.")
    parser.add_argument("--client-name", default=default_client_name, help="Client name sent to the main node.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TCP_CONNECT_TIMEOUT, help="Socket timeout in seconds. Defaults to 30.")
    parser.add_argument("--preview-count", type=int, default=8, help="How many response floats to preview.")
    parser.add_argument(
        "--vector-seed",
        type=lambda raw: int(raw, 0),
        default=DEFAULT_VECTOR_SEED,
        help="Seed used for the default deterministic 32K request vector.",
    )
    parser.add_argument(
        "--iteration-count",
        type=int,
        default=1,
        help="How many times the cluster should execute the same request math before responding.",
    )
    parser.add_argument(
        "--object-id",
        default="",
        help="Optional default object id for structured requests. The conv2d terminal flow asks separately for a local download name.",
    )
    parser.add_argument("--stream-id", default="", help="Optional default stream id for structured requests.")
    parser.add_argument("--multicast-group", default=DEFAULT_MULTICAST_GROUP, help="mDNS multicast group.")
    parser.add_argument("--udp-port", type=int, default=DEFAULT_DISCOVERY_PORT, help="mDNS UDP port.")
    parser.add_argument("--discovery-timeout", type=float, default=DEFAULT_DISCOVERY_TIMEOUT, help="Seconds to wait for one discovery reply.")
    parser.add_argument("--discover-attempts", type=int, default=DEFAULT_DISCOVERY_ATTEMPTS, help="How many discovery attempts to make.")
    parser.add_argument("--retry-delay", type=float, default=DEFAULT_DISCOVERY_RETRY_DELAY, help="Seconds to wait between discovery attempts.")
    parser.add_argument("--buffer-size", type=int, default=DEFAULT_BUFFER_SIZE, help="UDP receive buffer size.")
    parser.add_argument("--multicast-ttl", type=int, default=DEFAULT_MULTICAST_TTL, help="mDNS sender TTL.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Write DEBUG-level trace into the per-session client log file.",
    )
    return parser


def _request_id_from_clock(prefix: str = "client") -> str:
    normalized_prefix = (prefix.strip().lower() or "req").split("-", 1)[0][:4] or "req"
    return f"{normalized_prefix}-{next(_LOCAL_REQUEST_COUNTER):x}"


def _logged_input(prompt: str, *, preview_limit: int = 240) -> str:
    raw = input(prompt)
    if len(raw) > preview_limit:
        shown = f"{raw[:preview_limit]!r}... ({len(raw)} chars total)"
    else:
        shown = repr(raw)
    logger.info("prompt=%r input=%s", prompt, shown)
    return raw


def _auto_conv2d_download_name(size: str) -> str:
    normalized_size = normalize_request_size(size, default=WORKLOAD_SIZE_LARGE)
    return f"{_request_id_from_clock('conv2d')}-{normalized_size}"


def _prompt_size(prompt: str, *, default: str = WORKLOAD_SIZE_LARGE) -> str:
    raw = _logged_input(prompt).strip()
    normalized = normalize_request_size(raw, default=default)
    if normalized not in {"small", "mid", "large"}:
        raise ValueError("size must be small, mid, or large")
    return normalized


def _preview(values: list[float], count: int) -> str:
    preview_count = max(0, min(count, len(values)))
    return "[" + ", ".join(f"{value:.6f}" for value in values[:preview_count]) + "]"


def _decode_text_preview(raw: bytes, limit: int = 240) -> str | None:
    if not raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _print_response(result: RequestResult, preview_count: int) -> None:
    response = result.response
    print(
        f"CLIENT_RESPONSE task_id={response.task_id or response.request_id or '<empty>'} "
        f"method={response.method or '<empty>'} "
        f"size={response.size or '<empty>'} "
        f"status_code={response.status_code} client_id={response.client_id or '<empty>'} "
        f"iteration_count={response.iteration_count} worker_count={response.worker_count} "
        f"client_count={response.client_count} elapsed_ms={response.elapsed_ms}",
        flush=True,
    )
    print(
        f"client_elapsed_ms={result.client_elapsed_ms} "
        f"request_accept_latency_ms={result.request_accept_latency_ms} "
        f"transmission_overhead_ms={result.transmission_overhead_ms}",
        flush=True,
    )
    if response.error_message:
        print(f"error_message={response.error_message}", flush=True)
    timing = response.timing
    if timing is not None:
        print(
            f"timing dispatch_ms={timing.dispatch_ms} task_window_ms={timing.task_window_ms} "
            f"aggregate_ms={timing.aggregate_ms}",
            flush=True,
        )
        for worker in timing.workers:
            extras = []
            if worker.artifact_fetch_ms:
                extras.append(f"fetch_ms={worker.artifact_fetch_ms}")
            if worker.computation_ms:
                extras.append(f"compute_ms={worker.computation_ms}")
            if worker.peripheral_ms:
                extras.append(f"peripheral_ms={worker.peripheral_ms}")
            suffix = (" " + " ".join(extras)) if extras else ""
            print(
                f"worker node={worker.node_id or '<?>'} slice={worker.slice or '<?>'} "
                f"wall_ms={worker.wall_ms}{suffix}",
                flush=True,
            )
    if response.result_artifact is not None:
        print(
            f"cluster_artifact_id={response.result_artifact.artifact_id} "
            f"artifact_bytes={response.result_artifact.size_bytes}",
            flush=True,
        )
    if result.saved_output_path is not None:
        print(f"downloaded_to={result.saved_output_path}", flush=True)
    if result.artifact_downloaded:
        if result.artifact_checksum:
            print(f"checksum={result.artifact_checksum}", flush=True)
        return

    conv2d_payload = response.conv2d_payload if response.method == METHOD_CONV2D else None
    if conv2d_payload is not None and conv2d_payload.stats_element_count:
        count = conv2d_payload.stats_element_count
        stats_sum = conv2d_payload.stats_sum
        stats_sum_squares = conv2d_payload.stats_sum_squares
        mean = stats_sum / count if count else 0.0
        variance = max(stats_sum_squares / count - mean * mean, 0.0) if count else 0.0
        print(
            f"stats_only element_count={count} sum={stats_sum:.6f} "
            f"sum_squares={stats_sum_squares:.6f} mean={mean:.6f} variance={variance:.6f}",
            flush=True,
        )
        samples = list(conv2d_payload.stats_samples)
        if samples:
            print(f"samples={_preview(samples, len(samples))}", flush=True)
        return

    if result.output_values:
        print(f"output_preview={_preview(result.output_values, preview_count)}", flush=True)
        return

    raw_output = result.output_bytes or response.output_vector
    text_preview = _decode_text_preview(raw_output)
    if text_preview:
        print(f"output_text={text_preview}", flush=True)
    elif raw_output:
        print(f"output_bytes={len(raw_output)}", flush=True)


def _send_conv2d_request(session: SuperWebClientSession, args: argparse.Namespace) -> None:
    size = _prompt_size("size [small/mid/large, default large]: ")
    want_full = _prompt_yes_no("download full result? [Y/n]: ", default=True)
    if want_full:
        client_response_mode = CONV2D_CLIENT_RESPONSE_FULL
        stats_max_samples = CONV2D_DEFAULT_STATS_MAX_SAMPLES
        download_name = _prompt_optional(
            "download name [used as your local download name; press Enter for an auto-assigned name]: "
        )
        resolved_download_name = download_name or _auto_conv2d_download_name(size)
    else:
        client_response_mode = CONV2D_CLIENT_RESPONSE_STATS_ONLY
        stats_max_samples = _prompt_positive_int(
            f"max samples [default {CONV2D_DEFAULT_STATS_MAX_SAMPLES}]: ",
            default=CONV2D_DEFAULT_STATS_MAX_SAMPLES,
        )
        resolved_download_name = ""
    print(
        f"sending {METHOD_CONV2D} "
        f"size={size} download_name={resolved_download_name or '<stats_only>'} "
        f"iteration_count={args.iteration_count} "
        f"client_response_mode={'stats_only' if client_response_mode == CONV2D_CLIENT_RESPONSE_STATS_ONLY else 'full'} "
        f"stats_max_samples={stats_max_samples}",
        flush=True,
    )
    result = session.request_conv2d(
        client_name=args.client_name,
        request_id=_request_id_from_clock(f"conv2d-{size}"),
        size=size,
        local_download_name=resolved_download_name,
        iteration_count=args.iteration_count,
        stream_id=args.stream_id,
        client_response_mode=client_response_mode,
        stats_max_samples=stats_max_samples,
        on_task_accepted=lambda task_id: print(
            f"CLIENT_REQUEST_OK task_id={task_id} method={METHOD_CONV2D} size={size} download_name={resolved_download_name or '<stats_only>'}",
            flush=True,
        ),
    )
    _print_response(result, args.preview_count)


def _prompt_optional(prompt: str) -> str:
    return _logged_input(prompt).strip()


def _prompt_yes_no(prompt: str, *, default: bool) -> bool:
    raw = _logged_input(prompt).strip().lower()
    if not raw:
        return default
    if raw in {"y", "yes"}:
        return True
    if raw in {"n", "no"}:
        return False
    raise ValueError("answer must be y or n")


def _prompt_positive_int(prompt: str, *, default: int) -> int:
    raw = _logged_input(prompt).strip()
    if not raw:
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError("value must be positive")
    return value


def _resolve_endpoint_with_fallback(args: argparse.Namespace) -> tuple[str, int, str]:
    try:
        return resolve_main_node_endpoint(
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
        print(f"main-node discovery failed: {exc}", flush=True)
        manual = _logged_input("Enter host[:port] for manual connect, or press Enter to quit: ").strip()
        if not manual:
            raise
        host, port = normalize_manual_address(manual, args.port or DEFAULT_TCP_PORT)
        return host, port, "manual"


def _connect_and_join(args: argparse.Namespace) -> SuperWebClientSession:
    host, port, source = _resolve_endpoint_with_fallback(args)
    print(f"connecting to {host}:{port} via {source}", flush=True)
    session = connect_session(host, port, timeout=args.timeout)
    join_response = session.join(args.client_name)
    print(
        f"CLIENT_JOIN status_code={join_response.status_code} "
        f"client_id={join_response.client_id or '<empty>'} "
        f"worker_count={join_response.worker_count} client_count={join_response.client_count}",
        flush=True,
    )
    return session


def _send_gemv_request(session: SuperWebClientSession, args: argparse.Namespace) -> None:
    size = _prompt_size("size [small/mid/large, default large]: ")
    iteration_count = _prompt_positive_int(
        f"iteration count [default {args.iteration_count}]: ",
        default=args.iteration_count,
    )
    vector_values = generate_default_request_vector_values(seed=args.vector_seed, size=size)
    vector_length = vector_length_for_size(size)
    print(
        f"sending {METHOD_GEMV} "
        f"size={size} vector_length={vector_length} seed=0x{args.vector_seed:016X} "
        f"iteration_count={iteration_count}",
        flush=True,
    )
    result = session.request_gemv(
        client_name=args.client_name,
        request_id=_request_id_from_clock("fixed"),
        vector_values=vector_values,
        size=size,
        iteration_count=iteration_count,
        object_id=args.object_id,
        stream_id=args.stream_id,
        on_task_accepted=lambda task_id: print(
            f"CLIENT_REQUEST_OK task_id={task_id} method={METHOD_GEMV} size={size} object_id={args.object_id or '<empty>'}",
            flush=True,
        ),
    )
    _print_response(result, args.preview_count)


def _send_gemm_request(session: SuperWebClientSession, args: argparse.Namespace) -> None:
    size = _prompt_size("size [small/mid/large, default large]: ")
    iteration_count = _prompt_positive_int(
        f"iteration count [default {args.iteration_count}]: ",
        default=args.iteration_count,
    )
    print(
        f"sending {METHOD_GEMM} "
        f"size={size} iteration_count={iteration_count}",
        flush=True,
    )
    result = session.request_gemm(
        client_name=args.client_name,
        request_id=_request_id_from_clock(f"gemm-{size}"),
        size=size,
        iteration_count=iteration_count,
        object_id=args.object_id,
        stream_id=args.stream_id,
        on_task_accepted=lambda task_id: print(
            f"CLIENT_REQUEST_OK task_id={task_id} method={METHOD_GEMM} size={size}",
            flush=True,
        ),
    )
    _print_response(result, args.preview_count)


def _send_free_content(session: SuperWebClientSession, args: argparse.Namespace) -> None:
    method = METHOD_FREE_CONTENT
    content = _logged_input("free content [press Enter to cancel]: ")
    if not content:
        print("nothing sent", flush=True)
        return
    print(f"sending {method} with {len(content.encode('utf-8'))} UTF-8 bytes", flush=True)
    result = session.request_free_content(
        client_name=args.client_name,
        request_id=_request_id_from_clock("text"),
        content=content,
        iteration_count=args.iteration_count,
        object_id=args.object_id,
        stream_id=args.stream_id,
        method=method,
        on_task_accepted=lambda task_id: print(
            f"CLIENT_REQUEST_OK task_id={task_id} method={method} object_id={args.object_id or '<empty>'}",
            flush=True,
        ),
    )
    _print_response(result, args.preview_count)


def _iter_download_artifacts() -> list[tuple[Path, int]]:
    """Return ``(path, size_bytes)`` for matrix/vector outputs in the download dir.

    Only top-level ``.bin`` files are returned. Packaged vendor trees, benchmark
    JSON/Markdown under ``download/artifacts``, and any other subdirectory
    content are intentionally excluded — those aren't GEMV/GEMM/conv2d task
    outputs and should not be offered up for deletion through the clear menu.
    """

    if not DEFAULT_DOWNLOAD_DIR.is_dir():
        return []
    entries = [
        path
        for path in DEFAULT_DOWNLOAD_DIR.iterdir()
        if path.is_file() and path.suffix == ".bin"
    ]
    entries.sort(key=lambda path: path.name.lower())
    return [(path, path.stat().st_size) for path in entries]


def _format_bytes(size: int) -> str:
    """Render a byte count as a short human-readable string (1 KiB / 3.5 MiB / ...)."""

    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TiB"  # pragma: no cover — exhausted by the TiB branch above


def _parse_index_selection(raw: str, total: int) -> list[int]:
    """Parse ``"1,3 5-7"`` / ``"all"`` / ``""`` into a sorted list of 1-based indices.

    Returns an empty list for empty input or when no index matches. Raises
    ``ValueError`` when a token cannot be parsed so callers can print a
    diagnostic instead of silently deleting the wrong files.
    """

    text = raw.strip().lower()
    if not text:
        return []
    if text == "all":
        return list(range(1, total + 1))
    selected: set[int] = set()
    for token in text.replace(",", " ").split():
        if not token:
            continue
        if "-" in token:
            lo_text, _, hi_text = token.partition("-")
            lo = int(lo_text)
            hi = int(hi_text)
            if lo > hi:
                lo, hi = hi, lo
            for index in range(lo, hi + 1):
                if 1 <= index <= total:
                    selected.add(index)
        else:
            index = int(token)
            if 1 <= index <= total:
                selected.add(index)
    return sorted(selected)


def _list_download_artifacts() -> None:
    """Print the current matrix/vector downloads so the operator can eyeball the directory."""

    entries = _iter_download_artifacts()
    if not entries:
        print(f"no matrix/vector files in {DEFAULT_DOWNLOAD_DIR}", flush=True)
        return
    print(
        f"{len(entries)} matrix/vector file(s) under {DEFAULT_DOWNLOAD_DIR}:",
        flush=True,
    )
    for index, (path, size) in enumerate(entries, start=1):
        print(f"  [{index}] {path.name}  ({_format_bytes(size)})", flush=True)


def _clear_download_artifacts() -> None:
    """Prompt for an index selection and delete the chosen matrix/vector files."""

    entries = _iter_download_artifacts()
    if not entries:
        print(f"no matrix/vector files to clear in {DEFAULT_DOWNLOAD_DIR}", flush=True)
        return
    print(
        f"{len(entries)} matrix/vector file(s) under {DEFAULT_DOWNLOAD_DIR}:",
        flush=True,
    )
    for index, (path, size) in enumerate(entries, start=1):
        print(f"  [{index}] {path.name}  ({_format_bytes(size)})", flush=True)
    raw = _logged_input(
        "select numbers to delete (e.g. '1,3 5-7'), 'all', or empty to cancel: "
    )
    try:
        selected = _parse_index_selection(raw, len(entries))
    except ValueError:
        print("unable to parse selection, nothing deleted", flush=True)
        return
    if not selected:
        print("nothing selected, no files deleted", flush=True)
        return
    deleted = 0
    for index in selected:
        path, _size = entries[index - 1]
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            print(f"failed to delete {path.name}: {exc}", flush=True)
    logger.info("cleared %d matrix/vector file(s) from download dir", deleted)
    print(f"deleted {deleted} file(s)", flush=True)


def run_interactive_client(
    argv: list[str] | None,
    *,
    description: str,
    role: str = "client",
    default_client_name: str = DEFAULT_CLIENT_NAME,
) -> int:
    """Run the interactive terminal client loop."""

    args = build_terminal_parser(description, default_client_name=default_client_name).parse_args(argv)
    if args.verbose:
        configure_logging(verbose=True, role=role)
        log_path = current_log_path()
        if log_path is not None:
            print(f"logging session events to {log_path}", flush=True)
        logger.info(
            "interactive client start role=%s host=%s port=%s",
            role,
            args.host or "<auto>",
            args.port if args.port is not None else "<auto>",
        )
    session: SuperWebClientSession | None = None
    try:
        session = _connect_and_join(args)
        while True:
            print("", flush=True)
            print("superweb-client menu", flush=True)
            print(f"1. Generate and send {METHOD_GEMV}", flush=True)
            print(f"2. Send {METHOD_CONV2D}", flush=True)
            print(f"3. Generate and send {METHOD_GEMM}", flush=True)
            print("4. Send free content", flush=True)
            print("5. List downloaded matrix/vector files", flush=True)
            print("6. Clear downloaded matrix/vector files", flush=True)
            print("7. Exit", flush=True)
            choice = _logged_input("> ").strip()

            if choice == "1":
                _send_gemv_request(session, args)
            elif choice == "2":
                _send_conv2d_request(session, args)
            elif choice == "3":
                _send_gemm_request(session, args)
            elif choice == "4":
                _send_free_content(session, args)
            elif choice == "5":
                _list_download_artifacts()
            elif choice == "6":
                _clear_download_artifacts()
            elif choice == "7":
                print("bye", flush=True)
                logger.info("interactive client exited via menu")
                return 0
            else:
                print("please choose 1, 2, 3, 4, 5, 6, or 7", flush=True)
    except (KeyboardInterrupt, EOFError):
        print("\nbye", flush=True)
        logger.warning("interactive client exited via EOF/interrupt")
        return 0
    except (OSError, RuntimeError, ValueError, ConnectionError) as exc:
        print(f"client session failed: {exc}", flush=True)
        logger.error("interactive client session failed: %s", exc)
        return 1
    finally:
        if session is not None:
            session.close()
