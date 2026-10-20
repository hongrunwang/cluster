# Windows Native Backend

`windows_native/backend/` contains the native C++ backend for `superweb-client`. It mirrors
the current `superweb-cluster` discovery and runtime protocol, generates the same
deterministic 32K request vector, and sits behind the active WinUI3 frontend.

If you only want to run the terminal client on Windows, use the launcher under
`python/terminal/` instead of calling this backend directly.

## Dependencies

To build this backend from source on Windows, you need:

- Visual Studio Build Tools with the MSVC C++ toolchain
- CMake

This directory does not need to be present on an end-user machine if you are
shipping the prebuilt WinUI3 portable distribution from
`windows_native/winui3/dist/`, because that publish step already copies the
compiled `superweb_client_backend.dll` into the portable app folder.

Current responsibilities:

- discover the main node over mDNS
- open and manage one TCP client session
- send `CLIENT_JOIN`
- send `CLIENT_REQUEST` for `gemv`
- send typed `CLIENT_REQUEST` payloads such as `free_content` and `conv2d`
- mirror the latest `CLIENT_REQUEST_OK` fields, including upload/download slot metadata
- decode `CLIENT_RESPONSE`, including conv2d stats summaries and per-worker timing
- keep the temporary client-info liveness polling loop active while requests are in flight
- expose a stable C ABI for higher-level Windows UI integrations

The Windows backend does not generate the 2 GiB benchmark matrix. That matrix is
owned by the cluster-side compute nodes. The client only generates the 32K
request vector that is sent with each fixed-matrix-vector request.

## Public ABI

The exported header is [superweb_client_backend.h](./include/superweb_client_backend.h).
The API is synchronous on purpose:

- UI code can keep threading decisions in the frontend layer
- the DLL remains simple to call from C++, C++/WinRT, or P/Invoke-style bridges

Important lifecycle notes:

- initialize `SwcResponse response{}` before first use
- call `swc_reset_response(&response)` before reusing a response buffer
- call `swc_free_vector(...)` for standalone generated vectors
- call `swc_close_client(...)` when the session is no longer needed

## Build

This backend builds as a DLL through CMake.

```powershell
cmake -S windows_native\backend -B windows_native\backend\build
cmake --build windows_native\backend\build --config Release
```

The produced library sits behind the WinUI3 desktop frontend under
`windows_native/winui3/`, and it can also be reused later by a WinUI3 shell,
C++/WinRT layer, or any other higher-level Windows UI wrapper without having to
reimplement the mDNS or protobuf wire logic.

## Offline Smoke Tests

Windows-only offline smoke tests live under `windows_native/backend/tests/`.
They build the small harness under `windows_native/tests/harness/`, stand up a
Python mock control/data plane, and exercise the same `NativeClientBackend.cs`
service layer used by the WinUI3 app.

Run them from the `Client/` root:

```powershell
python\.venv\Scripts\python.exe -m unittest discover -s windows_native\backend\tests -p "test_*.py"
```
