"""Cross-platform entry point for the superweb-client Python end.

Responsibilities:

- ensure ``Client/python/`` is on ``sys.path`` so the per-domain packages
  (``core``, ``wire``, ``session``, ``terminal``, …) import directly when this
  script is invoked as ``python python/bootstrap.py``
- parse CLI flags shared by every platform (``--verbose`` for now; everything
  else is forwarded to the interactive driver)
- detect the current OS so the role label and default client name match the
  platform the operator is running from
- install the temporary mDNS firewall rules on Windows (no-op on Linux and
  macOS) for the duration of the interactive session
- hand off to ``session.driver.run_interactive_client`` for the actual loop
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent

# --- early -h: serve help before the venv check so it always works -----------

if "-h" in sys.argv or "--help" in sys.argv:
    if str(_PYTHON_ROOT) not in sys.path:
        sys.path.insert(0, str(_PYTHON_ROOT))

    from core.constants import (
        DEFAULT_BUFFER_SIZE,
        DEFAULT_CLIENT_NAME,
        DEFAULT_DISCOVERY_ATTEMPTS,
        DEFAULT_DISCOVERY_PORT,
        DEFAULT_DISCOVERY_RETRY_DELAY,
        DEFAULT_DISCOVERY_TIMEOUT,
        DEFAULT_MULTICAST_GROUP,
        DEFAULT_MULTICAST_TTL,
        DEFAULT_TCP_CONNECT_TIMEOUT,
    )
    _DEFAULT_VECTOR_SEED = 0x0FEDCBA987654321  # dataset/gemv/spec.py

    _p = argparse.ArgumentParser(description="superweb-client cross-platform interactive launcher")
    _p.add_argument("host", nargs="?", help="Optional main-node host or IP override.")
    _p.add_argument("--port", type=int, help="Optional main-node TCP port override.")
    _p.add_argument("--client-name", default=DEFAULT_CLIENT_NAME, help="Client name sent to the main node.")
    _p.add_argument("--timeout", type=float, default=DEFAULT_TCP_CONNECT_TIMEOUT, help="Socket timeout in seconds.")
    _p.add_argument("--preview-count", type=int, default=8, help="How many response floats to preview.")
    _p.add_argument("--vector-seed", type=lambda raw: int(raw, 0), default=_DEFAULT_VECTOR_SEED, help="Seed for the deterministic 32K request vector.")
    _p.add_argument("--iteration-count", type=int, default=1, help="How many times the cluster executes the request before responding.")
    _p.add_argument("--object-id", default="", help="Optional default object id for structured requests.")
    _p.add_argument("--stream-id", default="", help="Optional default stream id for structured requests.")
    _p.add_argument("--multicast-group", default=DEFAULT_MULTICAST_GROUP, help="mDNS multicast group.")
    _p.add_argument("--udp-port", type=int, default=DEFAULT_DISCOVERY_PORT, help="mDNS UDP port.")
    _p.add_argument("--discovery-timeout", type=float, default=DEFAULT_DISCOVERY_TIMEOUT, help="Seconds to wait for one discovery reply.")
    _p.add_argument("--discover-attempts", type=int, default=DEFAULT_DISCOVERY_ATTEMPTS, help="How many discovery attempts to make.")
    _p.add_argument("--retry-delay", type=float, default=DEFAULT_DISCOVERY_RETRY_DELAY, help="Seconds to wait between discovery attempts.")
    _p.add_argument("--buffer-size", type=int, default=DEFAULT_BUFFER_SIZE, help="UDP receive buffer size.")
    _p.add_argument("--multicast-ttl", type=int, default=DEFAULT_MULTICAST_TTL, help="mDNS sender TTL.")
    _p.add_argument("--verbose", action="store_true", help="Write DEBUG-level trace into the per-session client log file.")
    _p.parse_args(["-h"])  # prints help and exits

# --- self-bootstrap: ensure we're running inside the project venv ------------

def _venv_python(root: Path) -> Path:
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def _using_venv_python(root: Path) -> bool:
    try:
        return Path(sys.prefix).resolve() == (root / ".venv").resolve()
    except OSError:
        return False


if not _using_venv_python(_PYTHON_ROOT):
    _venv_py = _venv_python(_PYTHON_ROOT)
    if not _venv_py.exists():
        print(
            "bootstrap: virtual environment not found.\n"
            "Run 'python3 setup.py' first to create it.",
            file=sys.stderr,
        )
        sys.exit(1)

    os.execv(str(_venv_py), [str(_venv_py)] + sys.argv)

# --- venv is active from here ------------------------------------------------

from contextlib import nullcontext  # noqa: E402

PYTHON_ROOT = _PYTHON_ROOT
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from core.constants import LOG_DIRECTORY_NAME  # noqa: E402
from core.logging_setup import configure_logging  # noqa: E402
from session.driver import run_interactive_client  # noqa: E402


def _maybe_purge_previous_logs() -> None:
    """Prompt the operator once per launch about clearing old session logs.

    Runs before ``configure_logging`` installs this session's file handler so
    the prompt can safely delete everything under ``logs/`` without racing the
    active log file. Silent no-op when the directory is empty or missing —
    first-run launches and clean repos should not see a spurious prompt.

    Default answer is ``N`` so an accidental Enter preserves the existing
    logs, matching how ``pacman``-style prompts default to the safer option.
    """

    log_dir = PYTHON_ROOT / LOG_DIRECTORY_NAME
    if not log_dir.is_dir():
        return
    existing = sorted(
        path for path in log_dir.iterdir() if path.is_file() and path.suffix == ".txt"
    )
    if not existing:
        return
    print(
        f"found {len(existing)} previous client log file(s) under {log_dir}",
        flush=True,
    )
    raw = input("clear previous logs? [y/N]: ").strip().lower()
    if raw not in ("y", "yes"):
        print("kept previous logs", flush=True)
        return
    deleted = 0
    for path in existing:
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            print(f"failed to delete {path.name}: {exc}", flush=True)
    print(f"deleted {deleted} previous log file(s)", flush=True)


def _platform_role() -> str:
    """Return the per-session log role tag for the current platform."""

    if sys.platform == "win32":
        return "windows-terminal"
    if sys.platform == "darwin":
        return "macos-terminal"
    return "linux-terminal"


def _platform_client_name() -> str:
    """Return the default client_name advertised to the main node."""

    if sys.platform == "win32":
        return "windows superweb client"
    if sys.platform == "darwin":
        return "macos superweb client"
    return "linux superweb client"


def _firewall_guard():
    """Return the firewall context manager appropriate for the current platform.

    On Windows the temporary UDP 5353 (mDNS) and TCP 52021 (data plane) rules
    are installed for the duration of the session and removed on exit. On
    every other platform there is nothing to install, so a ``nullcontext`` is
    returned so the caller can treat the result uniformly.
    """

    if sys.platform != "win32":
        return nullcontext()
    from firewall import WindowsFirewallGuard

    return WindowsFirewallGuard()


def main(argv: list[str] | None = None) -> int:
    """Run the cross-platform interactive client and return a process exit code."""

    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    role = _platform_role()
    verbose = "--verbose" in effective_argv
    _maybe_purge_previous_logs()
    if verbose:
        configure_logging(verbose=True, role=role)

    try:
        with _firewall_guard():
            return run_interactive_client(
                effective_argv,
                description="superweb-client cross-platform interactive launcher",
                role=role,
                default_client_name=_platform_client_name(),
            )
    except RuntimeError as exc:
        print(f"unable to start interactive client: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
