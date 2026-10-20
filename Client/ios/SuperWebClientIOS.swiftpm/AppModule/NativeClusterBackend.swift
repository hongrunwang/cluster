import CryptoKit
import Foundation
import Network
import OSLog

enum NativeClientError: LocalizedError {
    case invalidSeed(String)
    case invalidHost(String)
    case notConnected
    case timedOut(String)
    case protocolViolation(String)
    case connectionClosed(String)
    case discoveryFailed(String)
    case artifactTransferFailed(String)
    case ioFailure(String)

    var errorDescription: String? {
        switch self {
        case .invalidSeed(let raw):
            return "Invalid vector seed: \(raw)"
        case .invalidHost(let raw):
            return "Invalid host: \(raw)"
        case .notConnected:
            return "No active cluster session. Connect first."
        case .timedOut(let message):
            return message
        case .protocolViolation(let message):
            return message
        case .connectionClosed(let message):
            return message
        case .discoveryFailed(let message):
            return message
        case .artifactTransferFailed(let message):
            return message
        case .ioFailure(let message):
            return message
        }
    }
}

private enum NativeConstants {
    static let defaultClientName = "ios superweb client"
    static let defaultTCPPort = 52020
    static let defaultConnectTimeout: TimeInterval = 30
    static let defaultArtifactTimeout: TimeInterval = 30
    static let maxProtobufMessageSize = 4 * 1024 * 1024
    static let clientInfoIntervalNanoseconds: UInt64 = 5_000_000_000
    static let clientInfoFailureLimit = 10
    static let requestPollIntervalNanoseconds: UInt64 = 50_000_000
    static let serviceType = "_superweb-cluster._tcp."
    static let serviceDomain = "local."
    static let methodGemv = "gemv"
    static let methodConv2d = "conv2d"
    static let methodFreeContent = "free_content"
    static let statusOK = 200
    static let defaultVectorSeed: UInt64 = 0x0FEDCBA987654321
}

private func nowMilliseconds() -> UInt64 {
    UInt64(Date().timeIntervalSince1970 * 1000)
}

private func normalizeRequestSize(_ raw: String?, default defaultSize: String = "large") -> String {
    let normalized = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    switch normalized {
    case "":
        return defaultSize
    case "medium":
        return "mid"
    case "test":
        return "small"
    case "runtime":
        return "large"
    default:
        return normalized
    }
}

private func vectorLength(for size: String?) -> Int {
    switch normalizeRequestSize(size) {
    case "small":
        return 4_096
    case "mid":
        return 16_384
    default:
        return 32_768
    }
}

private func parseVectorSeed(_ raw: String) throws -> UInt64 {
    let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    if text.isEmpty {
        return NativeConstants.defaultVectorSeed
    }
    if text.hasPrefix("0x") || text.hasPrefix("0X") {
        guard let value = UInt64(text.dropFirst(2), radix: 16) else {
            throw NativeClientError.invalidSeed(raw)
        }
        return value
    }
    guard let value = UInt64(text) else {
        throw NativeClientError.invalidSeed(raw)
    }
    return value
}

private let splitmixGamma: UInt64 = 0x9E3779B97F4A7C15
private let splitmixMix1: UInt64 = 0xBF58476D1CE4E5B9
private let splitmixMix2: UInt64 = 0x94D049BB133111EB

private func splitmix64(counter: Int, seed: UInt64) -> UInt64 {
    var z = seed &+ (UInt64(counter + 1) &* splitmixGamma)
    z = (z ^ (z >> 30)) &* splitmixMix1
    z = (z ^ (z >> 27)) &* splitmixMix2
    z ^= z >> 31
    return z
}

private func float32Word(from value: UInt64) -> UInt32 {
    let lower = UInt32(truncatingIfNeeded: value)
    let sign = (lower & 0x1) << 31
    let mantissa = (lower >> 1) & 0x007FFFFF
    return sign | 0x3F000000 | mantissa
}

private func generateRequestVectorData(seed: UInt64, size: String) -> Data {
    let count = vectorLength(for: size)
    var data = Data(capacity: count * 4)
    for index in 0..<count {
        var word = float32Word(from: splitmix64(counter: index, seed: seed)).littleEndian
        withUnsafeBytes(of: &word) { bytes in
            data.append(contentsOf: bytes)
        }
    }
    return data
}

private func unpackFloat32Bytes(_ data: Data) throws -> [Double] {
    guard data.count.isMultiple(of: 4) else {
        throw NativeClientError.protocolViolation("float32 payload byte length must be a multiple of 4")
    }
    var values: [Double] = []
    values.reserveCapacity(data.count / 4)
    for index in stride(from: 0, to: data.count, by: 4) {
        let word = data.subdata(in: index..<(index + 4)).withUnsafeBytes { rawBuffer in
            rawBuffer.load(as: UInt32.self)
        }
        let value = Float(bitPattern: UInt32(littleEndian: word))
        values.append(Double(value))
    }
    return values
}

private func conv2dPayload(for size: String) throws -> RuntimeConv2dRequestPayload {
    switch normalizeRequestSize(size) {
    case "small":
        return RuntimeConv2dRequestPayload(
            tensorH: 256,
            tensorW: 256,
            channelsIn: 32,
            channelsOut: 64,
            kernelSize: 3,
            padding: 1,
            stride: 1
        )
    case "mid":
        return RuntimeConv2dRequestPayload(
            tensorH: 1_024,
            tensorW: 1_024,
            channelsIn: 128,
            channelsOut: 256,
            kernelSize: 3,
            padding: 1,
            stride: 1
        )
    case "large":
        return RuntimeConv2dRequestPayload(
            tensorH: 2_048,
            tensorW: 2_048,
            channelsIn: 128,
            channelsOut: 256,
            kernelSize: 3,
            padding: 1,
            stride: 1
        )
    default:
        throw NativeClientError.protocolViolation("conv2d size must be 'small', 'mid', or 'large'")
    }
}

private func requestID(prefix: String) -> String {
    let suffix = UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(10)
    return "\(prefix)-\(suffix)"
}

private func safeUTF8Preview(_ data: Data) -> String {
    guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else {
        return ""
    }
    if text.count > 480 {
        return String(text.prefix(480)) + "..."
    }
    return text
}

private func sanitizeOutputComponent(_ raw: String, fallback: String) -> String {
    let invalid = CharacterSet(charactersIn: "<>:\"/\\|?*")
    var cleaned = ""
    for scalar in raw.unicodeScalars {
        if invalid.contains(scalar) {
            cleaned.append("-")
        } else {
            cleaned.append(String(scalar))
        }
    }
    let normalized = cleaned.trimmingCharacters(in: CharacterSet(charactersIn: " .-_"))
    return normalized.isEmpty ? fallback : normalized
}

private extension URL {
    func appendingUniquePathComponent(_ baseName: String, ext: String) -> URL {
        let initial = appendingPathComponent(baseName).appendingPathExtension(ext)
        if !FileManager.default.fileExists(atPath: initial.path) {
            return initial
        }
        for index in 1..<10_000 {
            let candidate = appendingPathComponent("\(baseName)-\(index)").appendingPathExtension(ext)
            if !FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
        }
        return initial
    }
}

private final class RawTCPConnection: @unchecked Sendable {
    private let connection: NWConnection
    private let queue = DispatchQueue(label: "superweb-client.native.connection")

