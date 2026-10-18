#if canImport(SwiftUI)
import SwiftUI

/// A button that requests installation through an update controller.
///
/// The host supplies the label and decides where the button belongs in its interface.
@available(iOS 16, macOS 13, *)
public struct UpdateButton<Label: View>: View {
    @ObservedObject private var controller: UpdateController
    private let label: () -> Label

    /// Creates an update button with a caller-provided label.
    ///
    /// - Parameters:
    ///   - controller: The controller that owns the available release.
    ///   - label: The view that describes the button's action.
    public init(
        controller: UpdateController,
        @ViewBuilder label: @escaping () -> Label
    ) {
        self.controller = controller
        self.label = label
    }

    /// The installation action, disabled while checking, handing off, or without an eligible release.
    public var body: some View {
        Button {
            Task { await controller.requestInstallation() }
        } label: {
            label()
        }
        .disabled(!controller.canRequestInstallation)
    }
}
#endif
