import Foundation

enum MessageKind: Int, Sendable {
    case unspecified = 0
    case registerWorker = 1
    case registerOk = 2
    case heartbeat = 3
    case heartbeatOk = 4
    case clientJoin = 5
    case clientRequest = 6
    case clientResponse = 7
    case taskAssign = 8
    case taskAccept = 9
    case taskFail = 10
    case taskResult = 11
    case artifactRelease = 12
    case workerUpdate = 13
    case clientInfoRequest = 14
    case clientInfoReply = 15
    case clientRequestOk = 16
}

enum ProtoWireType: UInt8 {
    case varint = 0
    case fixed64 = 1
    case lengthDelimited = 2
    case fixed32 = 5
}

enum ProtoCodecError: LocalizedError {
    case malformedVarint
    case truncatedField
    case unsupportedWireType(UInt8)
    case typeMismatch(String)
    case invalidUTF8

    var errorDescription: String? {
        switch self {
        case .malformedVarint:
            return "Malformed protobuf varint."
        case .truncatedField:
            return "Truncated protobuf field."
        case .unsupportedWireType(let wireType):
            return "Unsupported protobuf wire type: \(wireType)."
        case .typeMismatch(let message):
            return message
        case .invalidUTF8:
            return "Invalid UTF-8 field in protobuf payload."
        }
    }
}

struct ProtoEncoder {
    private(set) var data = Data()

    mutating func appendVarintField(_ number: Int, _ value: UInt64) {
        appendKey(fieldNumber: number, wireType: .varint)
        data.appendVarint(value)
    }

    mutating func appendBoolField(_ number: Int, _ value: Bool) {
        appendVarintField(number, value ? 1 : 0)
    }

    mutating func appendStringField(_ number: Int, _ value: String) {
        guard !value.isEmpty else { return }
        appendBytesField(number, Data(value.utf8))
    }

    mutating func appendBytesField(_ number: Int, _ value: Data) {
        guard !value.isEmpty else { return }
        appendKey(fieldNumber: number, wireType: .lengthDelimited)
        data.appendVarint(UInt64(value.count))
        data.append(value)
    }

    mutating func appendMessageField(_ number: Int, _ nested: Data) {
        guard !nested.isEmpty else { return }
        appendBytesField(number, nested)
    }

    private mutating func appendKey(fieldNumber: Int, wireType: ProtoWireType) {
        let key = UInt64((fieldNumber << 3) | Int(wireType.rawValue))
        data.appendVarint(key)
    }
}

struct ProtoReader {
    let data: Data
    private(set) var offset: Int = 0

    mutating func nextField() throws -> (number: Int, wireType: ProtoWireType)? {
        guard offset < data.count else {
            return nil
        }
        let key = try readVarint()
        guard let wireType = ProtoWireType(rawValue: UInt8(key & 0x7)) else {
            throw ProtoCodecError.unsupportedWireType(UInt8(key & 0x7))
        }
        return (Int(key >> 3), wireType)
    }

    mutating func readUInt64(wireType: ProtoWireType) throws -> UInt64 {
        guard wireType == .varint else {
            throw ProtoCodecError.typeMismatch("Expected varint field.")
        }
        return try readVarint()
    }

    mutating func readUInt32(wireType: ProtoWireType) throws -> UInt32 {
        UInt32(try readUInt64(wireType: wireType))
    }

    mutating func readBool(wireType: ProtoWireType) throws -> Bool {
        try readUInt64(wireType: wireType) != 0
    }

    mutating func readString(wireType: ProtoWireType) throws -> String {
        let bytes = try readBytes(wireType: wireType)
        guard let value = String(data: bytes, encoding: .utf8) else {
            throw ProtoCodecError.invalidUTF8
        }
        return value
    }

    mutating func readBytes(wireType: ProtoWireType) throws -> Data {
        guard wireType == .lengthDelimited else {
            throw ProtoCodecError.typeMismatch("Expected length-delimited field.")
        }
        let length = Int(try readVarint())
        guard length >= 0, offset + length <= data.count else {
            throw ProtoCodecError.truncatedField
        }
        let value = data.subdata(in: offset..<(offset + length))
        offset += length
        return value
    }