    init(host: String, port: Int) throws {
        guard !host.isEmpty else {
            throw NativeClientError.invalidHost(host)
        }
        guard let nwPort = NWEndpoint.Port(rawValue: UInt16(port)) else {
            throw NativeClientError.protocolViolation("Invalid TCP port: \(port)")
        }
        connection = NWConnection(host: NWEndpoint.Host(host), port: nwPort, using: .tcp)
    }

    func start(timeout: TimeInterval) async throws {
        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask {
                try await self.waitUntilReady()
            }
            group.addTask {
                let nanoseconds = UInt64(max(timeout, 0.1) * 1_000_000_000)
                try await Task.sleep(nanoseconds: nanoseconds)
                throw NativeClientError.timedOut("Timed out connecting to the main node.")
            }
            connection.start(queue: queue)
            let _ = try await group.next()
            group.cancelAll()
        }
    }

    func cancel() {
        connection.cancel()
    }

    func send(_ data: Data) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            connection.send(content: data, completion: .contentProcessed { error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            })
        }
    }

    func receiveExactly(_ expectedLength: Int) async throws -> Data? {
        var buffer = Data()
        buffer.reserveCapacity(expectedLength)
        while buffer.count < expectedLength {
            let nextLength = expectedLength - buffer.count
            let chunk = try await receive(minimumIncompleteLength: 1, maximumLength: nextLength)
            guard let chunk else {
                if buffer.isEmpty {
                    return nil
                }
                throw NativeClientError.connectionClosed("peer closed the TCP session mid-frame")
            }
            buffer.append(chunk)
        }
        return buffer
    }

    private func waitUntilReady() async throws {
        try await withCheckedThrowingContinuation { continuation in
            let lock = NSLock()
            let stateBox = ConnectionReadyStateBox()
            connection.stateUpdateHandler = { state in
                lock.lock()
                defer { lock.unlock() }
                guard !stateBox.finished else {
                    return
                }
                switch state {
                case .ready:
                    stateBox.finished = true
                    self.connection.stateUpdateHandler = nil
                    continuation.resume()
                case .failed(let error):
                    stateBox.finished = true
                    self.connection.stateUpdateHandler = nil
                    continuation.resume(throwing: error)
                case .cancelled:
                    stateBox.finished = true
                    self.connection.stateUpdateHandler = nil
                    continuation.resume(throwing: NativeClientError.connectionClosed("TCP connection was cancelled."))
                default:
                    break
                }
            }
        }
    }

    private func receive(minimumIncompleteLength: Int, maximumLength: Int) async throws -> Data? {
        try await withCheckedThrowingContinuation { continuation in
            connection.receive(minimumIncompleteLength: minimumIncompleteLength, maximumLength: maximumLength) {
                data,
                _,
                isComplete,
                error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                if let data, !data.isEmpty {
                    continuation.resume(returning: data)
                    return
                }
                if isComplete {
                    continuation.resume(returning: nil)
                    return
                }
                continuation.resume(returning: Data())
            }
        }
    }
}

private final class ConnectionReadyStateBox: @unchecked Sendable {
    var finished = false
}

@MainActor
private struct BonjourDiscoverySnapshot: Sendable {
    let nodes: [DiscoveredNode]
    let diagnostics: [String]
}

private let bonjourLogger = Logger(subsystem: "com.superweb.client.iossim", category: "BonjourDiscovery")

@MainActor
private final class BonjourDiscoveryBrowser: NSObject, @preconcurrency NetServiceBrowserDelegate, @preconcurrency NetServiceDelegate {
    private var browser: NetServiceBrowser?
    private var discoveredNodes: [DiscoveredNode] = []
    private var services: [NetService] = []
    private var timeoutTask: Task<Void, Never>?
    private var continuation: CheckedContinuation<BonjourDiscoverySnapshot, Never>?
    private var didFinish = false
    private var browseTimeout: TimeInterval = 3
    private var diagnostics: [String] = []
    private var pendingResolutionKeys: Set<String> = []
    private var browseBatchCompleted = false

    func discover(timeout: TimeInterval) async -> BonjourDiscoverySnapshot {
        browseTimeout = timeout
        return await withCheckedContinuation { continuation in
            reset()
            self.continuation = continuation
            record("Starting Bonjour browse for \(NativeConstants.serviceType)\(NativeConstants.serviceDomain) with timeout=\(String(format: "%.1f", timeout))s.")
            let browser = NetServiceBrowser()
            browser.delegate = self
            self.browser = browser
            browser.searchForServices(ofType: NativeConstants.serviceType, inDomain: NativeConstants.serviceDomain)
            timeoutTask = Task { [weak self] in
                let delay = UInt64(max(timeout, 0.5) * 1_000_000_000)
                try? await Task.sleep(nanoseconds: delay)
                await MainActor.run {
                    self?.finish()
                }
            }
        }
    }

