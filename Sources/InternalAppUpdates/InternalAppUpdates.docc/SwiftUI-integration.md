# SwiftUI integration

Own the ``UpdateController`` above the views that display its state, then connect it to scene activity.

```swift
import InternalAppUpdates
import SwiftUI

struct InternalUpdatesView: View {
    @Environment(\.scenePhase) private var scenePhase
    @ObservedObject var updates: UpdateController

    var body: some View {
        VStack {
            Button("Check for Updates") {
                Task { await updates.checkForUpdates() }
            }

            if let release = updates.availableRelease {
                Text("Build \(release.build)")
                UpdateButton(controller: updates) {
                    Label("Install Update", systemImage: "arrow.down.circle")
                }
            }
        }
        .task { updates.setActive(scenePhase == .active) }
        .onChange(of: scenePhase) { phase in
            updates.setActive(phase == .active)
        }
    }
}
```

``UpdateButton`` disables itself while no eligible release is ready or a handoff is active. The host app provides localized copy, layout, and any release-notes presentation.

For a multiwindow app, coordinate scene transitions so one backgrounded scene does not deactivate an updater that another active scene owns.