    mutating func skipField(wireType: ProtoWireType) throws {
        switch wireType {
        case .varint:
            _ = try readVarint()
        case .fixed64:
            guard offset + 8 <= data.count else {
                throw ProtoCodecError.truncatedField
            }
            offset += 8
        case .lengthDelimited:
            let length = Int(try readVarint())
            guard length >= 0, offset + length <= data.count else {
                throw ProtoCodecError.truncatedField
            }
            offset += length
        case .fixed32:
            guard offset + 4 <= data.count else {
                throw ProtoCodecError.truncatedField
            }
            offset += 4
        }
    }

    private mutating func readVarint() throws -> UInt64 {
        var result: UInt64 = 0
        var shift: UInt64 = 0
        for _ in 0..<10 {
            guard offset < data.count else {
                throw ProtoCodecError.truncatedField
            }
            let byte = data[offset]
            offset += 1
            result |= UInt64(byte & 0x7F) << shift
            if byte & 0x80 == 0 {
                return result
            }
            shift += 7
        }
        throw ProtoCodecError.malformedVarint
    }
}

private extension Data {
    mutating func appendVarint(_ value: UInt64) {
        var remaining = value
        while remaining >= 0x80 {
            append(UInt8(remaining & 0x7F) | 0x80)
            remaining >>= 7
        }
        append(UInt8(remaining))
    }
}

struct RuntimeHeartbeat {
    var mainNodeName: String = ""
    var unixTimeMs: UInt64 = 0

    func encode() -> Data {
        var encoder = ProtoEncoder()
        encoder.appendStringField(1, mainNodeName)
        encoder.appendVarintField(2, unixTimeMs)
        return encoder.data
    }

    static func decode(from data: Data) throws -> Self {
        var message = Self()
        var reader = ProtoReader(data: data)
        while let field = try reader.nextField() {
            switch field.number {
            case 1:
                message.mainNodeName = try reader.readString(wireType: field.wireType)
            case 2:
                message.unixTimeMs = try reader.readUInt64(wireType: field.wireType)
            default:
                try reader.skipField(wireType: field.wireType)
            }
        }
        return message
    }
}

struct RuntimeHeartbeatOk {
    var nodeName: String = ""
    var heartbeatUnixTimeMs: UInt64 = 0
    var receivedUnixTimeMs: UInt64 = 0
    var activeTaskIds: [String] = []

    func encode() -> Data {
        var encoder = ProtoEncoder()
        encoder.appendStringField(1, nodeName)
        encoder.appendVarintField(2, heartbeatUnixTimeMs)
        encoder.appendVarintField(3, receivedUnixTimeMs)
        for taskID in activeTaskIds {
            encoder.appendStringField(4, taskID)
        }
        return encoder.data
    }
}

struct RuntimeClientJoin {
    var clientName: String = ""

    func encode() -> Data {
        var encoder = ProtoEncoder()
        encoder.appendStringField(1, clientName)
        return encoder.data
    }
}

struct RuntimeClientInfoRequest {
    var clientId: String = ""
    var clientName: String = ""
    var timestampMs: UInt64 = 0

    func encode() -> Data {
        var encoder = ProtoEncoder()
        encoder.appendStringField(1, clientId)
        encoder.appendStringField(2, clientName)
        encoder.appendVarintField(3, timestampMs)
        return encoder.data
    }
}

struct RuntimeClientInfoReply {
    var clientId: String = ""
    var requestTimestampMs: UInt64 = 0
    var replyTimestampMs: UInt64 = 0
    var timeoutMs: UInt64 = 0
    var hasActiveTasks: Bool = false
    var activeTaskIds: [String] = []

