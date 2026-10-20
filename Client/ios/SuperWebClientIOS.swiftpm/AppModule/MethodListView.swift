import SwiftUI

struct MethodListView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 18) {
                connectionCard

                VStack(alignment: .leading, spacing: 14) {
                    Text("选择 method")
                        .font(.system(size: 28, weight: .semibold, design: .rounded))
                    Text("这一页按微信联系人做。点一个 method 进去就是对应的聊天会话。")
                        .font(.system(size: 14, weight: .regular, design: .rounded))
                        .foregroundStyle(AppTheme.secondaryText)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                LazyVStack(spacing: 12) {
                    ForEach(ChatMethod.allCases) { method in
                        Button {
                            model.openChat(for: method)
                        } label: {
                            methodRow(for: method)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(20)
            .padding(.bottom, 32)
        }
        .background(AppTheme.pageGradient.ignoresSafeArea())
        .navigationTitle("Methods")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await model.refreshBridgeStatus()
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button("刷新") {
                    Task { await model.refreshBridgeStatus() }
                }
            }
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    model.openDownloads()
                } label: {
                    Image(systemName: "tray.full")
                }
                .accessibilityLabel("下载记录")

                Button(model.isDisconnecting ? "断开中..." : "断开") {
                    Task { await model.disconnect() }
                }
                .disabled(model.isDisconnecting)
            }
        }
    }

    private var connectionCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("当前连接")
                    .font(.system(size: 19, weight: .semibold, design: .rounded))
                Spacer()
                Text(model.connectionSummary?.clientId ?? "not connected")
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(AppTheme.brandGreen.opacity(0.12))
                    .clipShape(Capsule())
            }

            if let summary = model.connectionSummary {
                Text("\(summary.host):\(summary.port) • \(summary.source)")
                    .font(.system(size: 15, weight: .medium, design: .rounded))
                Text("worker_count=\(summary.workerCount) client_count=\(summary.clientCount) status_code=\(summary.statusCode)")
                    .font(.system(size: 13, weight: .regular, design: .monospaced))
                    .foregroundStyle(AppTheme.secondaryText)
                if summary.workerCount == 0 {
                    Text("Main node is reachable, but no compute workers are currently registered.")
                        .font(.system(size: 13, weight: .medium, design: .rounded))
                        .foregroundStyle(Color.orange)
                }
            } else {
                Text("No active cluster connection.")
                    .foregroundStyle(AppTheme.secondaryText)
            }
        }
        .cardStyle()
    }

    private func methodRow(for method: ChatMethod) -> some View {
        let color = Color(hex: method.accentHex)

        return HStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(color.opacity(0.16))
                    .frame(width: 58, height: 58)
                Image(systemName: method.symbolName)
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(color)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text(method.title)
                    .font(.system(size: 18, weight: .semibold, design: .rounded))
                    .foregroundStyle(Color.black.opacity(0.88))
                Text(method.subtitle)
                    .font(.system(size: 13, weight: .regular, design: .rounded))
                    .foregroundStyle(AppTheme.secondaryText)
                Text(method.entryHint)
                    .font(.system(size: 12, weight: .regular, design: .rounded))
                    .foregroundStyle(AppTheme.secondaryText.opacity(0.84))
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(AppTheme.secondaryText)
        }
        .padding(16)
        .background(Color.white.opacity(0.92))
        .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 24, style: .continuous)
                .stroke(AppTheme.line, lineWidth: 1)
        )
        .shadow(color: Color.black.opacity(0.028), radius: 18, x: 0, y: 10)
    }
}
