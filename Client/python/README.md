# Python End

`python/` contains the cross-platform Python implementation of `superweb-client`.
It is the reference end: the Windows-native and iOS ends mirror its behavior
through the shared `proto/` contract, but do not share code with it.

The package layout is split by domain at the top level (no umbrella
`superweb_client/` namespace):

- `core/`: cross-cutting glue — constants, logging, tracing, types,
  float32 codec
- `wire/`: on-the-wire protocol — `runtime`, `runtime_codec`, `control_plane`,
  `data_plane`, `discovery_protocol`, plus generated protobuf under `proto/`
- `transport/`: bulk-byte data plane (`large_data_transfer.py`)
- `discovery/`: main-node mDNS lookup (`mdns.py`)
- `dataset/`: deterministic test-data generators for `gemv` and `conv2d`,
  byte-identical to the cluster's `compute_node/input_matrix/`
- `session/`: client lifecycle (`session.py`) and the high-level interactive
  driver loop (`driver.py`) shared by every terminal launcher
- `bootstrap.py`: cross-platform interactive entry point — sets up sys.path,
  dispatches on platform, installs the temporary mDNS firewall rules on
  Windows (no-op elsewhere) via `firewall.py`, then runs the interactive
  driver from `session/driver.py`. Invoke directly as
  `python/.venv/bin/python bootstrap.py` (Linux/macOS) or via `run.cmd`
  (Windows)
- `oneshot.py`: separate one-shot CLI entry point for sending a single
  request and exiting
- `firewall.py`: Windows-only mDNS/data-plane firewall guard, imported by
  `bootstrap.py`
- `run.cmd`: Windows double-click wrapper — finds host Python, runs
  `setup.py` to prepare `python/.venv`, self-elevates for the firewall
  rules, then hands off to `bootstrap.py`
- `setup.py`: prepares `python/.venv` and installs `requirements.txt`
  (idempotent — hash-stamped against `requirements.txt`)
- `tests/`: protocol, session, transport, and vector tests
- `download/`, `logs/`: runtime artifact and log dirs, auto-created on first
  use; gitignored except for the `.gitkeep` sentinels

Runtime responsibilities:

- automatic `HEARTBEAT` acknowledgement while waiting for request results
- background `CLIENT_INFO_REQUEST` polling only while a request is still active,
  so the client can notice when the main node disappears without polling
  forever after an idle `CLIENT_JOIN`
- artifact-backed large-result download handling under `Client/python/download/`
  where `cluster_artifact_id` is only the transfer/storage handle and
  `downloaded_to` is the actual local saved path
- per-session rotating log files under `Client/python/logs/`, auto-created
  on first run
