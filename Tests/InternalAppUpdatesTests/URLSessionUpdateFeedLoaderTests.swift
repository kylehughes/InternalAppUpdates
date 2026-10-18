@testable import InternalAppUpdates
import Darwin
import Dispatch
import Foundation
import Testing

@Suite("URL-session feed loader", .serialized)
struct URLSessionUpdateFeedLoaderTests {
    @Test("A declared oversized response cancels its URL-session task")
    func declaredSizeLimit() async throws {
        let server = try LoopbackHTTPServer(response: """
            HTTP/1.1 200 OK\r
            Content-Length: 1000\r
            Content-Type: application/json\r
            Connection: close\r
            \r
            """ + "\nx")
        let loader = try makeLoopbackLoader()

        await #expect(throws: UpdateFeedLoaderError.responseTooLarge) {
            try await loader.load(from: server.url, maximumResponseSize: 100)
        }
        let observedPeerEOF = try await server.observedPeerEOF.value
        #expect(observedPeerEOF)
    }

    @Test("An injected session still validates its timeout")
    func injectedSessionTimeout() {
        #expect(throws: UpdateError.self) {
            _ = try URLSessionUpdateFeedLoader(
                session: .shared,
                timeout: 0,
                allowsInsecureLoopback: false
            )
        }
    }

    @Test("An insecure final response URL is rejected")
    func insecureFinalURL() async throws {
        URLProtocolStub.state.prepare { _ in
            let response = HTTPURLResponse(
                url: URL(string: "http://updates.example.com/feed.json")!,
                statusCode: 200,
                httpVersion: "HTTP/1.1",
                headerFields: nil
            )!
            return .response(response, Data())
        }
        let loader = try makeStubLoader()

        await #expect(throws: UpdateFeedLoaderError.insecureResponseURL) {
            try await loader.load(
                from: URL(string: "https://updates.example.com/feed.json")!,
                maximumResponseSize: 100
            )
        }
    }

    @Test("An insecure initial request is rejected before loading")
    func insecureInitialURL() async throws {
        let loader = try makeStubLoader()

        await #expect(throws: UpdateFeedLoaderError.insecureResponseURL) {
            try await loader.load(
                from: URL(string: "http://updates.example.com/feed.json")!,
                maximumResponseSize: 100
            )
        }
    }

    @Test("An invalid HTTP status cancels its URL-session task")
    func invalidHTTPStatus() async throws {
        let server = try LoopbackHTTPServer(response: """
            HTTP/1.1 503 Service Unavailable\r
            Content-Length: 1000\r
            Content-Type: application/json\r
            Connection: close\r
            \r
            """ + "\nx")
        let loader = try makeLoopbackLoader()

        await #expect(throws: UpdateFeedLoaderError.invalidHTTPStatus(503)) {
            try await loader.load(from: server.url, maximumResponseSize: 100)
        }
        let observedPeerEOF = try await server.observedPeerEOF.value
        #expect(observedPeerEOF)
    }

    @Test("Explicit timeouts reject unsupported values", arguments: [
        TimeInterval.leastNonzeroMagnitude,
        TimeInterval(0.000_000_000_1),
        TimeInterval(-1),
        .zero,
        .infinity,
        .nan,
        TimeInterval(Int64.max / 1_000_000_000).nextUp,
    ])
    func invalidTimeout(_ timeout: TimeInterval) {
        #expect(throws: UpdateError.self) {
            _ = try URLSessionUpdateFeedLoader(timeout: timeout)
        }
    }

    @Test("The largest nanosecond-representable timeout is accepted")
    func maximumTimeout() {
        let timeout = TimeInterval(Int64.max / 1_000_000_000)

        #expect(throws: Never.self) {
            _ = try URLSessionUpdateFeedLoader(timeout: timeout)
        }
    }

    @Test("A one-nanosecond timeout is accepted")
    func minimumTimeout() {
        let timeout: TimeInterval = 1 / 1_000_000_000

        #expect(throws: Never.self) {
            _ = try URLSessionUpdateFeedLoader(timeout: timeout)
        }
    }

    @Test("A streamed oversized response cancels its URL-session task")
    func streamedSizeLimit() async throws {
        let server = try LoopbackHTTPServer(response: """
            HTTP/1.1 200 OK\r
            Transfer-Encoding: chunked\r
            Content-Type: application/json\r
            Connection: close\r
            \r
            5\r
            12345\r
            """ + "\n")
        let loader = try makeLoopbackLoader()

        await #expect(throws: UpdateFeedLoaderError.responseTooLarge) {
            try await loader.load(from: server.url, maximumResponseSize: 4)
        }
        let observedPeerEOF = try await server.observedPeerEOF.value
        #expect(observedPeerEOF)
    }

    private func makeLoopbackLoader() throws -> URLSessionUpdateFeedLoader {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.connectionProxyDictionary = [:]
        return try URLSessionUpdateFeedLoader(
            session: URLSession(configuration: configuration),
            // The server's three-second EOF bound must expire before a request timeout could close the socket.
            timeout: 15,
            allowsInsecureLoopback: true
        )
    }

    private func makeStubLoader() throws -> URLSessionUpdateFeedLoader {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [URLProtocolStub.self]
        return try URLSessionUpdateFeedLoader(
            session: URLSession(configuration: configuration),
            timeout: 1,
            allowsInsecureLoopback: false
        )
    }
}

