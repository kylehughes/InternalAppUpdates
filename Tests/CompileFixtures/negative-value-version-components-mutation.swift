
extension VersionComponents {
    private static func compileProofVersionComponentsAreImmutable() {
        var version = VersionComponents("1")!
        version.major = 2
    }
}
