import SwiftUI

struct DownloadHistoryView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 18) {
                summaryCard

                if model.downloadRecords.isEmpty {
                    emptyStateCard
                } else {
                    LazyVStack(spacing: 12) {
                        ForEach(model.downloadRecords) { record in
                            downloadRow(for: record)
                        }
                    }
                }
            }
            .padding(20)
            .padding(.bottom, 32)
        }
        .background(AppTheme.pageGradient.ignoresSafeArea())
        .navigationTitle("下载记录")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await model.refreshDownloadHistory()
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("刷新") {
                    Task { await model.refreshDownloadHistory() }
                }
            }
        }
    }

    private var summaryCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("已保存文件", systemImage: "tray.full")
                    .font(.system(size: 19, weight: .semibold, design: .rounded))
                Spacer()
                Text("\(model.downloadRecords.count)")
                    .font(.system(size: 14, weight: .semibold, design: .monospaced))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(AppTheme.brandGreen.opacity(0.14))
                    .clipShape(Capsule())
            }

            Text("目录")
                .font(.system(size: 12, weight: .medium, design: .rounded))
                .foregroundStyle(AppTheme.secondaryText)

            Text(displayDirectoryPath)
                .font(.system(size: 13, weight: .regular, design: .monospaced))
                .foregroundStyle(Color.black.opacity(0.76))
                .fixedSize(horizontal: false, vertical: true)

            Text("点击右侧分享按钮可以把文件导出到 Files、AirDrop 或其他 app。")
                .font(.system(size: 13, weight: .regular, design: .rounded))
                .foregroundStyle(AppTheme.secondaryText)
        }
        .cardStyle()
    }

    private var emptyStateCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("还没有下载记录。")
                .font(.system(size: 16, weight: .semibold, design: .rounded))
            Text("执行 `gemv` 或 `conv2d` 后，已保存的输出会出现在这里。`free_content` 默认只显示在聊天记录里，不会自动落盘。")
                .font(.system(size: 14, weight: .regular, design: .rounded))
                .foregroundStyle(AppTheme.secondaryText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardStyle()
    }

    private func downloadRow(for record: DownloadRecord) -> some View {
        let accent = accentColor(for: record)

        return HStack(alignment: .top, spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(accent.opacity(0.14))
                    .frame(width: 52, height: 52)
                Image(systemName: symbolName(for: record))
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(accent)
            }

            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(record.name)
                        .font(.system(size: 16, weight: .semibold, design: .rounded))
                        .foregroundStyle(Color.black.opacity(0.86))
                        .lineLimit(2)
                    Spacer(minLength: 8)
                    Text(record.sourceMethod)
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                        .foregroundStyle(accent)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 5)
                        .background(accent.opacity(0.12))
                        .clipShape(Capsule())
                }

                Text(relativeDisplayPath(for: record.path))
                    .font(.system(size: 12, weight: .regular, design: .monospaced))
                    .foregroundStyle(AppTheme.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 12) {
                    Label(formattedSize(record.sizeBytes), systemImage: "internaldrive")
                    Label(formattedDate(record.modifiedAt), systemImage: "clock")
                }
                .font(.system(size: 12, weight: .medium, design: .rounded))
                .foregroundStyle(AppTheme.secondaryText)
            }

            ShareLink(item: record.fileURL) {
                Image(systemName: "square.and.arrow.up")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 38, height: 38)
                    .background(accent)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
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

    private var displayDirectoryPath: String {
        if model.downloadDirectoryPath.isEmpty {
            return "Documents/superweb-downloads"
        }
        return relativeDisplayPath(for: model.downloadDirectoryPath)
    }

    private func relativeDisplayPath(for absolutePath: String) -> String {
        if let range = absolutePath.range(of: "/Documents/") {
            return String(absolutePath[range.lowerBound...]).replacingOccurrences(of: "/Documents/", with: "Documents/")
        }
        return absolutePath
    }

    private func symbolName(for record: DownloadRecord) -> String {
        switch record.sourceMethod {
        case "conv2d":
            return "square.grid.3x3.square"
        case "gemv":
            return "waveform.path.ecg.rectangle"
        default:
            return "doc"
        }
    }

    private func accentColor(for record: DownloadRecord) -> Color {
        switch record.sourceMethod {
        case "conv2d":
            return Color(hex: ChatMethod.conv2d.accentHex)
        case "gemv":
            return Color(hex: ChatMethod.gemv.accentHex)
        default:
            return Color(hex: "5B667A")
        }
    }

    private func formattedSize(_ sizeBytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(sizeBytes), countStyle: .file)
    }

    private func formattedDate(_ date: Date) -> String {
        Self.dateFormatter.string(from: date)
    }

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter
    }()
}
