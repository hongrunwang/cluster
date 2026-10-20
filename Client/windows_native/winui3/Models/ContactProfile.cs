using Microsoft.UI;
using Microsoft.UI.Xaml.Media;

namespace SuperWebClient.WinUI3.Models;

public sealed class ContactProfile
{
    public string Key { get; set; } = string.Empty;

    public string Title { get; set; } = string.Empty;

    public string Subtitle { get; set; } = string.Empty;

    public string Description { get; set; } = string.Empty;

    public string DefaultSize { get; set; } = string.Empty;

    public string DefaultObjectId { get; set; } = string.Empty;

    public string Initials { get; set; } = string.Empty;

    public ContactMethodKind MethodKind { get; set; }

    public Brush AvatarBackground { get; set; } = new SolidColorBrush(Colors.LightGray);

    public Brush AvatarForeground { get; set; } = new SolidColorBrush(Colors.White);
}