    func netServiceBrowserWillSearch(_ browser: NetServiceBrowser) {
        _ = browser
        record("Bonjour browse started.")
    }

    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        _ = browser
        services.append(service)
        service.delegate = self
        let key = serviceKey(for: service)
        pendingResolutionKeys.insert(key)
        record("Found Bonjour service \(key). Resolving endpoint details.")
        service.resolve(withTimeout: browseTimeout)
        if !moreComing {
            browseBatchCompleted = true
            record("Bonjour browse delivered the current batch. Waiting for \(pendingResolutionKeys.count) resolve result(s).")
            finishIfReady()
        }
    }

    func netServiceDidResolveAddress(_ sender: NetService) {
        let key = serviceKey(for: sender)
        pendingResolutionKeys.remove(key)
        let resolvedHosts = sender.addresses?.compactMap(resolveHost(from:)) ?? []
        let host = resolvedHosts.first ?? normalizedHostName(sender.hostName)
        guard let host else {
            record("Resolved \(key), but no usable host was returned from addresses or hostName.")
            finishIfReady()
            return
        }
        let node = DiscoveredNode(
            host: host,
            port: sender.port,
            source: "bonjour",
            message: "Resolved main-node Bonjour service \(sender.name) at \(host):\(sender.port)."
        )
        if !discoveredNodes.contains(node) {
            discoveredNodes.append(node)
        }
        if resolvedHosts.isEmpty, let hostName = sender.hostName {
            record("Resolved \(key) via hostName fallback \(hostName) -> \(host):\(sender.port).")
        } else {
            record("Resolved \(key) to \(host):\(sender.port).")
        }
        finishIfReady()
    }

    func netService(_ sender: NetService, didNotResolve errorDict: [String : NSNumber]) {
        pendingResolutionKeys.remove(serviceKey(for: sender))
        record("Failed to resolve \(serviceKey(for: sender)): \(format(errorDict: errorDict)).")
        finishIfReady()
    }

    func netServiceBrowserDidStopSearch(_ browser: NetServiceBrowser) {
        _ = browser
        record("Bonjour browse stopped.")
        finish()
    }

    func netServiceBrowser(_ browser: NetServiceBrowser, didNotSearch errorDict: [String : NSNumber]) {
        _ = browser
        record("Bonjour browse failed: \(format(errorDict: errorDict)).")
        finish()
    }

    private func finish() {
        guard !didFinish else {
            return
        }
        didFinish = true
        timeoutTask?.cancel()
        browser?.stop()
        browser = nil
        continuation?.resume(returning: BonjourDiscoverySnapshot(nodes: discoveredNodes, diagnostics: diagnostics))
        continuation = nil
    }

    private func finishIfReady() {
        guard browseBatchCompleted, pendingResolutionKeys.isEmpty else {
            return
        }
        finish()
    }

    private func reset() {
        discoveredNodes = []
        services = []
        timeoutTask?.cancel()
        timeoutTask = nil
        didFinish = false
        diagnostics = []
        pendingResolutionKeys = []
        browseBatchCompleted = false
    }

    private func serviceKey(for service: NetService) -> String {
        let type = service.type.isEmpty ? NativeConstants.serviceType : service.type
        let domain = service.domain.isEmpty ? NativeConstants.serviceDomain : service.domain
        return "\(service.name).\(type)\(domain)"
    }

    private func normalizedHostName(_ raw: String?) -> String? {
        guard let raw else {
            return nil
        }
        let trimmed = raw.trimmingCharacters(in: CharacterSet(charactersIn: ".").union(.whitespacesAndNewlines))
        return trimmed.isEmpty ? nil : trimmed
    }

    private func format(errorDict: [String : NSNumber]) -> String {
        guard !errorDict.isEmpty else {
            return "unknown error"
        }
        return errorDict
            .sorted { $0.key < $1.key }
            .map { "\($0.key)=\($0.value)" }
            .joined(separator: ", ")
    }

    private func record(_ message: String) {
        diagnostics.append(message)
        bonjourLogger.info("\(message)")
    }

    private func resolveHost(from data: Data) -> String? {
        data.withUnsafeBytes { rawBuffer in
            guard let baseAddress = rawBuffer.baseAddress else {
                return nil
            }
            let sockaddrPointer = baseAddress.assumingMemoryBound(to: sockaddr.self)
            switch Int32(sockaddrPointer.pointee.sa_family) {
            case AF_INET:
                let ipv4Pointer = baseAddress.assumingMemoryBound(to: sockaddr_in.self)
                var address = ipv4Pointer.pointee.sin_addr
                var buffer = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
                guard inet_ntop(AF_INET, &address, &buffer, socklen_t(INET_ADDRSTRLEN)) != nil else {
                    return nil
                }
                return String(decoding: buffer.prefix { $0 != 0 }.map(UInt8.init(bitPattern:)), as: UTF8.self)
            case AF_INET6:
                let ipv6Pointer = baseAddress.assumingMemoryBound(to: sockaddr_in6.self)
                var address = ipv6Pointer.pointee.sin6_addr
                var buffer = [CChar](repeating: 0, count: Int(INET6_ADDRSTRLEN))
                guard inet_ntop(AF_INET6, &address, &buffer, socklen_t(INET6_ADDRSTRLEN)) != nil else {
                    return nil
                }
                return String(decoding: buffer.prefix { $0 != 0 }.map(UInt8.init(bitPattern:)), as: UTF8.self)
            default:
                return nil
            }
        }
    }
}

private struct NativeRequestResult {
    let response: RuntimeClientResponse
    let outputValues: [Double]
    let outputBytes: Data
    let acceptedTaskId: String
    let requestAcceptLatencyMs: Int
    let clientElapsedMs: Int
    let transmissionOverheadMs: Int
    let savedOutputURL: URL?
    let artifactURL: URL?
    let artifactDownloaded: Bool
}

