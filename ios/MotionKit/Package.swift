// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "MotionKit",
    platforms: [.iOS(.v26), .macOS(.v26)],
    products: [.library(name: "MotionKit", targets: ["MotionKit"])],
    targets: [
        .target(name: "MotionKit"),
        .executableTarget(name: "motion-contract", dependencies: ["MotionKit"]),
        .testTarget(name: "MotionKitTests", dependencies: ["MotionKit"]),
    ]
)
