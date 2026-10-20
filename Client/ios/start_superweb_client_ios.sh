#!/usr/bin/env bash
set -euo pipefail

IOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$IOS_DIR/.." && pwd)"
APP_DIR="$IOS_DIR/SuperWebClientIOS.swiftpm"
DERIVED_DATA_DIR="$IOS_DIR/build"
DEVICE_NAME="${IOS_SIM_DEVICE:-iPhone 17 Pro}"
BUNDLE_ID="com.superweb.client.iossim"

cd "$ROOT_DIR"

DEVICE_UDID="$(
    xcrun simctl list devices available \
        | awk -F'[()]' -v name="$DEVICE_NAME" '$1 ~ name { print $2; exit }'
)"

if [[ -z "$DEVICE_UDID" ]]; then
    echo "Unable to find available simulator named '$DEVICE_NAME'" >&2
    exit 1
fi

echo "Booting simulator $DEVICE_NAME ($DEVICE_UDID)"
xcrun simctl boot "$DEVICE_UDID" >/dev/null 2>&1 || true
open -a Simulator --args -CurrentDeviceUDID "$DEVICE_UDID" >/dev/null 2>&1 || true
xcrun simctl bootstatus "$DEVICE_UDID" -b

echo "Building SuperWebClientIOS"
(
    cd "$APP_DIR"
    xcodebuild \
        -scheme SuperWebClientIOS \
        -destination "platform=iOS Simulator,id=$DEVICE_UDID" \
        -derivedDataPath "$DERIVED_DATA_DIR" \
        build
)

APP_PATH="$(find "$DERIVED_DATA_DIR" -name 'SuperWebClientIOS.app' -print -quit)"
if [[ -z "$APP_PATH" ]]; then
    echo "Built app bundle not found under $DERIVED_DATA_DIR" >&2
    exit 1
fi

echo "Installing app: $APP_PATH"
xcrun simctl install "$DEVICE_UDID" "$APP_PATH"

echo "Launching app"
xcrun simctl launch "$DEVICE_UDID" "$BUNDLE_ID"