private actor ClusterSession {
    private let connection: RawTCPConnection
    private let maxMessageSize: Int
    private let artifactTimeout: TimeInterval
    private let artifactDownloadDirectory: URL

    private var clientName = NativeConstants.defaultClientName
    private(set) var clientId = ""
    private(set) var closedReason: String?
    private var responseQueue: [RuntimeClientResponse] = []
    private var requestOkQueueByLocalID: [String: [RuntimeClientRequestOk]] = [:]
    private var awaitingRequestOkLocalIDs: [String] = []
    private var pendingTaskIDs: Set<String> = []
    private var localRequestIDByTaskID: [String: String] = [:]
    private var preferredOutputNameByLocalRequestID: [String: String] = [:]
    private var preferredOutputNameByTaskID: [String: String] = [:]
    private var infoPollFailures = 0
    private var infoRequestInFlightMs: UInt64?
    private var systemDead = false
    private var readerTask: Task<Void, Never>?
    private var infoPollTask: Task<Void, Never>?

    static func connect(
        host: String,
        port: Int,
        timeout: TimeInterval,
        artifactDownloadDirectory: URL
    ) async throws -> ClusterSession {
        let connection = try RawTCPConnection(host: host, port: port)
        try await connection.start(timeout: timeout)
        let session = ClusterSession(
            connection: connection,
            maxMessageSize: NativeConstants.maxProtobufMessageSize,
            artifactTimeout: NativeConstants.defaultArtifactTimeout,
            artifactDownloadDirectory: artifactDownloadDirectory
        )
        await session.startReaderLoop()
        return session
    }

    init(
        connection: RawTCPConnection,
        maxMessageSize: Int,
        artifactTimeout: TimeInterval,
        artifactDownloadDirectory: URL
    ) {
        self.connection = connection
        self.maxMessageSize = maxMessageSize
        self.artifactTimeout = artifactTimeout
        self.artifactDownloadDirectory = artifactDownloadDirectory
    }

    func snapshot() -> (connected: Bool, clientId: String, closedReason: String?) {
        (
            closedReason == nil && !systemDead,
            clientId,
            closedReason
        )
    }

    func close(reason: String? = nil) {
        if closedReason == nil {
            closedReason = reason ?? "client session closed"
        }
        readerTask?.cancel()
        infoPollTask?.cancel()
        connection.cancel()
    }

    func join(clientName: String) async throws -> RuntimeClientResponse {
        try ensureAlive()
        self.clientName = clientName
        let envelope = RuntimeEnvelope(
            kind: .clientJoin,
            heartbeat: nil,
            heartbeatOk: nil,
            clientJoin: RuntimeClientJoin(clientName: clientName),
            clientRequest: nil,
            clientResponse: nil,
            clientInfoRequest: nil,
            clientInfoReply: nil,
            clientRequestOk: nil
        )
        try await sendEnvelope(envelope)
        let response = try await waitForClientResponse(description: "client join response")
        guard Int(response.statusCode) == NativeConstants.statusOK else {
            throw NativeClientError.protocolViolation(response.errorMessage.isEmpty ? "CLIENT_JOIN failed." : response.errorMessage)
        }
        clientId = response.clientId
        return response
    }

    func requestGemv(
        clientName: String,
        requestId: String,
        vectorData: Data,
        size: String,
        iterationCount: Int,
        objectId: String,
        streamId: String
    ) async throws -> NativeRequestResult {
        let normalizedSize = normalizeRequestSize(size)
        let payload = RuntimeGemvRequestPayload(
            vectorLength: UInt32(vectorLength(for: normalizedSize)),
            vectorData: vectorData
        )
        return try await requestPayload(
            clientName: clientName,
            requestId: requestId,
            method: NativeConstants.methodGemv,
            size: normalizedSize,
            objectId: objectId,
            preferredOutputName: "",
            streamId: streamId,
            iterationCount: iterationCount,
            requestEnvelopePayload: .gemv(payload),
            decodeOutputAsFloat32: true
        )
    }

    func requestConv2d(
        clientName: String,
        requestId: String,
        size: String,
        objectId: String?,
        localDownloadName: String?,
        iterationCount: Int,
        streamId: String
    ) async throws -> NativeRequestResult {
        let normalizedSize = normalizeRequestSize(size)
        let payload = try conv2dPayload(for: normalizedSize)
        return try await requestPayload(
            clientName: clientName,
            requestId: requestId,
            method: NativeConstants.methodConv2d,
            size: normalizedSize,
            objectId: objectId?.isEmpty == false ? objectId ?? "" : "conv2d/\(normalizedSize)",
            preferredOutputName: localDownloadName ?? "",
            streamId: streamId,
            iterationCount: iterationCount,
            requestEnvelopePayload: .conv2d(payload),
            decodeOutputAsFloat32: false
        )
    }

    func requestFreeContent(
        clientName: String,
        requestId: String,
        content: String,
        method: String,
        iterationCount: Int,
        objectId: String,
        streamId: String
    ) async throws -> NativeRequestResult {
        let payloadData = Data(content.utf8)
        let payload = RuntimeGemvRequestPayload(vectorLength: UInt32(payloadData.count), vectorData: payloadData)
        return try await requestPayload(
            clientName: clientName,
            requestId: requestId,
            method: method,
            size: "",
            objectId: objectId,
            preferredOutputName: "",
            streamId: streamId,
            iterationCount: iterationCount,
            requestEnvelopePayload: .gemv(payload),
            decodeOutputAsFloat32: false
        )
    }

    private func requestPayload(
        clientName: String,
        requestId: String,
        method: String,
        size: String,
        objectId: String,
        preferredOutputName: String,
        streamId: String,
        iterationCount: Int,
        requestEnvelopePayload: RequestEnvelopePayload,
        decodeOutputAsFloat32: Bool
    ) async throws -> NativeRequestResult {
        try ensureAlive()
        self.clientName = clientName
        let localRequestId = requestId
        let requestStartedAt = DispatchTime.now().uptimeNanoseconds
        awaitingRequestOkLocalIDs.append(localRequestId)
        if !preferredOutputName.isEmpty {
            preferredOutputNameByLocalRequestID[localRequestId] = preferredOutputName
        }

        do {
            let request = RuntimeClientRequest(
                requestId: "",
                clientName: clientName,
                method: method,
                size: size,
                objectId: objectId,
                streamId: streamId,
                timestampMs: nowMilliseconds(),
                iterationCount: UInt32(max(iterationCount, 1)),
                gemvPayload: requestEnvelopePayload.gemvPayload,
                conv2dPayload: requestEnvelopePayload.conv2dPayload
            )
            let envelope = RuntimeEnvelope(
                kind: .clientRequest,
                heartbeat: nil,
                heartbeatOk: nil,
                clientJoin: nil,
                clientRequest: request,
                clientResponse: nil,
                clientInfoRequest: nil,
                clientInfoReply: nil,
                clientRequestOk: nil
            )
            try await sendEnvelope(envelope)
        } catch {
            awaitingRequestOkLocalIDs.removeAll { $0 == localRequestId }
            preferredOutputNameByLocalRequestID.removeValue(forKey: localRequestId)
            throw error
        }

        do {
            let requestOk = try await waitForClientRequestOk(localRequestId: localRequestId)
            let acceptedAt = DispatchTime.now().uptimeNanoseconds
            markTaskStarted(localRequestId: localRequestId, taskId: requestOk.taskId)
            let response = try await waitForClientResponse(description: "client response")
            let taskId = response.taskId.isEmpty ? requestOk.taskId : response.taskId
            let outputMaterialization = try await materializeResponseOutput(response)
            if !taskId.isEmpty {
                markTaskFinished(taskId: taskId)
            }
            let completedAt = DispatchTime.now().uptimeNanoseconds
            let outputValues = decodeOutputAsFloat32 && !outputMaterialization.outputBytes.isEmpty ? try unpackFloat32Bytes(outputMaterialization.outputBytes) : []
            let acceptLatency = Int((acceptedAt - requestStartedAt) / 1_000_000)
            let clientElapsed = Int((completedAt - acceptedAt) / 1_000_000)
            let transmissionOverhead = max(0, clientElapsed - Int(response.elapsedMs))
            return NativeRequestResult(
                response: response,
                outputValues: outputValues,
                outputBytes: outputMaterialization.outputBytes,
                acceptedTaskId: requestOk.taskId,
                requestAcceptLatencyMs: max(0, acceptLatency),
                clientElapsedMs: max(0, clientElapsed),
                transmissionOverheadMs: transmissionOverhead,
                savedOutputURL: outputMaterialization.savedURL,
                artifactURL: outputMaterialization.artifactURL,
                artifactDownloaded: outputMaterialization.artifactDownloaded
            )
        } catch {
            preferredOutputNameByLocalRequestID.removeValue(forKey: localRequestId)
            throw error
        }
    }

    private func startReaderLoop() {
        readerTask = Task {
            await self.readerLoop()
        }
    }

    private func readerLoop() async {
        while !Task.isCancelled {
            do {
                guard let envelope = try await receiveEnvelope() else {
                    close(reason: "main node closed the TCP session")
                    return
                }
                try await handleIncomingEnvelope(envelope)
            } catch {
                close(reason: error.localizedDescription)
                return
            }
        }
    }

    private func handleIncomingEnvelope(_ envelope: RuntimeEnvelope) async throws {
        switch envelope.kind {
        case .heartbeat:
            guard let heartbeat = envelope.heartbeat else {
                throw NativeClientError.protocolViolation("Received HEARTBEAT without payload.")
            }
            let response = RuntimeEnvelope(
                kind: .heartbeatOk,
                heartbeat: nil,
                heartbeatOk: RuntimeHeartbeatOk(
                    nodeName: clientName,
                    heartbeatUnixTimeMs: heartbeat.unixTimeMs,
                    receivedUnixTimeMs: nowMilliseconds(),
                    activeTaskIds: Array(pendingTaskIDs)
                ),
                clientJoin: nil,
                clientRequest: nil,
                clientResponse: nil,
                clientInfoRequest: nil,
                clientInfoReply: nil,
                clientRequestOk: nil
            )
            try await sendEnvelope(response)
        case .clientResponse:
            guard let response = envelope.clientResponse else {
                throw NativeClientError.protocolViolation("Received CLIENT_RESPONSE without payload.")
            }
            responseQueue.append(response)
        case .clientRequestOk:
            guard let requestOk = envelope.clientRequestOk else {
                throw NativeClientError.protocolViolation("Received CLIENT_REQUEST_OK without payload.")
            }
            guard !awaitingRequestOkLocalIDs.isEmpty else {
                throw NativeClientError.protocolViolation("Unexpected client request acknowledgement without a pending request.")
            }
            let localRequestId = awaitingRequestOkLocalIDs.removeFirst()
            requestOkQueueByLocalID[localRequestId, default: []].append(requestOk)
        case .clientInfoReply:
            guard let infoReply = envelope.clientInfoReply else {
                throw NativeClientError.protocolViolation("Received CLIENT_INFO_REPLY without payload.")
            }
            infoPollFailures = 0
            if infoRequestInFlightMs == infoReply.requestTimestampMs {
                infoRequestInFlightMs = nil
            }
        default:
            throw NativeClientError.protocolViolation("Unexpected runtime message: \(envelope.kind)")
        }
    }

    private func receiveEnvelope() async throws -> RuntimeEnvelope? {
        guard let header = try await connection.receiveExactly(4) else {
            return nil
        }
        let payloadSize = header.withUnsafeBytes { rawBuffer in
            rawBuffer.load(as: UInt32.self).bigEndian
        }
        guard payloadSize > 0, payloadSize <= maxMessageSize else {
            throw NativeClientError.protocolViolation("invalid protobuf message size: \(payloadSize)")
        }
        guard let payload = try await connection.receiveExactly(Int(payloadSize)) else {
            return nil
        }
        return try RuntimeEnvelope.decode(from: payload)
    }

    private func sendEnvelope(_ envelope: RuntimeEnvelope) async throws {
        try ensureAlive()
        let payload = envelope.encode()
        var frameLength = UInt32(payload.count).bigEndian
        let header = withUnsafeBytes(of: &frameLength) { Data($0) }
        try await connection.send(header + payload)
    }

    private func ensureAlive() throws {
        if systemDead {
            throw NativeClientError.connectionClosed("main node info polling failed repeatedly; treating the system as dead")
        }
        if let closedReason {
            throw NativeClientError.connectionClosed(closedReason)
        }
    }

    private func waitForClientResponse(description: String) async throws -> RuntimeClientResponse {
        while true {
            try ensureAlive()
            if !responseQueue.isEmpty {
                return responseQueue.removeFirst()
            }
            _ = description
            try await Task.sleep(nanoseconds: NativeConstants.requestPollIntervalNanoseconds)
        }
    }

    private func waitForClientRequestOk(localRequestId: String) async throws -> RuntimeClientRequestOk {
        while true {
            try ensureAlive()
            if var queue = requestOkQueueByLocalID[localRequestId], !queue.isEmpty {
                let message = queue.removeFirst()
                if queue.isEmpty {
                    requestOkQueueByLocalID.removeValue(forKey: localRequestId)
                } else {
                    requestOkQueueByLocalID[localRequestId] = queue
                }
                return message
            }
            try await Task.sleep(nanoseconds: NativeConstants.requestPollIntervalNanoseconds)
        }
    }

    private func startInfoPollingIfNeeded() {
        guard infoPollTask == nil else {
            return
        }
        infoPollTask = Task {
            await self.clientInfoPollLoop()
        }
    }

    private func clientInfoPollLoop() async {
        defer {
            infoPollTask = nil
        }
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: NativeConstants.clientInfoIntervalNanoseconds)
            if Task.isCancelled {
                return
            }
            let shouldClose = await performInfoPollTick()
            if shouldClose {
                return
            }
        }
    }

    private func performInfoPollTick() async -> Bool {
        if clientId.isEmpty || pendingTaskIDs.isEmpty {
            infoRequestInFlightMs = nil
            infoPollFailures = 0
            return false
        }
        if infoRequestInFlightMs != nil {
            infoPollFailures += 1
            infoRequestInFlightMs = nil
        }
        if infoPollFailures >= NativeConstants.clientInfoFailureLimit {
            systemDead = true
            close(reason: "client info polling failed \(NativeConstants.clientInfoFailureLimit) consecutive times")
            return true
        }
        let timestamp = nowMilliseconds()
        infoRequestInFlightMs = timestamp
        do {
            let request = RuntimeEnvelope(
                kind: .clientInfoRequest,
                heartbeat: nil,
                heartbeatOk: nil,
                clientJoin: nil,
                clientRequest: nil,
                clientResponse: nil,
                clientInfoRequest: RuntimeClientInfoRequest(clientId: clientId, clientName: clientName, timestampMs: timestamp),
                clientInfoReply: nil,
                clientRequestOk: nil
            )
            try await sendEnvelope(request)
        } catch {
            infoRequestInFlightMs = nil
            infoPollFailures += 1
            if infoPollFailures >= NativeConstants.clientInfoFailureLimit {
                systemDead = true
                close(reason: "client info polling failed \(NativeConstants.clientInfoFailureLimit) consecutive times")
                return true
            }
        }
        return false
    }

    private func markTaskStarted(localRequestId: String, taskId: String) {
        pendingTaskIDs.insert(taskId)
        localRequestIDByTaskID[taskId] = localRequestId
        if let preferredName = preferredOutputNameByLocalRequestID.removeValue(forKey: localRequestId), !preferredName.isEmpty {
            preferredOutputNameByTaskID[taskId] = preferredName
        }
        startInfoPollingIfNeeded()
    }

    private func markTaskFinished(taskId: String) {
        pendingTaskIDs.remove(taskId)
        localRequestIDByTaskID.removeValue(forKey: taskId)
        preferredOutputNameByTaskID.removeValue(forKey: taskId)
        if pendingTaskIDs.isEmpty {
            infoRequestInFlightMs = nil
            infoPollFailures = 0
        }
    }

    private func resolveLocalRequestID(_ response: RuntimeClientResponse) -> String {
        let taskId = response.taskId.isEmpty ? response.requestId : response.taskId
        if let localRequestID = localRequestIDByTaskID[taskId], !localRequestID.isEmpty {
            return localRequestID
        }
        if !response.requestId.isEmpty {
            return response.requestId
        }
        if !response.taskId.isEmpty {
            return response.taskId
        }
        return "response"
    }

    private func resolvePreferredOutputName(_ response: RuntimeClientResponse) -> String {
        let taskId = response.taskId.isEmpty ? response.requestId : response.taskId
        return preferredOutputNameByTaskID[taskId] ?? ""
    }

    private func buildOutputURL(response: RuntimeClientResponse, contentType: String? = nil) throws -> URL {
        try FileManager.default.createDirectory(at: artifactDownloadDirectory, withIntermediateDirectories: true, attributes: nil)
        let preferredOutputName = resolvePreferredOutputName(response)
        let defaultExtension = ((contentType ?? "").hasPrefix("text/") || response.method == NativeConstants.methodFreeContent) ? "txt" : "bin"

        let baseName: String
        let ext: String
        if !preferredOutputName.isEmpty {
            let leaf = URL(fileURLWithPath: preferredOutputName).lastPathComponent
            let suffix = URL(fileURLWithPath: leaf).pathExtension.lowercased()
            if suffix == "bin" || suffix == "txt" {
                baseName = sanitizeOutputComponent(URL(fileURLWithPath: leaf).deletingPathExtension().lastPathComponent, fallback: "response")
                ext = suffix
            } else {
                baseName = sanitizeOutputComponent(leaf, fallback: "response")
                ext = defaultExtension
            }
        } else {
            let methodComponent = sanitizeOutputComponent(response.method.isEmpty ? "response" : response.method, fallback: "response")
            let requestComponent = sanitizeOutputComponent(resolveLocalRequestID(response), fallback: "response")
            baseName = "\(methodComponent)-\(requestComponent)"
            ext = defaultExtension
        }
        return artifactDownloadDirectory.appendingUniquePathComponent(baseName, ext: ext)
    }

    private func materializeResponseOutput(
        _ response: RuntimeClientResponse
    ) async throws -> (outputBytes: Data, savedURL: URL?, artifactURL: URL?, artifactDownloaded: Bool) {
        if let descriptor = response.resultArtifact {
            let destination = try buildOutputURL(response: response, contentType: descriptor.contentType)
            try await fetchArtifact(descriptor: descriptor, destinationURL: destination, timeout: artifactTimeout)
            let outputBytes: Data
            if descriptor.sizeBytes <= 16 * 1024 * 1024 {
                outputBytes = try Data(contentsOf: destination)
            } else {
                outputBytes = Data()
            }
            return (outputBytes, destination, destination, true)
        }

        let outputBytes = response.outputVector
        guard !outputBytes.isEmpty else {
            return (Data(), nil, nil, false)
        }
        if response.method == NativeConstants.methodFreeContent {
            return (outputBytes, nil, nil, false)
        }
        let destination = try buildOutputURL(response: response)
        try outputBytes.write(to: destination, options: .atomic)
        return (outputBytes, destination, nil, false)
    }

    private func fetchArtifact(
        descriptor: RuntimeArtifactDescriptor,
        destinationURL: URL,
        timeout: TimeInterval
    ) async throws {
        let connection = try RawTCPConnection(host: descriptor.transferHost, port: Int(descriptor.transferPort))
        try await connection.start(timeout: timeout)
        defer {
            connection.cancel()
        }

        let request = ArtifactDataPlane.encodeRequest(artifactId: descriptor.artifactId)
        try await connection.send(request)

        guard let firstHeader = try await connection.receiveExactly(6) else {
            throw NativeClientError.artifactTransferFailed("Artifact server closed before INIT.")
        }
        let messageType = firstHeader[firstHeader.index(firstHeader.startIndex, offsetBy: 5)]
        let expectedChecksum: String
        let chunkSize: Int
        if messageType == ArtifactDataPlane.messageInit {
            guard let remainingHeader = try await connection.receiveExactly(ArtifactDataPlane.initHeaderLength - 6) else {
                throw NativeClientError.artifactTransferFailed("Artifact INIT header was truncated.")
            }
            let initHeader = firstHeader + remainingHeader
            let initPayloadLength = try ArtifactDataPlane.initPayloadLength(from: initHeader)
            guard let initPayload = try await connection.receiveExactly(initPayloadLength) else {
                throw NativeClientError.artifactTransferFailed("Artifact INIT payload was truncated.")
            }
            let decoded = try ArtifactDataPlane.decodeInit(header: initHeader, payload: initPayload)
            expectedChecksum = decoded.checksum
            chunkSize = decoded.chunkSize
        } else if messageType == ArtifactDataPlane.messageError {
            guard let remainingHeader = try await connection.receiveExactly(ArtifactDataPlane.errorHeaderLength - 6) else {
                throw NativeClientError.artifactTransferFailed("Artifact ERROR header was truncated.")
            }
            let errorHeader = firstHeader + remainingHeader
            let length = try ArtifactDataPlane.errorPayloadLength(from: errorHeader)
            guard let payload = try await connection.receiveExactly(length) else {
                throw NativeClientError.artifactTransferFailed("Artifact ERROR payload was truncated.")
            }
            throw NativeClientError.artifactTransferFailed(try ArtifactDataPlane.decodeError(header: errorHeader, payload: payload))
        } else {
            throw NativeClientError.artifactTransferFailed("Unexpected first data-plane message type.")
        }

        let tmpURL = destinationURL.appendingPathExtension("tmp")
        try FileManager.default.createDirectory(at: destinationURL.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: nil)
        FileManager.default.createFile(atPath: tmpURL.path, contents: nil)
        let handle = try FileHandle(forWritingTo: tmpURL)
        defer {
            try? handle.close()
        }

        var digest = SHA256Digest()
        var bytesWritten: UInt64 = 0

        while true {
            guard let header = try await connection.receiveExactly(6) else {
                throw NativeClientError.artifactTransferFailed("Artifact server closed before END.")
            }
            let messageType = header[header.index(header.startIndex, offsetBy: 5)]
            if messageType == ArtifactDataPlane.messageChunk {
                guard let remainingHeader = try await connection.receiveExactly(ArtifactDataPlane.chunkHeaderLength - 6) else {
                    throw NativeClientError.artifactTransferFailed("Artifact CHUNK header was truncated.")
                }
                let chunkHeader = header + remainingHeader
                let length = try ArtifactDataPlane.chunkPayloadLength(from: chunkHeader)
                guard let payload = try await connection.receiveExactly(length) else {
                    throw NativeClientError.artifactTransferFailed("Artifact CHUNK payload was truncated.")
                }
                let decoded = try ArtifactDataPlane.decodeChunk(header: chunkHeader, payload: payload)
                _ = decoded.offset
                try handle.write(contentsOf: decoded.data)
                digest.update(decoded.data)
                bytesWritten += UInt64(decoded.data.count)
            } else if messageType == ArtifactDataPlane.messageEnd {
                guard let remainingHeader = try await connection.receiveExactly(ArtifactDataPlane.endHeaderLength - 6) else {
                    throw NativeClientError.artifactTransferFailed("Artifact END header was truncated.")
                }
                let endHeader = header + remainingHeader
                let totalSize = try ArtifactDataPlane.decodeEnd(header: endHeader)
                if totalSize != bytesWritten {
                    throw NativeClientError.artifactTransferFailed("artifact end size mismatch")
                }
                break
            } else if messageType == ArtifactDataPlane.messageError {
                guard let remainingHeader = try await connection.receiveExactly(ArtifactDataPlane.errorHeaderLength - 6) else {
                    throw NativeClientError.artifactTransferFailed("Artifact ERROR header was truncated.")
                }
                let errorHeader = header + remainingHeader
                let length = try ArtifactDataPlane.errorPayloadLength(from: errorHeader)
                guard let payload = try await connection.receiveExactly(length) else {
                    throw NativeClientError.artifactTransferFailed("Artifact ERROR payload was truncated.")
                }
                throw NativeClientError.artifactTransferFailed(try ArtifactDataPlane.decodeError(header: errorHeader, payload: payload))
            } else {
                throw NativeClientError.artifactTransferFailed("Unexpected data-plane message type.")
            }
        }

        let checksum = digest.finalizeHex()
        if checksum != expectedChecksum {
            throw NativeClientError.artifactTransferFailed("artifact checksum mismatch")
        }
        if bytesWritten != descriptor.sizeBytes {
            throw NativeClientError.artifactTransferFailed("artifact size mismatch")
        }
        try? FileManager.default.removeItem(at: destinationURL)
        try FileManager.default.moveItem(at: tmpURL, to: destinationURL)
        _ = chunkSize
    }
}

