// swift-tools-version: 6.3

import PackageDescription

let package = Package(
    name: "InternalAppUpdates",
    platforms: [
        .iOS(.v16),
        .macOS(.v13),
    ],
    products: [
        .library(name: "InternalAppUpdates", targets: ["InternalAppUpdates"]),
    ],
    targets: [
        .target(name: "InternalAppUpdates"),
        .testTarget(name: "InternalAppUpdatesTests", dependencies: ["InternalAppUpdates"]),
    ],
    swiftLanguageModes: [.v6]
)
