# UIKit integration

Keep the ``UpdateController`` in a scene coordinator or another object that outlives individual view controllers. Activate it from `sceneDidBecomeActive` and deactivate it from `sceneWillResignActive` and `sceneDidDisconnect`.

Use ``UpdateController/states()`` to observe coherent snapshots in a view controller. Each call creates an independent stream that immediately buffers the current state. Retain and cancel the observation task with the visible view lifecycle.

```swift
private var stateTask: Task<Void, Never>?

override func viewWillAppear(_ animated: Bool) {
    super.viewWillAppear(animated)
    stateTask?.cancel()
    installButton.isHidden = true
    stateTask = Task { [weak self, updates] in
        for await state in updates.states() {
            guard let self else { return }
            self.installButton.isHidden = state.availableRelease == nil
            self.installButton.isEnabled = updates.canRequestInstallation
        }
    }
}

override func viewDidDisappear(_ animated: Bool) {
    super.viewDidDisappear(animated)
    stateTask?.cancel()
    stateTask = nil
}

deinit {
    stateTask?.cancel()
}

@IBAction private func installUpdate() {
    Task { [updates] in
        _ = await updates.requestInstallation()
    }
}
```

The stream retains only its newest unconsumed snapshot, so it represents current state rather than every transition. A failed check appears in ``UpdateState/check``; it does not throw from or finish the stream.
