import InternalAppUpdates
import SwiftUI

@main
struct InternalAppUpdatesDemoApp: App {
    var body: some Scene {
        WindowGroup {
            DemoView()
        }
    }
}

@MainActor
private struct DemoView: View {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = DemoModel()
    @AppStorage("automaticallyPresentUpdates") private var automaticallyPresentUpdates = false
    @AppStorage("installationTestNote") private var installationTestNote = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 16) {
                        Image(systemName: "arrow.down.app.fill")
                            .font(.largeTitle)
                            .foregroundStyle(.tint)
                            .accessibilityHidden(true)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(String.installedBuildTitle).font(.headline)
                            Text(model.installedDescription)
                                .font(.subheadline.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(.vertical, 8)
                }

                if let controller = model.controller {
                    UpdateSection(controller: controller)
                }

                Section {
                    Toggle(String.automaticUpdatesTitle, isOn: $automaticallyPresentUpdates)
                } footer: {
                    Text(String.automaticUpdatesExplanation)
                }

                Section {
                    TextField(String.feedURLTitle, text: $model.feedURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .accessibilityIdentifier("feedURL")
                    Button(String.useFeedTitle) { connect() }
                        .disabled(model.feedURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        .accessibilityIdentifier("useFeed")
                    if let error = model.configurationError {
                        Text(error).font(.footnote).foregroundStyle(.red)
                    }
                } header: {
                    Text(String.updateSourceTitle)
                } footer: {
                    Text(String.updateSourceExplanation)
                }

                Section {
                    TextField(String.testNoteTitle, text: $installationTestNote, axis: .vertical)
                        .lineLimit(2...5)
                        .accessibilityIdentifier("retainedNote")
                } header: {
                    Text(String.savedNoteTitle)
                } footer: {
                    Text(String.savedNoteExplanation)
                }
            }
            .navigationTitle(String.appTitle)
        }
        .task { connect() }
        .onChange(of: scenePhase) { phase in
            model.controller?.setActive(phase == .active)
        }
        .onChange(of: automaticallyPresentUpdates) { automatic in
            model.controller?.presentationPolicy = automatic ? .automatic : .manual
        }
    }

    private func connect() {
        model.connect(automatic: automaticallyPresentUpdates, active: scenePhase == .active)
    }
}

@MainActor
private final class DemoModel: ObservableObject {
    @Published var configurationError: String?
    @Published var controller: UpdateController?
    @Published var feedURL: String

    init() {
        let bundledURL = Bundle.main.object(forInfoDictionaryKey: "InternalAppUpdatesFeedURL") as? String ?? ""
        feedURL = UserDefaults.standard.string(forKey: "updateFeedURL") ?? bundledURL
    }

    var installedDescription: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        return String.installedDescription(version: version, build: build)
    }

    func connect(automatic: Bool, active: Bool) {
        controller?.setActive(false)
        controller = nil
        configurationError = nil
        let value = feedURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }

        do {
            guard let url = URL(string: value) else {
                configurationError = .invalidURLMessage
                return
            }
            let configuration = try UpdateConfiguration(
                feedURL: url,
                installedBuild: InstalledBuild(),
                presentation: automatic ? .automatic : .manual
            )
            let controller = UpdateController(configuration: configuration)
            self.controller = controller
            UserDefaults.standard.set(value, forKey: "updateFeedURL")
            controller.setActive(active)
        } catch {
            configurationError = error.localizedDescription
        }
    }
}

@MainActor
private struct UpdateSection: View {
    @ObservedObject var controller: UpdateController

