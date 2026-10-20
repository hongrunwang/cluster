import SwiftUI

struct ConnectionView: View {
    @EnvironmentObject private var model: AppModel
    @State private var pulse = false

    var body: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 20) {
                headerCard
                discoveredNodesCard
                bridgeStatusCard
                manualConnectCard
            }
            .padding(20)
            .padding(.bottom, 36)
        }
        .background(AppTheme.pageGradient.ignoresSafeArea())
        .navigationTitle("扫描主节点")
        .navigationBarTitleDisplayMode(.large)
        .task {
            await model.refreshBridgeStatus()
            if ProcessInfo.processInfo.environment["SUPERWEB_AUTOSCAN"] == "1",
               !model.isScanning,
               model.discoveredNodes.isEmpty {
                await model.scanForMainNode()
            }
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 1.7).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    model.openDownloads()
                } label: {
                    Image(systemName: "tray.full")
                }
                .accessibilityLabel("下载记录")
            }
        }
    }

    private var headerCard: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .center, spacing: 16) {
                ZStack {
                    scanningRadar
                    Image("BrandMark")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 48, height: 48)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text("superweb-client")
                        .font(.system(size: 29, weight: .semibold, design: .rounded))
                    Text("iOS Native Client")
                        .font(.system(size: 14, weight: .medium, design: .rounded))
                        .foregroundStyle(AppTheme.secondaryText)
                    Text("先扫 main node，再连 session。discovery、TCP 长连、protobuf 收发和 artifact 下载现在都在 iOS 端原生完成。")
                        .font(.system(size: 14, weight: .regular, design: .rounded))
                        .foregroundStyle(AppTheme.secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            HStack(spacing: 12) {
                Button {
                    Task { await model.scanForMainNode() }
                } label: {
                    Label(model.isScanning ? "扫描中..." : "扫描 main node", systemImage: "dot.radiowaves.left.and.right")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryPillButtonStyle())
                .disabled(model.isScanning || model.isConnecting)

                Button {
                    Task { await model.refreshBridgeStatus() }
                } label: {
                    Label("刷新状态", systemImage: "bolt.horizontal.circle")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(SecondaryPillButtonStyle())
            }

            Button {
                model.openOfflinePreview()
            } label: {
                Label("离线预览 UI", systemImage: "rectangle.stack")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(SecondaryPillButtonStyle())
        }
        .cardStyle(padding: 22)
    }

    private var bridgeStatusCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("运行时状态")
                    .font(.system(size: 19, weight: .semibold, design: .rounded))
                Spacer()
                statusChip
            }

            VStack(alignment: .leading, spacing: 8) {
                Text("Transport")
                    .font(.system(size: 13, weight: .medium, design: .rounded))
                    .foregroundStyle(AppTheme.secondaryText)
                Text("Native Swift backend on iPhone / simulator. Bonjour handles discovery and requests go straight to the main node over TCP.")
                    .font(.system(size: 14, weight: .regular, design: .rounded))
                    .foregroundStyle(AppTheme.secondaryText)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(14)
                    .background(Color.white)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .stroke(AppTheme.line, lineWidth: 1)
                    )
            }

            Text(model.bannerText)
                .font(.system(size: 14, weight: .regular, design: .rounded))
                .foregroundStyle(AppTheme.secondaryText)
        }
        .cardStyle()
    }

    private var manualConnectCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("手动连接")
                .font(.system(size: 19, weight: .semibold, design: .rounded))

            VStack(spacing: 12) {
                textField("Client name", text: $model.clientName)
                HStack(spacing: 12) {
                    textField("Host override", text: $model.manualHost)
                    textField("Port", text: $model.portText, keyboard: .numberPad)
                        .frame(width: 104)
                }
            }

            Button {
                Task { await model.connectUsingCurrentForm() }
            } label: {
                Label(model.isConnecting ? "连接中..." : "直接连接", systemImage: "link")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(PrimaryPillButtonStyle())
            .disabled(model.isConnecting || model.clientName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .cardStyle()
    }

    private var discoveredNodesCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("扫描结果")
                    .font(.system(size: 19, weight: .semibold, design: .rounded))
                Spacer()
                Text("\(model.discoveredNodes.count) node")
                    .font(.system(size: 13, weight: .medium, design: .rounded))
                    .foregroundStyle(AppTheme.secondaryText)
            }

            if model.discoveredNodes.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text("还没有发现 main node。先点上面的扫描按钮，或者直接输入 host:port。")
                        .font(.system(size: 14, weight: .regular, design: .rounded))
                        .foregroundStyle(AppTheme.secondaryText)
                    if !model.lastDiscoveryDiagnostics.isEmpty {
                        Divider()
                        Text("Bonjour 诊断")
                            .font(.system(size: 13, weight: .semibold, design: .rounded))
                            .foregroundStyle(AppTheme.secondaryText)
                        ForEach(Array(model.lastDiscoveryDiagnostics.enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.system(size: 12, weight: .regular, design: .monospaced))
                                .foregroundStyle(AppTheme.secondaryText)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .background(AppTheme.shellBackground)
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            } else {
                VStack(spacing: 12) {
                    ForEach(model.discoveredNodes) { node in
                        HStack(alignment: .top, spacing: 14) {
                            ZStack {
                                RoundedRectangle(cornerRadius: 16, style: .continuous)
                                    .fill(AppTheme.brandGreen.opacity(0.16))
                                    .frame(width: 52, height: 52)
                                Image(systemName: "antenna.radiowaves.left.and.right")
                                    .font(.system(size: 20, weight: .semibold))
                                    .foregroundStyle(AppTheme.brandGreenDark)
                            }

                            VStack(alignment: .leading, spacing: 4) {
                                Text(node.host)
                                    .font(.system(size: 18, weight: .semibold, design: .rounded))
                                Text("port \(node.port) • \(node.source)")
                                    .font(.system(size: 13, weight: .medium, design: .rounded))
                                    .foregroundStyle(AppTheme.secondaryText)
                                Text(node.message)
                                    .font(.system(size: 13, weight: .regular, design: .rounded))
                                    .foregroundStyle(AppTheme.secondaryText)
                                    .fixedSize(horizontal: false, vertical: true)
                            }

                            Spacer(minLength: 8)

                            Button("连接") {
                                Task { await model.connectUsingCurrentForm(hostOverride: node.host) }
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(AppTheme.brandGreen)
                        }
                        .padding(14)
                        .background(Color.white.opacity(0.85))
                        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 22, style: .continuous)
                                .stroke(AppTheme.line, lineWidth: 1)
                        )
                    }
                }
            }
        }
        .cardStyle()
    }

    private var scanningRadar: some View {
        ZStack {
            Circle()
                .fill(AppTheme.brandGreen.opacity(0.10))
                .frame(width: 112, height: 112)
                .scaleEffect(pulse ? 1.04 : 0.88)

            Circle()
                .stroke(AppTheme.brandGreen.opacity(0.20), lineWidth: 1)
                .frame(width: 92, height: 92)
                .scaleEffect(pulse ? 1.14 : 0.96)

            Circle()
                .stroke(AppTheme.brandGreen.opacity(0.34), lineWidth: 2)
                .frame(width: 68, height: 68)

            Image(systemName: "viewfinder.circle")
                .font(.system(size: 34, weight: .semibold))
                .foregroundStyle(AppTheme.brandGreenDark)
        }
        .frame(width: 120, height: 120)
    }

    private var statusChip: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(model.isBridgeHealthy ? AppTheme.brandGreen : Color.red.opacity(0.85))
                .frame(width: 8, height: 8)
            Text(model.connectionSummary != nil ? "Session online" : (model.isBridgeHealthy ? "Native ready" : "Native offline"))
                .font(.system(size: 13, weight: .medium, design: .rounded))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(model.isBridgeHealthy ? AppTheme.brandGreen.opacity(0.12) : Color.red.opacity(0.08))
        .clipShape(Capsule())
    }

    private func textField(_ title: String, text: Binding<String>, keyboard: UIKeyboardType = .default) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.system(size: 13, weight: .medium, design: .rounded))
                .foregroundStyle(AppTheme.secondaryText)
            TextField(title, text: text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(keyboard)
                .padding(12)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(AppTheme.line, lineWidth: 1)
                )
        }
    }
}

private struct PrimaryPillButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .semibold, design: .rounded))
            .foregroundStyle(.white)
            .padding(.horizontal, 18)
            .padding(.vertical, 14)
            .background(configuration.isPressed ? AppTheme.brandGreenDark : AppTheme.brandGreen)
            .clipShape(Capsule())
            .scaleEffect(configuration.isPressed ? 0.98 : 1.0)
    }
}

private struct SecondaryPillButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .semibold, design: .rounded))
            .foregroundStyle(Color.black.opacity(0.78))
            .padding(.horizontal, 18)
            .padding(.vertical, 14)
            .background(Color.white.opacity(configuration.isPressed ? 0.74 : 0.94))
            .overlay(
                Capsule()
                    .stroke(AppTheme.line, lineWidth: 1)
            )
            .clipShape(Capsule())
            .scaleEffect(configuration.isPressed ? 0.98 : 1.0)
    }
}
