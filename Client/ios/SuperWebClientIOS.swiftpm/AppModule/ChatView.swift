import SwiftUI

struct ChatView: View {
    let method: ChatMethod

    @EnvironmentObject private var model: AppModel

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(model.messages(for: method)) { message in
                        MessageBubbleView(message: message)
                            .id(message.id)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.top, 16)
                .padding(.bottom, 180)
            }
            .background(AppTheme.shellBackground.ignoresSafeArea())
            .safeAreaInset(edge: .bottom) {
                composerPanel
                    .background(.ultraThinMaterial)
            }
            .navigationTitle(method.shortTitle)
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
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        model.openDownloads()
                    } label: {
                        Image(systemName: "tray.full")
                    }
                    .accessibilityLabel("下载记录")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if let summary = model.connectionSummary {
                        Text(summary.clientId)
                            .font(.system(size: 12, weight: .medium, design: .monospaced))
                            .foregroundStyle(AppTheme.secondaryText)
                    }
                }
            }
            .onChange(of: model.messages(for: method).count) { _ in
                scrollToBottom(proxy)
            }
            .onAppear {
                scrollToBottom(proxy)
            }
        }
    }

    private var composerPanel: some View {
        VStack(spacing: 12) {
            HStack {
                Label("参数配置", systemImage: "slider.horizontal.3")
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                Spacer()
                Text(method.title)
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(Color(hex: method.accentHex).opacity(0.14))
                    .clipShape(Capsule())
            }

            methodPanel

            Button {
                Task { await model.sendRequest(for: method) }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: model.isSending ? "arrow.trianglehead.2.clockwise.rotate.90" : "paperplane.fill")
                    Text(model.isSending ? "发送中..." : "发送")
                }
                .font(.system(size: 16, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(model.isSending ? Color(hex: method.accentHex).opacity(0.72) : Color(hex: method.accentHex))
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            }
            .disabled(model.isSending)
        }
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, 10)
    }

    @ViewBuilder
    private var methodPanel: some View {
        switch method {
        case .gemv:
            GemvPanel(draft: $model.gemvDraft)
        case .conv2d:
            Conv2DPanel(draft: $model.conv2dDraft)
        case .custom:
            CustomPanel(draft: $model.customDraft)
        }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        guard let lastID = model.messages(for: method).last?.id else {
            return
        }
        withAnimation(.easeOut(duration: 0.22)) {
            proxy.scrollTo(lastID, anchor: .bottom)
        }
    }
}

private struct MessageBubbleView: View {
    let message: ChatBubble

    var body: some View {
        switch message.role {
        case .system:
            VStack(spacing: 6) {
                Text(message.body)
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(AppTheme.secondaryText)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(AppTheme.bubbleSystem)
                    .clipShape(Capsule())
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 4)
        case .ack:
            VStack(spacing: 4) {
                Text(message.title)
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundStyle(AppTheme.secondaryText)
                Text(message.body)
                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                    .foregroundStyle(Color.black.opacity(0.72))
                if !message.meta.isEmpty {
                    Text(message.meta)
                        .font(.system(size: 11, weight: .regular, design: .monospaced))
                        .foregroundStyle(AppTheme.secondaryText)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(AppTheme.bubbleAck)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 2)
        case .outgoing, .incoming:
            HStack(alignment: .bottom, spacing: 10) {
                if message.role == .incoming {
                    avatar(color: Color(hex: "B5BBC4"), symbol: "cpu")
                } else {
                    Spacer(minLength: 44)
                }

                VStack(alignment: message.role == .incoming ? .leading : .trailing, spacing: 4) {
                    Text(message.title)
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                        .foregroundStyle(AppTheme.secondaryText)

                    Text(message.body)
                        .font(.system(size: 15, weight: .regular, design: .rounded))
                        .foregroundStyle(message.role == .outgoing ? .white : Color.black.opacity(0.86))
                        .multilineTextAlignment(.leading)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .background(message.role == .outgoing ? AppTheme.brandGreen : AppTheme.bubbleWhite)
                        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: 20, style: .continuous)
                                .stroke(message.role == .outgoing ? Color.clear : AppTheme.line, lineWidth: 1)
                        )

                    if !message.meta.isEmpty {
                        Text(message.meta)
                            .font(.system(size: 11, weight: .regular, design: .monospaced))
                            .foregroundStyle(AppTheme.secondaryText)
                    }
                }
                .frame(maxWidth: 290, alignment: message.role == .incoming ? .leading : .trailing)

                if message.role == .outgoing {
                    avatar(color: AppTheme.brandGreen.opacity(0.18), symbol: "person.fill")
                } else {
                    Spacer(minLength: 44)
                }
            }
            .frame(maxWidth: .infinity)
        }
    }

    private func avatar(color: Color, symbol: String) -> some View {
        RoundedRectangle(cornerRadius: 14, style: .continuous)
            .fill(color)
            .frame(width: 36, height: 36)
            .overlay(
                Image(systemName: symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(symbol == "person.fill" ? AppTheme.brandGreenDark : Color(hex: "52606D"))
            )
    }
}