private struct LoopbackHTTPServer: Sendable {
    let observedPeerEOF: Task<Bool, any Error>
    let url: URL

    // Fixtures include a content type and a partial body so URLSession delivers the unfinished response promptly.
    init(response: String) throws {
        let listener = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard listener >= 0 else {
            throw LoopbackHTTPServerError.systemCall("socket", errno)
        }
        var ownsListener = true
        defer {
            if ownsListener {
                Darwin.close(listener)
            }
        }

        var reuseAddress: Int32 = 1
        guard Darwin.setsockopt(
            listener,
            SOL_SOCKET,
            SO_REUSEADDR,
            &reuseAddress,
            socklen_t(MemoryLayout.size(ofValue: reuseAddress))
        ) == 0 else {
            throw LoopbackHTTPServerError.systemCall("setsockopt", errno)
        }

        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bindResult = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(listener, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindResult == 0 else {
            throw LoopbackHTTPServerError.systemCall("bind", errno)
        }
        guard Darwin.listen(listener, 1) == 0 else {
            throw LoopbackHTTPServerError.systemCall("listen", errno)
        }

        var boundAddress = sockaddr_in()
        var boundAddressLength = socklen_t(MemoryLayout<sockaddr_in>.size)
        let nameResult = withUnsafeMutablePointer(to: &boundAddress) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.getsockname(listener, $0, &boundAddressLength)
            }
        }
        guard nameResult == 0 else {
            throw LoopbackHTTPServerError.systemCall("getsockname", errno)
        }
        let port = UInt16(bigEndian: boundAddress.sin_port)
        guard let url = URL(string: "http://127.0.0.1:\(port)/feed.json") else {
            throw LoopbackHTTPServerError.invalidURL
        }

