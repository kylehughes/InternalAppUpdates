import Foundation

enum JSONMemberValidator {
    enum ValidationError: Error {
        case duplicateMemberName
        case invalidEncoding
        case invalidStructure
    }

    /// Checks object member names in a payload whose complete JSON syntax was already validated by
    /// `JSONSerialization`. This scanner relies on that validation to identify quoted strings followed by a colon as
    /// object member names.
    static func validateUniqueObjectMemberNames(in data: Data) throws {
        let bytes = try normalizedUTF8Bytes(from: data)
        var objectMemberNames: [Set<[UInt8]>] = []
        var index = 0

        while index < bytes.count {
            switch bytes[index] {
            case 0x22:
                let stringStart = index
                index += 1
                while index < bytes.count {
                    if bytes[index] == 0x5C {
                        guard index + 1 < bytes.count else {
                            throw ValidationError.invalidStructure
                        }
                        index += 2
                    } else if bytes[index] == 0x22 {
                        index += 1
                        break
                    } else {
                        index += 1
                    }
                }
                guard index <= bytes.count, bytes[index - 1] == 0x22 else {
                    throw ValidationError.invalidStructure
                }

                var lookahead = index
                while lookahead < bytes.count, isJSONWhitespace(bytes[lookahead]) {
                    lookahead += 1
                }
                if lookahead < bytes.count, bytes[lookahead] == 0x3A {
                    guard let objectIndex = objectMemberNames.indices.last else {
                        throw ValidationError.invalidStructure
                    }
                    let encodedName = Data(bytes[stringStart..<index])
                    guard let name = try? JSONDecoder().decode(String.self, from: encodedName) else {
                        throw ValidationError.invalidStructure
                    }
                    guard objectMemberNames[objectIndex].insert(Array(name.utf8)).inserted else {
                        throw ValidationError.duplicateMemberName
                    }
                }
            case 0x7B:
                objectMemberNames.append([])
                index += 1
            case 0x7D:
                guard objectMemberNames.popLast() != nil else {
                    throw ValidationError.invalidStructure
                }
                index += 1
            default:
                index += 1
            }
        }

        guard objectMemberNames.isEmpty else {
            throw ValidationError.invalidStructure
        }
    }

    private static func isJSONWhitespace(_ byte: UInt8) -> Bool {
        byte == 0x20 || byte == 0x09 || byte == 0x0A || byte == 0x0D
    }

    private static func normalizedUTF8Bytes(from data: Data) throws -> [UInt8] {
        let bytes = [UInt8](data)
        let encoding: String.Encoding
        let byteOrderMarkLength: Int

        if bytes.starts(with: [0x00, 0x00, 0xFE, 0xFF]) {
            encoding = .utf32BigEndian
            byteOrderMarkLength = 4
        } else if bytes.starts(with: [0xFF, 0xFE, 0x00, 0x00]) {
            encoding = .utf32LittleEndian
            byteOrderMarkLength = 4
        } else if bytes.starts(with: [0xEF, 0xBB, 0xBF]) {
            encoding = .utf8
            byteOrderMarkLength = 3
        } else if bytes.starts(with: [0xFE, 0xFF]) {
            encoding = .utf16BigEndian
            byteOrderMarkLength = 2
        } else if bytes.starts(with: [0xFF, 0xFE]) {
            encoding = .utf16LittleEndian
            byteOrderMarkLength = 2
        } else if bytes.count >= 4, bytes[0] == 0, bytes[1] == 0, bytes[2] == 0 {
            encoding = .utf32BigEndian
            byteOrderMarkLength = 0
        } else if bytes.count >= 4, bytes[1] == 0, bytes[2] == 0, bytes[3] == 0 {
            encoding = .utf32LittleEndian
            byteOrderMarkLength = 0
        } else if bytes.count >= 4, bytes[0] == 0, bytes[2] == 0 {
            encoding = .utf16BigEndian
            byteOrderMarkLength = 0
        } else if bytes.count >= 4, bytes[1] == 0, bytes[3] == 0 {
            encoding = .utf16LittleEndian
            byteOrderMarkLength = 0
        } else {
            encoding = .utf8
            byteOrderMarkLength = 0
        }

        guard let string = String(
            data: Data(bytes.dropFirst(byteOrderMarkLength)),
            encoding: encoding
        ) else {
            throw ValidationError.invalidEncoding
        }
        return Array(string.utf8)
    }
}