private struct GemvPanel: View {
    @Binding var draft: GemvRequestDraft

    var body: some View {
        VStack(spacing: 12) {
            Picker("Size", selection: $draft.size) {
                Text("small").tag("small")
                Text("mid").tag("mid")
                Text("large").tag("large")
            }
            .pickerStyle(.segmented)

            compactField("Vector seed", text: $draft.vectorSeed, monospaced: true)

            HStack(spacing: 12) {
                compactField("Iteration", text: $draft.iterationCount, keyboard: .numberPad)
                compactField("Object id", text: $draft.objectId)
            }

            compactField("Stream id", text: $draft.streamId)
        }
    }
}

private struct Conv2DPanel: View {
    @Binding var draft: Conv2DRequestDraft

    var body: some View {
        VStack(spacing: 12) {
            Picker("Size", selection: $draft.size) {
                Text("small").tag("small")
                Text("mid").tag("mid")
                Text("large").tag("large")
            }
            .pickerStyle(.segmented)

            compactField("Local download name", text: $draft.localDownloadName)

            HStack(spacing: 12) {
                compactField("Iteration", text: $draft.iterationCount, keyboard: .numberPad)
                compactField("Object id", text: $draft.objectId)
            }

            compactField("Stream id", text: $draft.streamId)
        }
    }
}

private struct CustomPanel: View {
    @Binding var draft: CustomRequestDraft

    var body: some View {
        VStack(spacing: 12) {
            compactField("Method", text: $draft.method)

            TextEditor(text: $draft.content)
                .font(.system(size: 14, weight: .regular, design: .rounded))
                .frame(minHeight: 88, maxHeight: 110)
                .padding(10)
                .background(Color.white)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .stroke(AppTheme.line, lineWidth: 1)
                )

            HStack(spacing: 12) {
                compactField("Iteration", text: $draft.iterationCount, keyboard: .numberPad)
                compactField("Object id", text: $draft.objectId)
            }

            compactField("Stream id", text: $draft.streamId)
        }
    }
}

private func compactField(
    _ title: String,
    text: Binding<String>,
    keyboard: UIKeyboardType = .default,
    monospaced: Bool = false
) -> some View {
    VStack(alignment: .leading, spacing: 6) {
        Text(title)
            .font(.system(size: 12, weight: .medium, design: .rounded))
            .foregroundStyle(AppTheme.secondaryText)
        TextField(title, text: text)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .keyboardType(keyboard)
            .font(monospaced ? .system(size: 14, weight: .medium, design: .monospaced) : .system(size: 14, weight: .regular, design: .rounded))
            .padding(.horizontal, 12)
            .padding(.vertical, 12)
            .background(Color.white)
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(AppTheme.line, lineWidth: 1)
            )
    }
}
