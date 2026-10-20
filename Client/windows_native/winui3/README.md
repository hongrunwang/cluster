# WinUI3 Frontend

`windows_native/winui3/` contains a WinUI3 desktop frontend for
`superweb-client`.

## Dependencies

Running the portable distribution under `dist/` needs:

- Windows 10 version 2004 or newer, x64
- `.NET Desktop Runtime 8` x64 on the target machine

Building this frontend from source needs:

- `.NET SDK 9.0.115`, pinned by `windows_native/global.json`
- Windows 10/11 x64
- the native backend DLL from `windows_native/backend/build/Release/`, which the launcher and publish script can build automatically when the local C++ toolchain is available

What it does:

- presents the client as a chat-style desktop window
- uses the shared superweb branding assets for the executable icon, window icon, and sidebar header
- can discover and connect to the main node
- shows the assigned `client_id` returned by the main node
- can send the fixed 32K vector request manually
- can send `conv2d` with either full-result download or `stats_only` response mode
- can send free-form typed text manually
- lets you set `iteration_count` for every request
- auto-saves returned inline vectors and downloaded artifacts into a `download/`
  folder next to `SuperWebClient.WinUI3.exe`; the folder is created on first use,
  so the portable distribution stays self-contained after unzip
- surfaces request accept latency, client-side elapsed time, transmission overhead, and worker timing details in the chat transcript
- can automate repeated sends on a fixed one-second cadence
- sits on top of the native Windows backend under `windows_native/backend/`

Notes:

- the current cluster runtime fully supports `gemv`
- the current WinUI3 frontend mirrors the Python terminal flow for `gemv`, `conv2d`, and `free_content`, including the temporary client-info liveness polling kept by the native backend while requests are still active
- typed text requests are still useful for protocol experiments, but the main
  node may respond with `unsupported method`
- the app tries to add temporary Windows Firewall rules for mDNS discovery when
  it starts, and removes them on exit
- the UI is configured for per-monitor DPI awareness so it stays crisp on
  Hi-DPI displays
- the color palette and logo now live in `Themes/Branding.xaml` and
  `../../assets/branding/`
- `iteration_count` repeats the assigned cluster-side math locally; it does not
  mean the frontend resends the request N times
- the temporary firewall rules are named `Allow SuperWeb-Client UDP 5353 Inbound`
  and `Allow SuperWeb-Client UDP 5353 Outbound`

If the whole app still looks blurry:

- the app should report `awareness=PerMonitorV2` in its system-message display diagnostics
- if it does not, Windows is likely bitmap-scaling the process
- if it does, the next place to check is the executable's Compatibility ->
  High DPI settings override in Windows

Build:

```powershell
dotnet build windows_native\winui3\SuperWebClient.WinUI3.csproj
```

Launch on Windows:

```bat
windows_native\winui3\start_superweb_client_winui3.cmd
```

That launcher:

- builds `windows_native/backend/` automatically if `superweb_client_backend.dll` is missing
- builds the WinUI3 app
- requests elevation before launch so temporary Windows Firewall rules for UDP 5353 can be added reliably
- starts the chat-style desktop window

If the app is launched some other way without elevation, the UI still attempts
to add the rules from inside the process on a best-effort basis. In that case,
you can still connect manually by entering the host even if Windows blocks the
firewall update.

Useful options:

```bat
windows_native\winui3\start_superweb_client_winui3.cmd --build-only
windows_native\winui3\start_superweb_client_winui3.cmd --debug
```

## Single-file publish

You can also publish the WinUI3 frontend as a single executable:

```bat
windows_native\winui3\publish_superweb_client_winui3.cmd
```

That produces:

```text
windows_native\winui3\bin\x64\Release\net8.0-windows10.0.19041.0\win-x64\publish\SuperWebClient.WinUI3.exe
```

The publish script then copies the redistributable artifact to:

```text
windows_native\winui3\dist\
```

That `dist/` directory is the main handoff artifact when you want to send the
app to someone else. It contains:

- `start_superweb_client_winui3.cmd`
- `app\...` published WinUI3 files
- `superweb-client-winui3-portable.zip`

I intentionally switched away from the earlier self-contained single-file EXE
experiment because that format ballooned to more than 1 GB on this WinUI3 toolchain.

The raw publish folder is still available here:

```text
windows_native\winui3\bin\x64\Release\net8.0-windows10.0.19041.0\win-x64\publish\start_superweb_client_winui3_published.cmd
```

The WinUI3 project is pinned to the repo-local `.NET 9.0.115` SDK through
`windows_native/global.json`, because that matches the current Windows App SDK build tooling
on this machine more reliably than `.NET 10`.

The project expects `superweb_client_backend.dll` to be available from the
native backend build under `windows_native/backend/build/Release/`. The publish
script will create that DLL automatically when possible.
