
import Foundation

func mutateUpdateRelease(_ release: inout UpdateRelease) {
    release.id = String(repeating: "b", count: 64)
}
