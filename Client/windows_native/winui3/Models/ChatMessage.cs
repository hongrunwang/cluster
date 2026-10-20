using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;

namespace SuperWebClient.WinUI3.Models;

public sealed class ChatMessage
{
    public string Header { get; set; } = string.Empty;

    public string Body { get; set; } = string.Empty;

    public string Meta { get; set; } = string.Empty;

    public int ColumnIndex { get; set; }

    public HorizontalAlignment Alignment { get; set; } = HorizontalAlignment.Left;

    public Thickness OuterMargin { get; set; }

    public Brush BubbleBackground { get; set; } = new SolidColorBrush(Microsoft.UI.Colors.White);

    public Brush BubbleBorder { get; set; } = new SolidColorBrush(Microsoft.UI.Colors.LightGray);

    public Brush Foreground { get; set; } = new SolidColorBrush(Microsoft.UI.Colors.Black);

    public string ActionText { get; set; } = string.Empty;

    public string ActionTargetPath { get; set; } = string.Empty;

    public Visibility ActionVisibility { get; set; } = Visibility.Collapsed;

    public int AvatarColumnIndex { get; set; }

    public string AvatarText { get; set; } = string.Empty;

    public Brush AvatarBackground { get; set; } = new SolidColorBrush(Microsoft.UI.Colors.LightGray);

    public Brush AvatarForeground { get; set; } = new SolidColorBrush(Microsoft.UI.Colors.White);

    public Visibility AvatarVisibility { get; set; } = Visibility.Visible;

    public Visibility HeaderVisibility { get; set; } = Visibility.Visible;
}
