import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack(path: $model.path) {
            ConnectionView()
                .navigationDestination(for: AppRoute.self) { route in
                    switch route {
                    case .methods:
                        MethodListView()
                    case .chat(let method):
                        ChatView(method: method)
                    case .downloads:
                        DownloadHistoryView()
                    }
                }
        }
        .task {
            await model.probeBridgeIfNeeded()
        }
    }
}