    var body: some View {
        Section {
            HStack {
                Text(statusDescription)
                Spacer()
                if controller.status == .checking || controller.status == .openingInstaller {
                    ProgressView()
                }
            }
            .accessibilityIdentifier("updateStatus")

            if let release = controller.availableRelease {
                Text(String.installedDescription(version: release.version, build: release.build))
                    .font(.headline.monospacedDigit())
                if let notes = release.releaseNotes, !notes.isEmpty {
                    Text(notes).font(.subheadline).foregroundStyle(.secondary)
                }
                UpdateButton(controller: controller) {
                    Label(String.installUpdateTitle, systemImage: "arrow.down.circle")
                }
                .accessibilityIdentifier("installUpdate")
            }

            Button(String.checkForUpdatesTitle) {
                Task { await controller.checkForUpdates() }
            }
            .disabled(controller.status == .checking || controller.status == .openingInstaller)
            .accessibilityIdentifier("checkForUpdates")
        } header: {
            Text(String.updatesTitle)
        } footer: {
            Text(String.installationExplanation)
        }
    }

    private var statusDescription: String {
        switch controller.status {
        case .idle: .readyStatus
        case .checking: .checkingStatus
        case .updateAvailable: .availableStatus
        case .upToDate: .upToDateStatus
        case .openingInstaller: .openingStatus
        case let .failed(error): error.localizedDescription
        }
    }
}

// These strings belong to the sample application's main bundle.
extension String {
    fileprivate static let appTitle = String(localized: "Internal App Updates", comment: "Sample app navigation title.")

    fileprivate static let automaticUpdatesExplanation = String(localized: "While this app is open, check every minute and ask iOS to present installation once for each new build. You can always install manually.", comment: "Automatic presentation setting help.")

    fileprivate static let automaticUpdatesTitle = String(localized: "Automatically Present Updates", comment: "Automatic presentation toggle.")

    fileprivate static let availableStatus = String(localized: "A new build is available", comment: "Update availability status.")

    fileprivate static let checkForUpdatesTitle = String(localized: "Check for Updates", comment: "Manual update check button.")

    fileprivate static let checkingStatus = String(localized: "Checking for updates…", comment: "Update loading status.")

    fileprivate static let feedURLTitle = String(localized: "https://your-host/app/feed.json", comment: "Feed URL field placeholder.")

    fileprivate static let installationExplanation = String(localized: "iOS asks you to confirm installation. Reopen the app afterward to check its build number.", comment: "Explains system installation confirmation.")

    fileprivate static let installUpdateTitle = String(localized: "Install Update", comment: "Installation button.")

    fileprivate static let installedBuildTitle = String(localized: "Installed Build", comment: "Installed build summary heading.")

    fileprivate static let invalidURLMessage = String(localized: "Enter a valid HTTPS feed URL.", comment: "Invalid feed URL error.")

    fileprivate static let openingStatus = String(localized: "Opening the installer…", comment: "Installation URL handoff status.")

    fileprivate static let readyStatus = String(localized: "Ready to check", comment: "Initial update status.")

    fileprivate static let savedNoteExplanation = String(localized: "Write something here before updating. It should still be here after the new build is installed.", comment: "Explains data preservation check.")

    fileprivate static let savedNoteTitle = String(localized: "Keep Through an Update", comment: "Data preservation test section.")

    fileprivate static let testNoteTitle = String(localized: "A note to keep", comment: "Persistent test note placeholder.")

    fileprivate static let updateSourceExplanation = String(localized: "Use the feed URL printed by the publisher. With Tailscale Serve, keep Tailscale connected on this device.", comment: "Feed configuration help.")

    fileprivate static let updateSourceTitle = String(localized: "Update Source", comment: "Feed configuration section title.")

    fileprivate static let updatesTitle = String(localized: "Updates", comment: "Available updates section title.")

    fileprivate static let upToDateStatus = String(localized: "This build is up to date", comment: "No newer build status.")

    fileprivate static let useFeedTitle = String(localized: "Use This Feed", comment: "Apply feed configuration button.")

    fileprivate static func installedDescription(version: String, build: String) -> String {
        String(localized: "Version \(version) · Build \(build)", comment: "App marketing version and build number.")
    }
}