private enum RequestEnvelopePayload {
    case gemv(RuntimeGemvRequestPayload)
    case conv2d(RuntimeConv2dRequestPayload)

    var gemvPayload: RuntimeGemvRequestPayload? {
        if case .gemv(let payload) = self {
            return payload
        }
        return nil
    }

    var conv2dPayload: RuntimeConv2dRequestPayload? {
        if case .conv2d(let payload) = self {
            return payload
        }
        return nil
    }
}

private enum ArtifactDataPlane {
    static let magic = Data("SWAD".utf8)
    static let protocolVersion: UInt8 = 1
    static let messageRequest: UInt8 = 1
    static let messageInit: UInt8 = 2
    static let messageChunk: UInt8 = 3
    static let messageEnd: UInt8 = 4
    static let messageError: UInt8 = 5

    static let requestHeaderLength = 10
    static let initHeaderLength = 26
    static let chunkHeaderLength = 18
    static let endHeaderLength = 14
    static let errorHeaderLength = 10

    static func encodeRequest(artifactId: String) -> Data {
        let payload = Data(artifactId.utf8)
        var data = Data()
        data.append(magic)
        data.append(protocolVersion)
        data.append(messageRequest)
        var length = UInt32(payload.count).bigEndian
        withUnsafeBytes(of: &length) { bytes in
            data.append(contentsOf: bytes)
        }
        data.append(payload)
        return data
    }

