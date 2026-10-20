# superweb-client

Standalone client workspace for `superweb-cluster`.

The project is split by runtime, not by layer. The three ends share only the
wire contract under `proto/`; the implementations are independent of each
other and the Python end is the reference.

- `python/`: cross-platform Python implementation (Windows / Linux / macOS) —
  reference implementation
- `windows_native/`: native Windows C++ backend plus the WinUI3 desktop frontend
- `ios/`: self-contained Swift implementation for the iOS Simulator
- `proto/`: shared runtime protocol definitions mirrored by every end
- `assets/branding/`: shared branding assets used by the desktop frontend

## Layout

```text
Client/
├── proto/
│   └── superweb_cluster_runtime.proto
├── assets/
│   └── branding/
├── python/                    # split by domain at the top level
│   ├── bootstrap.py           # cross-platform interactive entry point
│   ├── oneshot.py             # one-shot CLI entry point
│   ├── firewall.py            # Windows mDNS/data-plane firewall guard (used by bootstrap.py)
│   ├── run.cmd                # Windows double-click wrapper around bootstrap.py
│   ├── setup.py               # prepares python/.venv + requirements.txt
│   ├── requirements.txt
│   ├── core/                  # constants, logging, tracing, types, codec
│   ├── wire/                  # protocol framing + protobuf, control/data plane
│   ├── transport/             # bulk-byte data plane
│   ├── discovery/             # mDNS main-node lookup
│   ├── dataset/               # deterministic gemv/conv2d generators
│   ├── session/               # client lifecycle + interactive driver loop
│   ├── tests/
│   ├── download/              # Python-end runtime artifacts (gitignored)
│   └── logs/                  # Python-end runtime logs (gitignored)
├── windows_native/
│   ├── global.json            # .NET SDK pin
│   ├── backend/               # native C++ backend (CMake)
│   ├── winui3/                # WinUI3 desktop frontend
│   └── tests/
│       └── harness/           # C# offline smoke harness
└── ios/
    ├── SuperWebClientIOS.swiftpm/
    ├── start_superweb_client_ios.sh
    ├── README.md
    └── IOS_DEVELOPMENT_PLAN.md
```

## Dependencies

This repo has two very different dependency profiles:

- running an existing frontend
- building frontends and native backends from source

### Runtime dependencies

- Python interactive / one-shot terminal:
  - `python3` 3.10 or newer
  - a project-local `python/.venv`
  - Python packages from `python/requirements.txt`, including `numpy` and `protobuf`
  - on Windows the launcher requests admin elevation so it can manage the
    temporary UDP 5353 firewall rules used for mDNS discovery
- WinUI3 portable distribution under `windows_native/winui3/dist/`:
  - Windows 10 version 2004 or newer, x64
  - `.NET Desktop Runtime 8` x64 on the target machine
  - the published `app/` folder already carries the WinUI3 app binaries, branding assets, and the native backend DLL

### Build-from-source dependencies

- Python frontend / backend development:
  - `python` 3.10 or newer
  - `python python/setup.py` from the `Client/` root to create `python/.venv`
    and install `numpy`, `protobuf`, and the related Python tooling
- WinUI3 frontend development:
  - `.NET SDK 9.0.115`, pinned by [windows_native/global.json](windows_native/global.json)
  - Windows 10/11 x64
- Native Windows backend development:
  - Visual Studio Build Tools with the MSVC C++ toolchain
  - CMake

### Git tracking policy

- generated binaries under `windows_native/backend/build/`,
  `windows_native/winui3/bin/`, and `windows_native/winui3/dist/` are intentionally ignored
- source files such as [windows_native/winui3/app.manifest](windows_native/winui3/app.manifest) remain tracked
- shared branding assets under [assets/branding](assets/branding) are tracked

## Current Request Model

The client currently supports two structured methods:

- `gemv`
- `conv2d`

For `gemv`, the client generates a deterministic 32K float32 request vector
and sends it to the main node. The client does not generate the 2 GiB matrix;
that matrix stays on the compute side of the cluster.

For `conv2d`, the client sends a typed request for the named `small`, `mid`,
or `large` workload, uploads the deterministic weight tensor over the TCP data
plane, and can either download the full result artifact or request a
`stats_only` summary. `object_id` / `stream_id` still flow through as normal
structured-request metadata.

