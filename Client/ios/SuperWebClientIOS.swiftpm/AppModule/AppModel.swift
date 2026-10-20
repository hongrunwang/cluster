import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var path: [AppRoute] = []

    @Published var clientName = "ios native client"
    @Published var manualHost = ""
    @Published var portText = "52020"

    @Published var isBridgeHealthy = false
    @Published var isScanning = false
    @Published var isConnecting = false
    @Published var isSending = false
    @Published var isDisconnecting = false
    @Published var bannerText = "Native backend not checked yet."
    @Published var lastDiscoveryDiagnostics: [String] = []

    @Published var discoveredNodes: [DiscoveredNode] = []
    @Published var availableMethods: [MethodDescriptor] = []
    @Published var connectionSummary: ConnectionSummary?
    @Published var downloadRecords: [DownloadRecord] = []
    @Published var downloadDirectoryPath = ""

    @Published var gemvDraft = GemvRequestDraft()
    @Published var conv2dDraft = Conv2DRequestDraft()
    @Published var customDraft = CustomRequestDraft()

    @Published private var conversationStore: [ChatMethod: [ChatBubble]] = [:]

    func probeBridgeIfNeeded() async {
        guard bannerText == "Native backend not checked yet." else {
            return
        }
        await refreshBridgeStatus()
        await loadMethods()
    }

    func refreshBridgeStatus() async {
        let health = await NativeClusterBackend.shared.health()
        connectionSummary = health.connection
        isBridgeHealthy = (health.status == "ok")
        if health.connected, let summary = health.connection {
            bannerText = "Native backend online. Connected to \(summary.host):\(summary.port) as \(summary.clientId)."
        } else if let closedReason = health.closedReason, !closedReason.isEmpty {
            bannerText = "Native backend online, but the last cluster session closed: \(closedReason)"
        } else {
            bannerText = "Native backend online. Scan or connect to the main node."
        }
    }

    func loadMethods() async {
        availableMethods = await NativeClusterBackend.shared.methods()
    }

    func refreshDownloadHistory() async {
        let listing = await NativeClusterBackend.shared.downloadListing()
        downloadRecords = listing.records
        downloadDirectoryPath = listing.directoryPath
    }

    func openDownloads() {
        Task {
            await refreshDownloadHistory()
            if path.last != .downloads {
                path.append(.downloads)
            }
        }
    }

    func messages(for method: ChatMethod) -> [ChatBubble] {
        return conversationStore[method] ?? []
    }

    func scanForMainNode() async {
        isScanning = true
        defer { isScanning = false }

        do {
            let response = try await NativeClusterBackend.shared.discover(
                DiscoveryPayload(
                    clientName: clientName,
                    multicastGroup: "224.0.0.251",
                    udpPort: 5353,
                    discoveryTimeout: 3.0,
                    discoveryAttempts: 3,
                    retryDelay: 1.0,
                    bufferSize: 2048,
                    multicastTTL: 255
                )
            )
            discoveredNodes = response.results
            bannerText = response.message
            lastDiscoveryDiagnostics = response.diagnostics ?? []
        } catch {
            discoveredNodes = []
            bannerText = "Scan failed: \(error.localizedDescription)"
            lastDiscoveryDiagnostics = []
        }
    }

    func connectUsingCurrentForm(hostOverride: String? = nil) async {
        isConnecting = true
        defer { isConnecting = false }

        let cleanedHost = (hostOverride ?? manualHost).trimmingCharacters(in: .whitespacesAndNewlines)
        let hostValue = cleanedHost.isEmpty ? nil : cleanedHost

        do {
            let summary = try await NativeClusterBackend.shared.connect(
                ConnectPayload(
                    clientName: clientName,
                    hostOverride: hostValue,
                    portOverride: Int(portText),
                    timeout: 30,
                    multicastGroup: "224.0.0.251",
                    udpPort: 5353,
                    discoveryTimeout: 3.0,
                    discoveryAttempts: 3,
                    retryDelay: 1.0,
                    bufferSize: 2048,
                    multicastTTL: 255
                )
            )
            connectionSummary = summary
            bannerText = "Connected to \(summary.host):\(summary.port) via \(summary.source)."
            appendSystemMessageToAll(
                """
                Connected to \(summary.host):\(summary.port)
                client_id=\(summary.clientId) worker_count=\(summary.workerCount) client_count=\(summary.clientCount)
                """
            )
            if summary.workerCount == 0 {
                appendSystemMessageToAll("Connected to the main node, but there are currently no registered compute workers. Requests may return 'no registered compute workers are currently available'.")
            }
            await refreshBridgeStatus()
            path = [.methods]
        } catch {
            bannerText = "Connect failed: \(error.localizedDescription)"
        }
    }

    func disconnect() async {
        isDisconnecting = true
        defer { isDisconnecting = false }

        await NativeClusterBackend.shared.disconnect()
        bannerText = "Disconnected from the cluster session."
        appendSystemMessageToAll("Disconnected from the cluster session.")
        connectionSummary = nil
        await refreshBridgeStatus()
        path = []
    }

    func openChat(for method: ChatMethod) {
        bootstrapConversationIfNeeded(for: method)
        if path.last != .chat(method) {
            path.append(.chat(method))
        }
    }

    func openOfflinePreview() {
        appendSystemMessageToAll("Offline preview mode is active. The native iOS backend is idle until you connect to a real main node.")
        path = [.methods]
    }

    func sendRequest(for method: ChatMethod) async {
        guard connectionSummary != nil else {
            appendSystemMessage("Connect to the main node first.", to: method)
            return
        }

        isSending = true
        defer { isSending = false }

        do {
            let summary: RequestExecutionSummary

            switch method {
            case .gemv:
                let iterationCount = Int(gemvDraft.iterationCount) ?? 1
                appendOutgoingMessage(
                    """
                    Send gemv
                    size=\(gemvDraft.size)
                    vector_seed=\(gemvDraft.vectorSeed)
                    iteration_count=\(iterationCount)
                    """,
                    meta: compactMeta(objectId: gemvDraft.objectId, streamId: gemvDraft.streamId),
                    to: method
                )
                summary = try await NativeClusterBackend.shared.sendGemv(
                    GemvRequestPayload(
                        clientName: clientName,
                        size: gemvDraft.size,
                        vectorSeed: gemvDraft.vectorSeed,
                        iterationCount: iterationCount,
                        objectId: gemvDraft.objectId,
                        streamId: gemvDraft.streamId
                    )
                )
            case .conv2d:
                let iterationCount = Int(conv2dDraft.iterationCount) ?? 1
                appendOutgoingMessage(
                    """
                    Send conv2d
                    size=\(conv2dDraft.size)
                    download_name=\(conv2dDraft.localDownloadName.isEmpty ? "<auto>" : conv2dDraft.localDownloadName)
                    iteration_count=\(iterationCount)
                    """,
                    meta: compactMeta(objectId: conv2dDraft.objectId, streamId: conv2dDraft.streamId),
                    to: method
                )
                summary = try await NativeClusterBackend.shared.sendConv2D(
                    Conv2DRequestPayload(
                        clientName: clientName,
                        size: conv2dDraft.size,
                        localDownloadName: conv2dDraft.localDownloadName,
                        iterationCount: iterationCount,
                        objectId: conv2dDraft.objectId,
                        streamId: conv2dDraft.streamId
                    )
                )
            case .custom:
                let iterationCount = Int(customDraft.iterationCount) ?? 1
                appendOutgoingMessage(
                    customDraft.content,
                    meta: "method=\(customDraft.method) iteration_count=\(iterationCount) \(compactMeta(objectId: customDraft.objectId, streamId: customDraft.streamId))",
                    to: method
                )
                summary = try await NativeClusterBackend.shared.sendCustom(
                    CustomRequestPayload(
                        clientName: clientName,
                        method: customDraft.method,
                        content: customDraft.content,
                        iterationCount: iterationCount,
                        objectId: customDraft.objectId,
                        streamId: customDraft.streamId
                    )
                )
            }

            appendAckMessage(summary, to: method)
            appendIncomingMessage(summary, to: method)
            await refreshDownloadHistory()
        } catch {
            appendSystemMessage("Request failed: \(error.localizedDescription)", to: method)
        }

        await refreshBridgeStatus()
        if connectionSummary == nil {
            appendSystemMessage("Cluster session is currently disconnected. Reconnect from the first screen before sending again.", to: method)
        }
    }

    private func compactMeta(objectId: String, streamId: String) -> String {
        let cleanedObjectId = objectId.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanedStreamId = streamId.trimmingCharacters(in: .whitespacesAndNewlines)
        return "object_id=\(cleanedObjectId.isEmpty ? "<empty>" : cleanedObjectId) stream_id=\(cleanedStreamId.isEmpty ? "<empty>" : cleanedStreamId)"
    }

    private func appendAckMessage(_ summary: RequestExecutionSummary, to method: ChatMethod) {
        appendMessage(
            ChatBubble(
                role: .ack,
                title: "CLIENT_REQUEST_OK",
                body: "task_id=\(summary.acceptedTaskId)\naccept_latency_ms=\(summary.requestAcceptLatencyMs)",
                meta: "method=\(summary.response.method) size=\(summary.response.size)"
            ),
            to: method
        )
    }

    private func appendIncomingMessage(_ summary: RequestExecutionSummary, to method: ChatMethod) {
        var bodyLines = [
            "status_code=\(summary.response.statusCode)",
            "elapsed_ms=\(summary.response.elapsedMs)",
            "iteration_count=\(summary.response.iterationCount)"
        ]

        if !summary.response.errorMessage.isEmpty {
            bodyLines.append(summary.response.errorMessage)
        }

        if !summary.outputPreview.isEmpty {
            let preview = summary.outputPreview
                .map { String(format: "%.6f", $0) }
                .joined(separator: ", ")
            bodyLines.append("output_preview=[\(preview)]")
        }

        if !summary.outputText.isEmpty {
            bodyLines.append(summary.outputText)
        }

        if let artifact = summary.artifact {
            bodyLines.append("artifact_id=\(artifact.artifactId)")
            bodyLines.append("artifact_bytes=\(artifact.sizeBytes)")
        } else if summary.outputByteCount > 0 {
            bodyLines.append("output_bytes=\(summary.outputByteCount)")
        }

        if !summary.savedOutputPath.isEmpty {
            bodyLines.append("saved_to=\(summary.savedOutputPath)")
        }

        appendMessage(
            ChatBubble(
                role: .incoming,
                title: "Cluster",
                body: bodyLines.joined(separator: "\n"),
                meta: "client_id=\(summary.response.clientId) worker_count=\(summary.response.workerCount) client_count=\(summary.response.clientCount)"
            ),
            to: method
        )
    }

    private func appendOutgoingMessage(_ body: String, meta: String, to method: ChatMethod) {
        appendMessage(
            ChatBubble(
                role: .outgoing,
                title: "You",
                body: body,
                meta: meta
            ),
            to: method
        )
    }

    private func appendSystemMessage(_ body: String, to method: ChatMethod) {
        appendMessage(
            ChatBubble(
                role: .system,
                title: "System",
                body: body,
                meta: ""
            ),
            to: method
        )
    }

    private func appendSystemMessageToAll(_ body: String) {
        for method in ChatMethod.allCases {
            appendSystemMessage(body, to: method)
        }
    }

    private func bootstrapConversationIfNeeded(for method: ChatMethod) {
        if conversationStore[method] != nil {
            return
        }

        let intro: String
        switch method {
        case .gemv:
            intro = "gemv chat is ready. Bottom panel controls size, vector seed, iteration_count, object_id, and stream_id."
        case .conv2d:
            intro = "conv2d chat is ready. Bottom panel controls workload size, local download name, iteration_count, and metadata."
        case .custom:
            intro = "custom chat is ready. Use free_content or any other method name for protocol debugging."
        }

        conversationStore[method] = [
            ChatBubble(role: .system, title: "System", body: intro)
        ]
    }

    private func appendMessage(_ message: ChatBubble, to method: ChatMethod) {
        bootstrapConversationIfNeeded(for: method)
        var messages = conversationStore[method] ?? []
        messages.append(message)
        conversationStore[method] = messages
    }
}
