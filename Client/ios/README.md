# iOS Client

Planning for the iOS-native `superweb-client` frontend lives in
[IOS_DEVELOPMENT_PLAN.md](IOS_DEVELOPMENT_PLAN.md).

The iOS client is a self-contained Swift implementation. It does not depend on
the Python backend; the runtime contract is mirrored from `proto/` and the
discovery / session / data-plane logic is reimplemented natively under
`SuperWebClientIOS.swiftpm/AppModule/NativeClusterBackend.swift`.

Layout:

- `SuperWebClientIOS.swiftpm/`: SwiftUI iOS app package that builds for the iOS Simulator
- `start_superweb_client_ios.sh`: helper that boots the simulator, builds the app, installs it, and launches it

## Simulator Run

From the `Client/` root:

```bash
ios/start_superweb_client_ios.sh
```

What it does:

- boots the `iPhone 17 Pro` simulator by default
- builds `SuperWebClientIOS.swiftpm`
- installs and launches `com.superweb.client.iossim`

You can override the simulator device name with:

```bash
IOS_SIM_DEVICE="iPhone 17 Pro Max" ios/start_superweb_client_ios.sh
```