    static func initPayloadLength(from header: Data) throws -> Int {
        let checksumLength = try readUInt32(header, offset: 18)
        let contentTypeLength = try readUInt32(header, offset: 22)
        return Int(checksumLength + contentTypeLength)
    }

    static func decodeInit(header: Data, payload: Data) throws -> (sizeBytes: UInt64, chunkSize: Int, checksum: String, contentType: String) {
        try validatePrefix(header: header, messageType: messageInit)
        let sizeBytes = try readUInt64(header, offset: 6)
        let chunkSize = Int(try readUInt32(header, offset: 14))
        let checksumLength = Int(try readUInt32(header, offset: 18))
        let contentTypeLength = Int(try readUInt32(header, offset: 22))
        guard payload.count == checksumLength + contentTypeLength else {
            throw NativeClientError.artifactTransferFailed("artifact init payload length mismatch")
        }
        let checksum = String(decoding: payload.prefix(checksumLength), as: UTF8.self)
        let contentType = String(decoding: payload.dropFirst(checksumLength), as: UTF8.self)
        return (sizeBytes, chunkSize, checksum, contentType)
    }

    static func chunkPayloadLength(from header: Data) throws -> Int {
        Int(try readUInt32(header, offset: 14))
    }

    static func decodeChunk(header: Data, payload: Data) throws -> (offset: UInt64, data: Data) {
        try validatePrefix(header: header, messageType: messageChunk)
        let offset = try readUInt64(header, offset: 6)
        let length = Int(try readUInt32(header, offset: 14))
        guard payload.count == length else {
            throw NativeClientError.artifactTransferFailed("artifact chunk payload length mismatch")
        }
        return (offset, payload)
    }

