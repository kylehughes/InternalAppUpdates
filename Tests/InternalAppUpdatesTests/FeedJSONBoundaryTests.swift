@testable import InternalAppUpdates
import Foundation
import Testing

@Suite("Feed JSON boundary")
struct FeedJSONBoundaryTests {
    let currentOS = OperatingSystemVersion(majorVersion: 16, minorVersion: 4, patchVersion: 0)

    @Test("Duplicate release members are rejected", arguments: [
        #""version": "1.1", "version": "1.2""#,
        #""version": "1.2", "version": "1.1""#,
        #""version": "1.1", "version": "1.1""#,
    ])
    func duplicateReleaseMember(replacement: String) {
        let json = historicalFeed.replacingOccurrences(of: #""version": "1.1""#, with: replacement)

        #expect(throws: duplicateMemberError) {
            try decode(Data(json.utf8))
        }
    }

    @Test("Duplicate root members are rejected", arguments: [
        #"{"schemaVersion":1,"schemaVersion":1,"bundleIdentifier":"com.example.App","release":null}"#,
        #"{"schemaVersion":1,"schemaVersion":2,"bundleIdentifier":"com.example.App","release":null}"#,
        #"{"schemaVersion":2,"schemaVersion":1,"bundleIdentifier":"com.example.App","release":null}"#,
    ])
    func duplicateRootMember(json: String) {
        #expect(throws: duplicateMemberError) {
            try decode(Data(json.utf8))
        }
    }

    @Test("Duplicate members in nested objects are rejected")
    func duplicateNestedMember() {
        let json = #"{"schemaVersion":1,"bundleIdentifier":"com.example.App","release":null,"future":[{"value":1,"value":2}]}"#

        #expect(throws: duplicateMemberError) {
            try decode(Data(json.utf8))
        }
    }

    @Test("Escaped-equivalent member names are duplicates")
    func escapedDuplicateMember() {
        let json = #"{"schemaVersion":1,"schema\u0056ersion":1,"bundleIdentifier":"com.example.App","release":null}"#

        #expect(throws: duplicateMemberError) {
            try decode(Data(json.utf8))
        }
    }

    @Test("Malformed JSON and invalid encodings are rejected", arguments: [
        Data(#"{"schemaVersion":1"#.utf8),
        Data([0xFF]),
    ])
    func malformedJSON(data: Data) {
        #expect(throws: UpdateError.malformedFeed("The response is not valid JSON.")) {
            try decode(data)
        }
    }

    @Test("Object-like text inside a string does not create members")
    func memberLikeStringContent() throws {
        let notes = #"Literal {"version":"1"}: text ["key": "value"]"#
        let release = try #require(
            try decode(try makeFeed(release: makeReleaseObject(releaseNotes: notes)))
        )

        #expect(release.releaseNotes == notes)
    }

    @Test("A fixed historical feed with distinct members remains valid")
    func historicalFeedRemainsValid() throws {
        let release = try #require(try decode(Data(historicalFeed.utf8)))

        #expect(release.id == releaseID)
        #expect(release.releaseNotes == "Install this release.")
        #expect(release.version == "1.1")
    }

    @Test("Current feed output remains valid")
    func currentFeedOutputRemainsValid() throws {
        let release = try #require(try decode(try makeFeed()))

        #expect(release.id == releaseID)
    }

    @Test("Supported Unicode JSON encodings remain valid")
    func unicodeEncodingsRemainValid() throws {
        for (encoding, byteOrderMark) in unicodeEncodings {
            let release = try #require(
                try decode(try encodedFeed(encoding: encoding, byteOrderMark: byteOrderMark))
            )
            #expect(release.version == "1.1")
        }
    }

    @Test("Duplicate detection covers supported Unicode JSON encodings")
    func unicodeEncodingsRejectDuplicates() throws {
        let duplicateJSON = historicalFeed.replacingOccurrences(
            of: #""version": "1.1""#,
            with: #""version": "1.1", "version": "1.2""#
        )

        for (encoding, byteOrderMark) in unicodeEncodings {
            var data = Data(byteOrderMark)
            data.append(try #require(duplicateJSON.data(using: encoding)))
            #expect(throws: duplicateMemberError) {
                try decode(data)
            }
        }
    }

    @Test("releaseNotes may be omitted")
    func releaseNotesMayBeOmitted() throws {
        let release = try #require(try decode(try makeFeed()))

        #expect(release.releaseNotes == nil)
    }

    @Test("releaseNotes may be a string")
    func releaseNotesMayBeString() throws {
        let release = try #require(
            try decode(try makeFeed(release: makeReleaseObject(releaseNotes: "Install this release.")))
        )

        #expect(release.releaseNotes == "Install this release.")
    }

    @Test("releaseNotes may be an empty string")
    func releaseNotesMayBeEmptyString() throws {
        let release = try #require(
            try decode(try makeFeed(release: makeReleaseObject(releaseNotes: "")))
        )

        #expect(release.releaseNotes == "")
    }

    @Test("A null releaseNotes member is rejected")
    func nullReleaseNotesIsRejected() throws {
        #expect(throws: releaseNotesTypeError) {
            try decode(try makeFeed(release: makeReleaseObject(releaseNotes: NSNull())))
        }
    }

    @Test("A nonstring releaseNotes member is rejected")
    func nonstringReleaseNotesIsRejected() throws {
        for value: Any in [true, 42, ["text"]] {
            #expect(throws: releaseNotesTypeError) {
                try decode(try makeFeed(release: makeReleaseObject(releaseNotes: value)))
            }
        }
    }

    private var duplicateMemberError: UpdateError {
        .malformedFeed("The feed contains duplicate object member names.")
    }

    private var releaseNotesTypeError: UpdateError {
        .malformedFeed("releaseNotes must be a string when present.")
    }

    private var unicodeEncodings: [(String.Encoding, [UInt8])] {
        [
            (.utf8, []),
            (.utf8, [0xEF, 0xBB, 0xBF]),
            (.utf16BigEndian, []),
            (.utf16BigEndian, [0xFE, 0xFF]),
            (.utf16LittleEndian, []),
            (.utf16LittleEndian, [0xFF, 0xFE]),
            (.utf32BigEndian, []),
            (.utf32BigEndian, [0x00, 0x00, 0xFE, 0xFF]),
            (.utf32LittleEndian, []),
        ]
    }

    private func decode(_ data: Data) throws -> UpdateRelease? {
        try FeedDecoder.decode(
            data,
            installedBuild: makeInstalledBuild(),
            currentOSVersion: currentOS,
            now: fixedNow
        )
    }

    private func encodedFeed(encoding: String.Encoding, byteOrderMark: [UInt8]) throws -> Data {
        var data = Data(byteOrderMark)
        data.append(try #require(historicalFeed.data(using: encoding)))
        return data
    }
}

private let historicalFeed = """
    {
      "schemaVersion": 1,
      "bundleIdentifier": "com.example.App",
      "release": {
        "id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "version": "1.1",
        "build": "1",
        "minimumOSVersion": "16.0",
        "publishedAt": "2026-10-14T00:00:00Z",
        "expiresAt": "2027-01-01T00:00:00Z",
        "manifestURL": "https://updates.example.com/app/manifest.plist",
        "releaseNotes": "Install this release."
      }
    }
    """