    static func decode(from data: Data) throws -> Self {
        var message = Self()
        var reader = ProtoReader(data: data)
        while let field = try reader.nextField() {
            switch field.number {
            case 1:
                message.clientId = try reader.readString(wireType: field.wireType)
            case 2:
                message.requestTimestampMs = try reader.readUInt64(wireType: field.wireType)
            case 3:
                message.replyTimestampMs = try reader.readUInt64(wireType: field.wireType)
            case 4:
                message.timeoutMs = try reader.readUInt64(wireType: field.wireType)
            case 5:
                message.hasActiveTasks = try reader.readBool(wireType: field.wireType)
            case 6:
                message.activeTaskIds.append(try reader.readString(wireType: field.wireType))
            default:
                try reader.skipField(wireType: field.wireType)
            }
        }
        return message
    }
}

struct RuntimeArtifactDescriptor {
    var artifactId: String = ""
    var contentType: String = ""
    var sizeBytes: UInt64 = 0
    var checksum: String = ""
    var producerNodeId: String = ""
    var transferHost: String = ""
    var transferPort: UInt32 = 0
    var chunkSize: UInt32 = 0
    var ready: Bool = true

    static func decode(from data: Data) throws -> Self {
        var message = Self()
        var reader = ProtoReader(data: data)
        while let field = try reader.nextField() {
            switch field.number {
            case 1:
                message.artifactId = try reader.readString(wireType: field.wireType)
            case 2:
                message.contentType = try reader.readString(wireType: field.wireType)
            case 3:
                message.sizeBytes = try reader.readUInt64(wireType: field.wireType)
            case 4:
                message.checksum = try reader.readString(wireType: field.wireType)
            case 5:
                message.producerNodeId = try reader.readString(wireType: field.wireType)
            case 6:
                message.transferHost = try reader.readString(wireType: field.wireType)
            case 7:
                message.transferPort = try reader.readUInt32(wireType: field.wireType)
            case 8:
                message.chunkSize = try reader.readUInt32(wireType: field.wireType)
            case 9:
                message.ready = try reader.readBool(wireType: field.wireType)
            default:
                try reader.skipField(wireType: field.wireType)
            }
        }
        return message
    }
}

struct RuntimeGemvRequestPayload {
    var vectorLength: UInt32 = 0
    var vectorData = Data()

    func encode() -> Data {
        var encoder = ProtoEncoder()
        encoder.appendVarintField(1, UInt64(vectorLength))
        encoder.appendBytesField(2, vectorData)
        return encoder.data
    }
}

struct RuntimeConv2dRequestPayload {
    var tensorH: UInt32 = 0
    var tensorW: UInt32 = 0
    var channelsIn: UInt32 = 0
    var channelsOut: UInt32 = 0
    var kernelSize: UInt32 = 0
    var padding: UInt32 = 0
    var stride: UInt32 = 1

    func encode() -> Data {
        var encoder = ProtoEncoder()
        encoder.appendVarintField(1, UInt64(tensorH))
        encoder.appendVarintField(2, UInt64(tensorW))
        encoder.appendVarintField(3, UInt64(channelsIn))
        encoder.appendVarintField(4, UInt64(channelsOut))
        encoder.appendVarintField(5, UInt64(kernelSize))
        encoder.appendVarintField(6, UInt64(padding))
        encoder.appendVarintField(7, UInt64(stride))
        return encoder.data
    }
}

struct RuntimeGemvResponsePayload {
    var outputLength: UInt32 = 0
    var outputVector = Data()

    static func decode(from data: Data) throws -> Self {
        var message = Self()
        var reader = ProtoReader(data: data)
        while let field = try reader.nextField() {
            switch field.number {
            case 1:
                message.outputLength = try reader.readUInt32(wireType: field.wireType)
            case 2:
                message.outputVector = try reader.readBytes(wireType: field.wireType)
            default:
                try reader.skipField(wireType: field.wireType)
            }
        }
        return message
    }
}

struct RuntimeConv2dResponsePayload {
    var outputLength: UInt32 = 0
    var outputVector = Data()
    var resultArtifactId: String = ""

