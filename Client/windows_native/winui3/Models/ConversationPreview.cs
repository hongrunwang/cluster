using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;

namespace SuperWebClient.WinUI3.Models;

public sealed class ConversationPreview : INotifyPropertyChanged
{
    private string _key = string.Empty;
    private string _title = string.Empty;
    private string _presence = string.Empty;
    private string _snippet = string.Empty;
    private string _timestamp = string.Empty;
    private string _initials = string.Empty;
    private string _badgeText = string.Empty;
    private Visibility _badgeVisibility = Visibility.Collapsed;
    private Brush _avatarBackground = new SolidColorBrush(Colors.LightGray);
    private Brush _avatarForeground = new SolidColorBrush(Colors.White);
    private string _detailNote = string.Empty;
    private string _size = string.Empty;
    private string _objectId = string.Empty;
    private string _streamId = string.Empty;
    private bool _conv2dStatsOnly;
    private string _conv2dDownloadName = string.Empty;
    private string _conv2dStatsMaxSamples = "16";
    private ContactMethodKind _methodKind = ContactMethodKind.FreeContent;

    public ObservableCollection<ChatMessage> Transcript { get; } = [];

    public string Key
    {
        get => _key;
        set => SetField(ref _key, value);
    }

    public string Title
    {
        get => _title;
        set => SetField(ref _title, value);
    }

    public string Presence
    {
        get => _presence;
        set => SetField(ref _presence, value);
    }

    public string Snippet
    {
        get => _snippet;
        set => SetField(ref _snippet, value);
    }

    public string Timestamp
    {
        get => _timestamp;
        set => SetField(ref _timestamp, value);
    }

    public string Initials
    {
        get => _initials;
        set => SetField(ref _initials, value);
    }

    public string BadgeText
    {
        get => _badgeText;
        set => SetField(ref _badgeText, value);
    }

    public Visibility BadgeVisibility
    {
        get => _badgeVisibility;
        set => SetField(ref _badgeVisibility, value);
    }

    public Brush AvatarBackground
    {
        get => _avatarBackground;
        set => SetField(ref _avatarBackground, value);
    }

    public Brush AvatarForeground
    {
        get => _avatarForeground;
        set => SetField(ref _avatarForeground, value);
    }

    public string DetailNote
    {
        get => _detailNote;
        set => SetField(ref _detailNote, value);
    }

    public string Size
    {
        get => _size;
        set => SetField(ref _size, value);
    }

    public string ObjectId
    {
        get => _objectId;
        set => SetField(ref _objectId, value);
    }

    public string StreamId
    {
        get => _streamId;
        set => SetField(ref _streamId, value);
    }

    public bool Conv2dStatsOnly
    {
        get => _conv2dStatsOnly;
        set => SetField(ref _conv2dStatsOnly, value);
    }

    public string Conv2dDownloadName
    {
        get => _conv2dDownloadName;
        set => SetField(ref _conv2dDownloadName, value);
    }

    public string Conv2dStatsMaxSamples
    {
        get => _conv2dStatsMaxSamples;
        set => SetField(ref _conv2dStatsMaxSamples, value);
    }

    public ContactMethodKind MethodKind
    {
        get => _methodKind;
        set => SetField(ref _methodKind, value);
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void SetField<T>(ref T storage, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(storage, value))
        {
            return;
        }

        storage = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