    static func decodeEnd(header: Data) throws -> UInt64 {
        try validatePrefix(header: header, messageType: messageEnd)
        return try readUInt64(header, offset: 6)
    }

    static func errorPayloadLength(from header: Data) throws -> Int {
        Int(try readUInt32(header, offset: 6))
    }

    static func decodeError(header: Data, payload: Data) throws -> String {
        try validatePrefix(header: header, messageType: messageError)
        let length = try errorPayloadLength(from: header)
        guard payload.count == length else {
            throw NativeClientError.artifactTransferFailed("artifact error payload length mismatch")
        }
        return String(decoding: payload, as: UTF8.self)
    }

    private static func validatePrefix(header: Data, messageType: UInt8) throws {
        guard header.count >= 6 else {
            throw NativeClientError.artifactTransferFailed("artifact header truncated")
        }
        guard header.prefix(4) == magic,
              header[header.startIndex.advanced(by: 4)] == protocolVersion,
              header[header.startIndex.advanced(by: 5)] == messageType else {
            throw NativeClientError.artifactTransferFailed("invalid artifact header")
        }
    }

    private static func readUInt32(_ data: Data, offset: Int) throws -> UInt32 {
        guard offset + 4 <= data.count else {
            throw NativeClientError.artifactTransferFailed("artifact header truncated")
        }
        return data.subdata(in: offset..<(offset + 4)).withUnsafeBytes { rawBuffer in
            rawBuffer.load(as: UInt32.self).bigEndian
        }
    }

    private static func readUInt64(_ data: Data, offset: Int) throws -> UInt64 {
        guard offset + 8 <= data.count else {
            throw NativeClientError.artifactTransferFailed("artifact header truncated")
        }
        return data.subdata(in: offset..<(offset + 8)).withUnsafeBytes { rawBuffer in
            rawBuffer.load(as: UInt64.self).bigEndian
        }
    }
}

private struct SHA256Digest {
    private var hasher = CryptoKit.SHA256()

    mutating func update(_ data: Data) {
        hasher.update(data: data)
    }

