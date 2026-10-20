// swift-tools-version: 6.0

import PackageDescription
import AppleProductTypes

let package = Package(
    name: "SuperWebClientIOS",
    platforms: [
        .iOS("16.0")
    ],
    products: [
        .iOSApplication(
            name: "SuperWebClientIOS",
            targets: ["AppModule"],
            bundleIdentifier: "com.superweb.client.iossim",
            displayVersion: "0.1.0",
            bundleVersion: "1",
            iconAssetName: "AppIcon",
            supportedDeviceFamilies: [
                .phone,
                .pad
            ],
            supportedInterfaceOrientations: [
                .portrait,
                .landscapeRight,
                .landscapeLeft,
                .portraitUpsideDown(.when(deviceFamilies: [.pad]))
            ],
            additionalInfoPlistContentFilePath: "AppModule/AppInfo.plist"
        )
    ],
    targets: [
        .executableTarget(
            name: "AppModule",
            path: "AppModule"
        )
    ],
    swiftLanguageVersions: [.v6]
)