    static func decode(from data: Data) throws -> Self {
        var message = Self()
        var reader = ProtoReader(data: data)
        while let field = try reader.nextField() {
            switch field.number {
            case 1:
                message.outputLength = try reader.readUInt32(wireType: field.wireType)
            case 2:
                message.outputVector = try reader.readBytes(wireType: field.wireType)
            case 3:
                message.resultArtifactId = try reader.readString(wireType: field.wireType)
            default:
                try reader.skipField(wireType: field.wireType)
            }
        }
        return message
    }
}

struct RuntimeClientRequest {
    var requestId: String = ""
    var clientName: String = ""
    var method: String = ""
    var size: String = ""
    var objectId: String = ""
    var streamId: String = ""
    var timestampMs: UInt64 = 0
    var iterationCount: UInt32 = 1
    var gemvPayload: RuntimeGemvRequestPayload?
    var conv2dPayload: RuntimeConv2dRequestPayload?

    func encode() -> Data {
        var encoder = ProtoEncoder()
        encoder.appendStringField(1, requestId)
        encoder.appendStringField(2, clientName)
        encoder.appendStringField(3, method)
        encoder.appendStringField(10, size)
        encoder.appendStringField(4, objectId)
        encoder.appendStringField(5, streamId)
        encoder.appendVarintField(6, timestampMs)
        encoder.appendVarintField(7, UInt64(iterationCount))
        if let gemvPayload {
            encoder.appendMessageField(8, gemvPayload.encode())
        } else if let conv2dPayload {
            encoder.appendMessageField(9, conv2dPayload.encode())
        }
        return encoder.data
    }
}

struct RuntimeClientRequestOk {
    var clientId: String = ""
    var taskId: String = ""
    var method: String = ""
    var size: String = ""
    var objectId: String = ""
    var acceptedTimestampMs: UInt64 = 0

    static func decode(from data: Data) throws -> Self {
        var message = Self()
        var reader = ProtoReader(data: data)
        while let field = try reader.nextField() {
            switch field.number {
            case 1:
                message.clientId = try reader.readString(wireType: field.wireType)
            case 2:
                message.taskId = try reader.readString(wireType: field.wireType)
            case 3:
                message.method = try reader.readString(wireType: field.wireType)
            case 6:
                message.size = try reader.readString(wireType: field.wireType)
            case 4:
                message.objectId = try reader.readString(wireType: field.wireType)
            case 5:
                message.acceptedTimestampMs = try reader.readUInt64(wireType: field.wireType)
            default:
                try reader.skipField(wireType: field.wireType)
            }
        }
        return message
    }
}

struct RuntimeClientResponse {
    var requestId: String = ""
    var method: String = ""
    var size: String = ""
    var objectId: String = ""
    var streamId: String = ""
    var timestampMs: UInt64 = 0
    var statusCode: UInt32 = 0
    var errorMessage: String = ""
    var workerCount: UInt32 = 0
    var clientCount: UInt32 = 0
    var clientId: String = ""
    var iterationCount: UInt32 = 0
    var gemvPayload: RuntimeGemvResponsePayload?
    var conv2dPayload: RuntimeConv2dResponsePayload?
    var resultArtifact: RuntimeArtifactDescriptor?
    var taskId: String = ""
    var elapsedMs: UInt64 = 0

    var outputVector: Data {
        if let gemvPayload {
            return gemvPayload.outputVector
        }
        if let conv2dPayload {
            return conv2dPayload.outputVector
        }
        return Data()
    }

