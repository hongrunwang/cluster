import Foundation

enum AppRoute: Hashable {
    case methods
    case chat(ChatMethod)
    case downloads
}

enum ChatMethod: String, CaseIterable, Hashable, Identifiable {
    case gemv
    case conv2d
    case custom

    var id: String { rawValue }

    var title: String {
        switch self {
        case .gemv:
            return "gemv"
        case .conv2d:
            return "conv2d"
        case .custom:
            return "free_content / custom"
        }
    }

    var shortTitle: String {
        switch self {
        case .gemv:
            return "gemv"
        case .conv2d:
            return "conv2d"
        case .custom:
            return "custom"
        }
    }

    var subtitle: String {
        switch self {
        case .gemv:
            return "Deterministic 32K vector request with size and seed control."
        case .conv2d:
            return "Named spatial workload request with artifact download support."
        case .custom:
            return "UTF-8 content sender for free_content and custom methods."
        }
    }

    var symbolName: String {
        switch self {
        case .gemv:
            return "waveform.path.ecg.rectangle"
        case .conv2d:
            return "square.grid.3x3.square"
        case .custom:
            return "ellipsis.bubble"
        }
    }

    var accentHex: String {
        switch self {
        case .gemv:
            return "07C160"
        case .conv2d:
            return "FF9F0A"
        case .custom:
            return "5B667A"
        }
    }

    var entryHint: String {
        switch self {
        case .gemv:
            return "像联系人一样点进去，然后直接发固定向量请求。"
        case .conv2d:
            return "进入聊天页后配置 size、迭代次数和下载名。"
        case .custom:
            return "调试入口。底部输入 method 和内容即可发送。"
        }
    }
}

enum ChatBubbleRole: String, Codable {
    case system
    case outgoing
    case ack
    case incoming
}

struct ChatBubble: Identifiable, Hashable {
    let id: UUID
    let role: ChatBubbleRole
    let title: String
    let body: String
    let meta: String
    let createdAt: Date

    init(
        id: UUID = UUID(),
        role: ChatBubbleRole,
        title: String,
        body: String,
        meta: String = "",
        createdAt: Date = .now
    ) {
        self.id = id
        self.role = role
        self.title = title
        self.body = body
        self.meta = meta
        self.createdAt = createdAt
    }
}

struct DiscoveredNode: Codable, Hashable, Identifiable, Sendable {
    let host: String
    let port: Int
    let source: String
    let message: String

    var id: String { "\(host):\(port)" }
}

struct DiscoveryPayload: Codable, Sendable {
    var clientName: String
    var multicastGroup: String
    var udpPort: Int
    var discoveryTimeout: Double
    var discoveryAttempts: Int
    var retryDelay: Double
    var bufferSize: Int
    var multicastTTL: Int
}

struct DiscoveryResponse: Codable, Sendable {
    let results: [DiscoveredNode]
    let message: String
    var diagnostics: [String]? = nil
}

struct ConnectPayload: Codable, Sendable {
    var clientName: String
    var hostOverride: String?
    var portOverride: Int?
    var timeout: Double
    var multicastGroup: String
    var udpPort: Int
    var discoveryTimeout: Double
    var discoveryAttempts: Int
    var retryDelay: Double
    var bufferSize: Int
    var multicastTTL: Int
}

struct ConnectionSummary: Codable, Hashable, Sendable {
    let host: String
    let port: Int
    let source: String
    let clientId: String
    let workerCount: Int
    let clientCount: Int
    let statusCode: Int
}

struct BridgeHealth: Codable, Sendable {
    let status: String
    let connected: Bool
    let bridgeVersion: String
    let connection: ConnectionSummary?
    let closedReason: String?
}

struct MethodDescriptor: Codable, Hashable, Identifiable, Sendable {
    let method: String
    let title: String
    let subtitle: String

    var id: String { method }
}

struct ArtifactSummary: Codable, Hashable, Sendable {
    let artifactId: String
    let contentType: String
    let sizeBytes: Int
    let checksum: String
    let transferHost: String
    let transferPort: Int
}

struct RuntimeResponseSummary: Codable, Hashable, Sendable {
    let requestId: String
    let taskId: String
    let method: String
    let size: String
    let objectId: String
    let streamId: String
    let statusCode: Int
    let errorMessage: String
    let workerCount: Int
    let clientCount: Int
    let clientId: String
    let iterationCount: Int
    let elapsedMs: Int
}

struct RequestExecutionSummary: Codable, Hashable, Sendable {
    let acceptedTaskId: String
    let requestAcceptLatencyMs: Int
    let clientElapsedMs: Int
    let transmissionOverheadMs: Int
    let outputPreview: [Double]
    let outputText: String
    let outputByteCount: Int
    let savedOutputPath: String
    let artifactPath: String
    let artifactDownloaded: Bool
    let artifact: ArtifactSummary?
    let response: RuntimeResponseSummary
}

struct DownloadRecord: Hashable, Identifiable, Sendable {
    let name: String
    let path: String
    let sizeBytes: Int
    let modifiedAt: Date
    let sourceMethod: String

    var id: String { path }
    var fileURL: URL { URL(fileURLWithPath: path) }
}

struct DownloadListing: Hashable, Sendable {
    let directoryPath: String
    let records: [DownloadRecord]
}

struct GemvRequestDraft: Hashable {
    var size: String = "large"
    var vectorSeed: String = "0x0FEDCBA987654321"
    var iterationCount: String = "1"
    var objectId: String = ""
    var streamId: String = ""
}

struct Conv2DRequestDraft: Hashable {
    var size: String = "large"
    var localDownloadName: String = ""
    var iterationCount: String = "1"
    var objectId: String = ""
    var streamId: String = ""
}

struct CustomRequestDraft: Hashable {
    var method: String = "free_content"
    var content: String = "hello from SuperWebClientIOS"
    var iterationCount: String = "1"
    var objectId: String = ""
    var streamId: String = ""
}

struct GemvRequestPayload: Codable, Sendable {
    var clientName: String
    var size: String
    var vectorSeed: String
    var iterationCount: Int
    var objectId: String
    var streamId: String
}

struct Conv2DRequestPayload: Codable, Sendable {
    var clientName: String
    var size: String
    var localDownloadName: String
    var iterationCount: Int
    var objectId: String
    var streamId: String
}

struct CustomRequestPayload: Codable, Sendable {
    var clientName: String
    var method: String
    var content: String
    var iterationCount: Int
    var objectId: String
    var streamId: String
}