        let responseData = Data(response.utf8)
        observedPeerEOF = Task {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Bool, any Error>) in
                DispatchQueue.global(qos: .userInitiated).async {
                    do {
                        continuation.resume(returning: try Self.serve(
                            listener: listener,
                            response: responseData
                        ))
                    } catch {
                        continuation.resume(throwing: error)
                    }
                }
            }
        }
        self.url = url
        ownsListener = false
    }

    private static func readRequestHeaders(from client: Int32) throws {
        let terminator = Data("\r\n\r\n".utf8)
        var request = Data()
        var buffer = [UInt8](repeating: 0, count: 1_024)
        while request.count <= 16 * 1_024 {
            let count = buffer.withUnsafeMutableBytes {
                Darwin.recv(client, $0.baseAddress, $0.count, 0)
            }
            if count > 0 {
                request.append(contentsOf: buffer.prefix(count))
                if request.range(of: terminator) != nil {
                    return
                }
            } else if count == 0 {
                throw LoopbackHTTPServerError.peerClosedBeforeRequest
            } else if errno != EINTR {
                throw LoopbackHTTPServerError.systemCall("recv", errno)
            }
        }
        throw LoopbackHTTPServerError.requestHeadersTooLarge
    }

    private static func serve(listener: Int32, response: Data) throws -> Bool {
        defer {
            Darwin.close(listener)
        }
        var pollDescriptor = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
        let pollResult = Darwin.poll(&pollDescriptor, 1, 3_000)
        guard pollResult > 0, pollDescriptor.revents & Int16(POLLIN) != 0 else {
            throw LoopbackHTTPServerError.acceptTimedOut
        }

        let client = Darwin.accept(listener, nil, nil)
        guard client >= 0 else {
            throw LoopbackHTTPServerError.systemCall("accept", errno)
        }
        defer {
            Darwin.close(client)
        }

        var timeout = timeval(tv_sec: 3, tv_usec: 0)
        guard Darwin.setsockopt(
            client,
            SOL_SOCKET,
            SO_RCVTIMEO,
            &timeout,
            socklen_t(MemoryLayout.size(ofValue: timeout))
        ) == 0,
            Darwin.setsockopt(
                client,
                SOL_SOCKET,
                SO_SNDTIMEO,
                &timeout,
                socklen_t(MemoryLayout.size(ofValue: timeout))
            ) == 0
        else {
            throw LoopbackHTTPServerError.systemCall("setsockopt", errno)
        }
        var noSignal: Int32 = 1
        guard Darwin.setsockopt(
            client,
            SOL_SOCKET,
            SO_NOSIGPIPE,
            &noSignal,
            socklen_t(MemoryLayout.size(ofValue: noSignal))
        ) == 0 else {
            throw LoopbackHTTPServerError.systemCall("setsockopt", errno)
        }

        try readRequestHeaders(from: client)
        try write(response, to: client)
        return try waitForPeerEOF(from: client)
    }

    private static func waitForPeerEOF(from client: Int32) throws -> Bool {
        var byte: UInt8 = 0
        while true {
            let count = Darwin.recv(client, &byte, 1, 0)
            if count == 0 {
                return true
            }
            if count > 0 {
                continue
            }
            if errno == EINTR {
                continue
            }
            if errno == EAGAIN || errno == EWOULDBLOCK {
                throw LoopbackHTTPServerError.peerEOFTimedOut
            }
            throw LoopbackHTTPServerError.systemCall("recv", errno)
        }
    }

    private static func write(_ data: Data, to client: Int32) throws {
        var offset = 0
        while offset < data.count {
            let count = data.withUnsafeBytes { bytes in
                Darwin.send(client, bytes.baseAddress?.advanced(by: offset), bytes.count - offset, 0)
            }
            if count > 0 {
                offset += count
            } else if count < 0, errno == EINTR {
                continue
            } else {
                throw LoopbackHTTPServerError.systemCall("send", errno)
            }
        }
    }
}

private enum LoopbackHTTPServerError: Error {
    case acceptTimedOut
    case invalidURL
    case peerClosedBeforeRequest
    case peerEOFTimedOut
    case requestHeadersTooLarge
    case systemCall(String, Int32)
}

private final class URLProtocolStub: URLProtocol, @unchecked Sendable {
    static let state = URLProtocolStubState()

    override class func canInit(with request: URLRequest) -> Bool { true }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        switch Self.state.handler(request) {
        case let .response(response, data):
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        }
    }

    override func stopLoading() {}
}

private enum URLProtocolStubResult: Sendable {
    case response(HTTPURLResponse, Data)
}

private final class URLProtocolStubState: @unchecked Sendable {
    private let lock = NSLock()
    private var storedHandler: @Sendable (URLRequest) -> URLProtocolStubResult = { request in
        .response(
            HTTPURLResponse(
                url: request.url!,
                statusCode: 500,
                httpVersion: "HTTP/1.1",
                headerFields: nil
            )!,
            Data()
        )
    }

    var handler: @Sendable (URLRequest) -> URLProtocolStubResult {
        lock.lock()
        defer { lock.unlock() }
        return storedHandler
    }

    func prepare(handler: @escaping @Sendable (URLRequest) -> URLProtocolStubResult) {
        lock.lock()
        storedHandler = handler
        lock.unlock()
    }
}