    static func decode(from data: Data) throws -> Self {
        var message = Self()
        var reader = ProtoReader(data: data)
        while let field = try reader.nextField() {
            switch field.number {
            case 1:
                message.requestId = try reader.readString(wireType: field.wireType)
            case 2:
                message.method = try reader.readString(wireType: field.wireType)
            case 17:
                message.size = try reader.readString(wireType: field.wireType)
            case 3:
                message.objectId = try reader.readString(wireType: field.wireType)
            case 4:
                message.streamId = try reader.readString(wireType: field.wireType)
            case 5:
                message.timestampMs = try reader.readUInt64(wireType: field.wireType)
            case 6:
                message.statusCode = try reader.readUInt32(wireType: field.wireType)
            case 7:
                message.errorMessage = try reader.readString(wireType: field.wireType)
            case 8:
                message.workerCount = try reader.readUInt32(wireType: field.wireType)
            case 9:
                message.clientCount = try reader.readUInt32(wireType: field.wireType)
            case 10:
                message.clientId = try reader.readString(wireType: field.wireType)
            case 11:
                message.iterationCount = try reader.readUInt32(wireType: field.wireType)
            case 12:
                message.gemvPayload = try RuntimeGemvResponsePayload.decode(from: reader.readBytes(wireType: field.wireType))
            case 13:
                message.conv2dPayload = try RuntimeConv2dResponsePayload.decode(from: reader.readBytes(wireType: field.wireType))
            case 14:
                message.resultArtifact = try RuntimeArtifactDescriptor.decode(from: reader.readBytes(wireType: field.wireType))
            case 15:
                message.taskId = try reader.readString(wireType: field.wireType)
            case 16:
                message.elapsedMs = try reader.readUInt64(wireType: field.wireType)
            default:
                try reader.skipField(wireType: field.wireType)
            }
        }
        return message
    }
}

struct RuntimeEnvelope {
    var kind: MessageKind = .unspecified
    var heartbeat: RuntimeHeartbeat?
    var heartbeatOk: RuntimeHeartbeatOk?
    var clientJoin: RuntimeClientJoin?
    var clientRequest: RuntimeClientRequest?
    var clientResponse: RuntimeClientResponse?
    var clientInfoRequest: RuntimeClientInfoRequest?
    var clientInfoReply: RuntimeClientInfoReply?
    var clientRequestOk: RuntimeClientRequestOk?

    func encode() -> Data {
        var encoder = ProtoEncoder()
        encoder.appendVarintField(1, UInt64(kind.rawValue))
        if let heartbeat {
            encoder.appendMessageField(4, heartbeat.encode())
        }
        if let heartbeatOk {
            encoder.appendMessageField(5, heartbeatOk.encode())
        }
        if let clientJoin {
            encoder.appendMessageField(6, clientJoin.encode())
        }
        if let clientRequest {
            encoder.appendMessageField(7, clientRequest.encode())
        }
        if let clientInfoRequest {
            encoder.appendMessageField(15, clientInfoRequest.encode())
        }
        return encoder.data
    }

    static func decode(from data: Data) throws -> Self {
        var envelope = Self()
        var reader = ProtoReader(data: data)
        while let field = try reader.nextField() {
            switch field.number {
            case 1:
                envelope.kind = MessageKind(rawValue: Int(try reader.readUInt64(wireType: field.wireType))) ?? .unspecified
            case 4:
                envelope.heartbeat = try RuntimeHeartbeat.decode(from: reader.readBytes(wireType: field.wireType))
            case 5:
                // The iOS client only sends HEARTBEAT_OK and does not parse it back.
                _ = try reader.readBytes(wireType: field.wireType)
            case 6:
                _ = try reader.readBytes(wireType: field.wireType)
            case 7:
                _ = try reader.readBytes(wireType: field.wireType)
            case 8:
                envelope.clientResponse = try RuntimeClientResponse.decode(from: reader.readBytes(wireType: field.wireType))
            case 15:
                _ = try reader.readBytes(wireType: field.wireType)
            case 16:
                envelope.clientInfoReply = try RuntimeClientInfoReply.decode(from: reader.readBytes(wireType: field.wireType))
            case 17:
                envelope.clientRequestOk = try RuntimeClientRequestOk.decode(from: reader.readBytes(wireType: field.wireType))
            default:
                try reader.skipField(wireType: field.wireType)
            }
        }
        return envelope
    }
}
