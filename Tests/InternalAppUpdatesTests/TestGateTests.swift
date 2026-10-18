import Foundation
import Testing

@Suite("Test gates")
struct TestGateTests {
    @Test("Installation gate resumes every attached caller and latches its first result")
    func installationGateResumesAllCallers() async throws {
        let gate = InstallationGate()
        let url = try #require(URL(string: "itms-services://?action=download-manifest"))
        let first = Task {
            await gate.install(url)
        }
        let second = Task {
            await gate.install(url)
        }

        try await withAsyncCleanup {
            try await gate.waitUntilStarted(count: 2)
            #expect(await gate.urls.count == 2)
            await gate.finish(with: true)
            await gate.finish(with: false)

            let firstResult = await first.value
            let secondResult = await second.value
            let lateResult = await gate.install(url)
            #expect(firstResult)
            #expect(secondResult)
            #expect(lateResult)
        } cleanup: {
            await gate.finish(with: false)
            _ = await first.value
            _ = await second.value
        }
    }

    @Test("Load gate resumes every attached caller and latches its first result")
    func loadGateResumesAllCallers() async throws {
        let gate = LoadGate()
        let expected = Data("first result".utf8)
        let fallback = Data("cleanup result".utf8)
        let url = try #require(URL(string: "https://updates.example.com/feed.json"))
        let first = Task {
            try await gate.load(from: url, maximumResponseSize: 1)
        }
        let second = Task {
            try await gate.load(from: url, maximumResponseSize: 1)
        }

        try await withAsyncCleanup {
            try await gate.waitUntilStarted(count: 2)
            #expect(await gate.loadCount == 2)
            await gate.finish(with: expected)
            await gate.finish(with: fallback)

            let firstResult = try await first.value
            let secondResult = try await second.value
            let lateResult = try await gate.load(from: url, maximumResponseSize: 1)
            #expect(firstResult == expected)
            #expect(secondResult == expected)
            #expect(lateResult == expected)
        } cleanup: {
            await gate.finish(with: fallback)
            _ = try? await first.value
            _ = try? await second.value
        }
    }

    private func withAsyncCleanup(
        operation: () async throws -> Void,
        cleanup: () async -> Void
    ) async rethrows {
        do {
            try await operation()
        } catch {
            await cleanup()
            throw error
        }
        await cleanup()
    }
}
