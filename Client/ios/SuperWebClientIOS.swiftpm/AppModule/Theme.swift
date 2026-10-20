import SwiftUI

enum AppTheme {
    static let brandGreen = Color(hex: "07C160")
    static let brandGreenDark = Color(hex: "0A9E51")
    static let mintBackground = Color(hex: "EAF7F0")
    static let shellBackground = Color(hex: "F6F7F1")
    static let cardBackground = Color.white.opacity(0.94)
    static let line = Color.black.opacity(0.08)
    static let secondaryText = Color(hex: "687381")
    static let bubbleWhite = Color.white
    static let bubbleAck = Color(hex: "EEF1F5")
    static let bubbleSystem = Color(hex: "F5F6F8")
    static let pageGradient = LinearGradient(
        colors: [
            Color(hex: "F7FBF8"),
            Color(hex: "E8F5EC"),
            Color(hex: "F3F3EC")
        ],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

extension Color {
    init(hex: String) {
        let sanitized = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var value: UInt64 = 0
        Scanner(string: sanitized).scanHexInt64(&value)

        let r, g, b: Double
        switch sanitized.count {
        case 6:
            r = Double((value >> 16) & 0xFF) / 255.0
            g = Double((value >> 8) & 0xFF) / 255.0
            b = Double(value & 0xFF) / 255.0
        default:
            r = 0.0
            g = 0.0
            b = 0.0
        }

        self.init(.sRGB, red: r, green: g, blue: b, opacity: 1.0)
    }
}

extension View {
    func cardStyle(padding: CGFloat = 18) -> some View {
        self
            .padding(padding)
            .background(AppTheme.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .stroke(AppTheme.line, lineWidth: 1)
            )
            .shadow(color: Color.black.opacity(0.035), radius: 20, x: 0, y: 12)
    }
}