    mutating func finalizeHex() -> String {
        hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

actor NativeClusterBackend {
    static let shared = NativeClusterBackend()

    private var session: ClusterSession?
    private var connectionSummary: ConnectionSummary?
    private var clientName = NativeConstants.defaultClientName
    private let downloadDirectory: URL = {
        let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first ?? FileManager.default.temporaryDirectory
        return root.appendingPathComponent("superweb-downloads", isDirectory: true)
    }()

    func downloadListing() async -> DownloadListing {
        let fileManager = FileManager.default
        try? fileManager.createDirectory(at: downloadDirectory, withIntermediateDirectories: true, attributes: nil)

        let resourceKeys: Set<URLResourceKey> = [
            .isRegularFileKey,
            .fileSizeKey,
            .contentModificationDateKey
        ]
        let urls = (try? fileManager.contentsOfDirectory(
            at: downloadDirectory,
            includingPropertiesForKeys: Array(resourceKeys),
            options: [.skipsHiddenFiles]
        )) ?? []

        let records = urls.compactMap { url -> DownloadRecord? in
            guard let values = try? url.resourceValues(forKeys: resourceKeys),
                  values.isRegularFile == true else {
                return nil
            }
            return DownloadRecord(
                name: url.lastPathComponent,
                path: url.path,
                sizeBytes: values.fileSize ?? 0,
                modifiedAt: values.contentModificationDate ?? .distantPast,
                sourceMethod: inferDownloadSourceMethod(from: url.lastPathComponent)
            )
        }
        .sorted { left, right in
            if left.modifiedAt == right.modifiedAt {
                return left.name.localizedCaseInsensitiveCompare(right.name) == .orderedAscending
            }
            return left.modifiedAt > right.modifiedAt
        }

        return DownloadListing(directoryPath: downloadDirectory.path, records: records)
    }

    func health() async -> BridgeHealth {
        if let session {
            let snapshot = await session.snapshot()
            if !snapshot.connected {
                self.session = nil
                connectionSummary = nil
            }
            return BridgeHealth(
                status: "ok",
                connected: snapshot.connected,
                bridgeVersion: "native-ios-0.2.0",
                connection: snapshot.connected ? connectionSummary : nil,
                closedReason: snapshot.closedReason
            )
        }
        return BridgeHealth(
            status: "ok",
            connected: false,
            bridgeVersion: "native-ios-0.2.0",
            connection: nil,
            closedReason: nil
        )
    }

    func methods() async -> [MethodDescriptor] {
        [
            MethodDescriptor(
                method: NativeConstants.methodGemv,
                title: "gemv",
                subtitle: "Fixed vector request handled by the native Swift runtime."
            ),
            MethodDescriptor(
                method: NativeConstants.methodConv2d,
                title: "conv2d",
                subtitle: "Structured workload request with native artifact download."
            ),
            MethodDescriptor(
                method: NativeConstants.methodFreeContent,
                title: "free_content / custom",
                subtitle: "UTF-8 content sender that stays on the iOS-native client stack."
            )
        ]
    }

    private func inferDownloadSourceMethod(from fileName: String) -> String {
        let prefix = fileName.split(separator: "-", maxSplits: 1).first?.lowercased() ?? ""
        switch prefix {
        case NativeConstants.methodGemv:
            return NativeConstants.methodGemv
        case NativeConstants.methodConv2d:
            return NativeConstants.methodConv2d
        case NativeConstants.methodFreeContent:
            return NativeConstants.methodFreeContent
        default:
            return "saved output"
        }
    }

    func discover(_ payload: DiscoveryPayload) async throws -> DiscoveryResponse {
        let attempts = max(payload.discoveryAttempts, 1)
        var lastMessage = "Discovery did not run."
        var lastDiagnostics: [String] = []
        for attempt in 1...attempts {
            let snapshot = await BonjourDiscoveryBrowser().discover(timeout: payload.discoveryTimeout)
            lastDiagnostics = snapshot.diagnostics
            if !snapshot.nodes.isEmpty {
                return DiscoveryResponse(
                    results: snapshot.nodes,
                    message: snapshot.nodes.first?.message ?? "Resolved main-node Bonjour service.",
                    diagnostics: snapshot.diagnostics
                )
            }
            if snapshot.diagnostics.isEmpty {
                lastMessage = "Discovery attempt \(attempt)/\(attempts) failed: No main-node Bonjour service found."
            } else {
                lastMessage = "Discovery attempt \(attempt)/\(attempts) failed: \(snapshot.diagnostics.joined(separator: " | "))"
            }
            if attempt < attempts {
                let delay = UInt64(max(payload.retryDelay, 0) * 1_000_000_000)
                try await Task.sleep(nanoseconds: delay)
            }
        }
        return DiscoveryResponse(results: [], message: lastMessage, diagnostics: lastDiagnostics)
    }

    func connect(_ payload: ConnectPayload) async throws -> ConnectionSummary {
        let resolvedClientName = payload.clientName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? NativeConstants.defaultClientName : payload.clientName.trimmingCharacters(in: .whitespacesAndNewlines)
        let endpoint: (host: String, port: Int, source: String)
        let hostOverride = payload.hostOverride?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !hostOverride.isEmpty {
            endpoint = (hostOverride, payload.portOverride ?? NativeConstants.defaultTCPPort, "explicit")
        } else {
            let response = try await discover(
                DiscoveryPayload(
                    clientName: resolvedClientName,
                    multicastGroup: payload.multicastGroup,
                    udpPort: payload.udpPort,
                    discoveryTimeout: payload.discoveryTimeout,
                    discoveryAttempts: payload.discoveryAttempts,
                    retryDelay: payload.retryDelay,
                    bufferSize: payload.bufferSize,
                    multicastTTL: payload.multicastTTL
                )
            )
            guard let node = response.results.first else {
                throw NativeClientError.discoveryFailed(response.message)
            }
            endpoint = (node.host, payload.portOverride ?? node.port, node.source)
        }

        let newSession = try await ClusterSession.connect(
            host: endpoint.host,
            port: endpoint.port,
            timeout: payload.timeout,
            artifactDownloadDirectory: downloadDirectory
        )
        let joinResponse = try await newSession.join(clientName: resolvedClientName)

        if let currentSession = session {
            await currentSession.close()
        }
        session = newSession
        clientName = resolvedClientName
        let summary = ConnectionSummary(
            host: endpoint.host,
            port: endpoint.port,
            source: endpoint.source,
            clientId: joinResponse.clientId,
            workerCount: Int(joinResponse.workerCount),
            clientCount: Int(joinResponse.clientCount),
            statusCode: Int(joinResponse.statusCode)
        )
        connectionSummary = summary
        return summary
    }

    func disconnect() async {
        if let session {
            await session.close()
        }
        session = nil
        connectionSummary = nil
    }

    func sendGemv(_ payload: GemvRequestPayload) async throws -> RequestExecutionSummary {
        guard let session else {
            throw NativeClientError.notConnected
        }
        let size = normalizeRequestSize(payload.size)
        let seed = try parseVectorSeed(payload.vectorSeed)
        let vectorData = generateRequestVectorData(seed: seed, size: size)
        let result = try await session.requestGemv(
            clientName: payload.clientName,
            requestId: requestID(prefix: "gemv"),
            vectorData: vectorData,
            size: size,
            iterationCount: payload.iterationCount,
            objectId: payload.objectId,
            streamId: payload.streamId
        )
        return updateStateAndBuildSummary(from: result)
    }

    func sendConv2D(_ payload: Conv2DRequestPayload) async throws -> RequestExecutionSummary {
        guard let session else {
            throw NativeClientError.notConnected
        }
        let result = try await session.requestConv2d(
            clientName: payload.clientName,
            requestId: requestID(prefix: "conv2d"),
            size: payload.size,
            objectId: payload.objectId.isEmpty ? nil : payload.objectId,
            localDownloadName: payload.localDownloadName.isEmpty ? nil : payload.localDownloadName,
            iterationCount: payload.iterationCount,
            streamId: payload.streamId
        )
        return updateStateAndBuildSummary(from: result)
    }

    func sendCustom(_ payload: CustomRequestPayload) async throws -> RequestExecutionSummary {
        guard let session else {
            throw NativeClientError.notConnected
        }
        guard !payload.content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw NativeClientError.protocolViolation("content must not be empty")
        }
        let method = payload.method.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? NativeConstants.methodFreeContent : payload.method.trimmingCharacters(in: .whitespacesAndNewlines)
        let result = try await session.requestFreeContent(
            clientName: payload.clientName,
            requestId: requestID(prefix: "text"),
            content: payload.content,
            method: method,
            iterationCount: payload.iterationCount,
            objectId: payload.objectId,
            streamId: payload.streamId
        )
        return updateStateAndBuildSummary(from: result)
    }

    private func updateStateAndBuildSummary(from result: NativeRequestResult) -> RequestExecutionSummary {
        if let summary = connectionSummary {
            connectionSummary = ConnectionSummary(
                host: summary.host,
                port: summary.port,
                source: summary.source,
                clientId: result.response.clientId.isEmpty ? summary.clientId : result.response.clientId,
                workerCount: Int(result.response.workerCount),
                clientCount: Int(result.response.clientCount),
                statusCode: Int(result.response.statusCode)
            )
        }

        let artifactSummary: ArtifactSummary?
        if let artifact = result.response.resultArtifact {
            artifactSummary = ArtifactSummary(
                artifactId: artifact.artifactId,
                contentType: artifact.contentType,
                sizeBytes: Int(artifact.sizeBytes),
                checksum: artifact.checksum,
                transferHost: artifact.transferHost,
                transferPort: Int(artifact.transferPort)
            )
        } else {
            artifactSummary = nil
        }

        return RequestExecutionSummary(
            acceptedTaskId: result.acceptedTaskId,
            requestAcceptLatencyMs: result.requestAcceptLatencyMs,
            clientElapsedMs: result.clientElapsedMs,
            transmissionOverheadMs: result.transmissionOverheadMs,
            outputPreview: Array(result.outputValues.prefix(8)),
            outputText: safeUTF8Preview(result.outputBytes),
            outputByteCount: result.outputBytes.count,
            savedOutputPath: result.savedOutputURL?.path ?? "",
            artifactPath: result.artifactURL?.path ?? "",
            artifactDownloaded: result.artifactDownloaded,
            artifact: artifactSummary,
            response: RuntimeResponseSummary(
                requestId: result.response.requestId,
                taskId: result.response.taskId,
                method: result.response.method,
                size: result.response.size,
                objectId: result.response.objectId,
                streamId: result.response.streamId,
                statusCode: Int(result.response.statusCode),
                errorMessage: result.response.errorMessage,
                workerCount: Int(result.response.workerCount),
                clientCount: Int(result.response.clientCount),
                clientId: result.response.clientId,
                iterationCount: Int(result.response.iterationCount),
                elapsedMs: Int(result.response.elapsedMs)
            )
        )
    }
}
