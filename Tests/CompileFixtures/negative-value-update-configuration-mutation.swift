
import Foundation

func mutateUpdateConfiguration(_ configuration: inout UpdateConfiguration) {
    configuration.feedURL = URL(string: "https://example.com/other-feed.json")!
}