Every structured request can also include an `iteration_count`, which tells
the cluster how many times to execute the same math before returning the final
output vector. When the main node accepts a session, it also assigns the client
its own runtime id such as `client-1`.

The interactive launchers also support sending free UTF-8 content with method
`free_content` or another method name you enter manually.

Each end owns its own runtime output tree. Downloads and logs are never shared
across ends:

- Python end: `Client/python/download/` and `Client/python/logs/`
- WinUI3 end: `download/` and `logs/` auto-created next to the running
  `SuperWebClient.WinUI3.exe` (inside the portable distribution's `app/`
  folder, or the project's `bin/…/` folder during development)
- iOS end: the app's sandboxed Documents directory

All of these are created on first use, so every distribution is self-contained
after unzip or install.

The Python client session keeps a background liveness loop only while it still
has an active request in flight. During that window it sends
`CLIENT_INFO_REQUEST` every 5 seconds, treats each matching
`CLIENT_INFO_REPLY` as a successful refresh, and marks the cluster as dead only
after 10 consecutive missed replies.

## Python Environment

Prepare the client-local Python environment from the `Client/` directory:

```bash
python python/setup.py
```

That creates `python/.venv` and installs the Python packages listed in
[python/requirements.txt](python/requirements.txt).

## Entrypoints

### Windows users

Double-click entrypoint (preferred):

```bat
python\run.cmd
```

What it does:

- finds host Python (`py -3` or `python` on PATH)
- runs `setup.py` to ensure `python\.venv` and the dependencies in
  `python\requirements.txt` are current (idempotent — only the first run pays
  the install cost)
- self-elevates so it can add and remove the temporary UDP 5353 firewall rules
- runs the cross-platform interactive client wrapped in the Windows firewall guard
- saves returned vectors or matrices into `Client/python/download/`

If you want a desktop UI instead of a terminal, launch:

```bat
windows_native\winui3\start_superweb_client_winui3.cmd
```

That WinUI3 frontend uses the native Windows backend and presents requests and
responses like a chat conversation. It also tries to build the native backend
DLL automatically if it is missing.

If you want a published single-file desktop executable, run:

```bat
windows_native\winui3\publish_superweb_client_winui3.cmd
```

That publish step produces a single-file `.exe`, then copies the distributable
result to:

```text
windows_native/winui3/dist/
```

### Linux / macOS users

Cross-platform entry point:

```bash
python/.venv/bin/python python/bootstrap.py
```

`bootstrap.py` detects the current platform and skips the Windows firewall step
on Linux and macOS.

### iOS Simulator users (macOS hosts only)

```bash
ios/start_superweb_client_ios.sh
```

That helper boots the `iPhone 17 Pro` simulator by default, builds the
`SuperWebClientIOS.swiftpm` package, installs it, and launches it. The iOS app
implements the runtime protocol natively in Swift and does not depend on the
Python end.

## One-shot Python Tool

```bash
python/.venv/bin/python python/oneshot.py
```

Optional direct-connect override:

```bash
python/.venv/bin/python python/oneshot.py 192.168.1.148 --port 52020
```

Useful options:

- `--timeout 30`: set the TCP connect/socket polling timeout used by the Python client
- `--vector-seed 0x1234`: change the deterministic 32K request vector
- `--save-output out.bin`: copy the auto-saved result from `Client/python/download/` to another path
- `--object-id` and `--stream-id`: populate structured request metadata

## Windows Native Backend

The native Windows backend lives in [windows_native/backend/](windows_native/backend/README.md).
It exposes a stable C ABI so the active WinUI3 frontend can reuse the same
discovery, vector, and runtime protocol logic without embedding Python.

## Verification

Run the Python protocol tests from the `Client/` directory:

```bash
python/.venv/bin/python -m unittest discover -s python/tests -p "test_*.py"
```

On Windows, you can also run the offline WinUI3-native smoke tests from the
`Client/` directory:

```powershell
python\.venv\Scripts\python.exe -m unittest discover -s windows_native\backend\tests -p "test_*.py"
```

Those smoke tests build and invoke `windows_native/tests/harness/`, which
reuses the same `NativeClientBackend.cs` service layer that the WinUI3 app uses
for `free_content`, `conv2d` stats mode, and `conv2d` full artifact download.
