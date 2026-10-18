import Foundation

struct PositiveDuration: Sendable {
    static let fifteenSeconds: Self = {
        guard let duration = Self(15) else {
            preconditionFailure("A fixed 15-second duration must be valid.")
        }
        return duration
    }()

    let seconds: TimeInterval

    init?(_ seconds: TimeInterval) {
        guard seconds.isFinite,
              seconds >= 0.000_000_001,
              seconds <= TimeInterval(Int64.max / 1_000_000_000)
        else {
            return nil
        }

        self.seconds = seconds
    }

    var duration: Duration {
        .seconds(seconds)
    }
}
