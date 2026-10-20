using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using Microsoft.UI;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using SuperWebClient.WinUI3.Models;
using SuperWebClient.WinUI3.Services;

namespace SuperWebClient.WinUI3;

public sealed partial class MainWindow : Window, INotifyPropertyChanged
{
    private enum SidebarMode
    {
        Conversations,
        Contacts,
    }

    private const string GemvMethod = "gemv";
    private const string Conv2dMethod = "conv2d";
    private const string FreeContentMethod = "free_content";
    private const double AutoSendCadenceSeconds = 1.0;
    private static int _localRequestCounter;

    private static readonly nint DpiAwarenessContextUnaware = new(-1);
    private static readonly nint DpiAwarenessContextSystemAware = new(-2);
    private static readonly nint DpiAwarenessContextPerMonitorAware = new(-3);
    private static readonly nint DpiAwarenessContextPerMonitorAwareV2 = new(-4);
    private static readonly nint DpiAwarenessContextUnawareGdiScaled = new(-5);

    private readonly NativeClientBackend _backend = new();
    private readonly SemaphoreSlim _sendGate = new(1, 1);
    private readonly DispatcherQueueTimer _autoTimer;

    private FirewallRuleScope? _firewallScope;
    private SidebarMode _sidebarMode = SidebarMode.Conversations;
    private bool _reportedDisplayDiagnostics;
    private ConversationPreview? _activeConversation;
    private string _connectionStatusText = "Not connected to a main node yet. Click the logo on the top-left to open the connection menu.";
    private string _activeConversationTitle = "GEMV";
    private string _activeConversationSubtitle = "32K fixed vector route";
    private string _activeConversationInitials = "GV";
    private Brush _activeConversationAvatarBackground = new SolidColorBrush(ColorHelper.FromArgb(255, 102, 103, 171));
    private Brush _activeConversationAvatarForeground = new SolidColorBrush(Colors.White);
    private string _activeConversationDetailNote = "Open a compute method from the left panel to automatically start a new conversation for that route.";
    private string _clusterHealthText = "Worker / client status and this session's task summary will appear after the client connects.";
    private string _footerStatusText = "Disconnected";
    private string _composerHintText = "Click the primary button to send a fixed 32K vector request.";
    private string _requestSummaryText = "GEMV conversations send a deterministic 32K vector; the text box only appears under the free-content method.";
    private string _primaryActionText = "Send 32K vector";
    private string _routeTitleText = "gemv";
    private string _routeDetailText = "size=large · object_id=(auto) · stream_id=(empty)";
    private Visibility _gemvSizeVisibility = Visibility.Visible;
    private Visibility _textComposerVisibility = Visibility.Collapsed;
    private Visibility _requestSummaryVisibility = Visibility.Visible;
    private Visibility _conv2dSettingsVisibility = Visibility.Collapsed;
    private Visibility _autoContentVisibility = Visibility.Collapsed;
    private Visibility _vectorSeedVisibility = Visibility.Visible;

    public MainWindow()
    {
        InitializeComponent();
        Title = "superweb-client";
        WindowBranding.Apply(this);

        SeedContacts();
        LoadBrandmarkImage();
        var initialConversation = EnsureConversationForContact(Contacts[0], activate: true);
        SeedWelcomeMessages(initialConversation);

        _autoTimer = DispatcherQueue.GetForCurrentThread().CreateTimer();
        _autoTimer.IsRepeating = true;
        _autoTimer.Tick += AutoTimer_Tick;

        Activated += MainWindow_Activated;
        Closed += MainWindow_Closed;
        if (Content is FrameworkElement root)
        {
            root.Loaded += Root_Loaded;
        }

        if (ConversationList.Items.Count > 0)
        {
            ConversationList.SelectedIndex = 0;
        }

        if (FirewallRuleScope.TryCreate(out var scope, out var firewallError))
        {
            _firewallScope = scope;
            AddSystemMessage("A temporary Windows Firewall rule for mDNS has been opened for this session.");
        }
        else if (!string.IsNullOrWhiteSpace(firewallError))
        {
            AddSystemMessage(
                "Failed to open a temporary Windows Firewall rule for mDNS discovery.\n"
                + "If discovery fails, run the app as administrator, or set the host manually from the logo dropdown.\n"
                + firewallError);
        }
    }

    public ObservableCollection<ChatMessage> Messages { get; } = [];

    public ObservableCollection<ConversationPreview> Conversations { get; } = [];

    public ObservableCollection<ContactProfile> Contacts { get; } = [];

    public string SidebarSectionTitle => _sidebarMode == SidebarMode.Conversations
        ? "Recent conversations"
        : "Compute Methods";

    public string SidebarSearchPlaceholderText => _sidebarMode == SidebarMode.Conversations
        ? "Search recent conversations"
        : "Search compute methods";

    public Visibility ConversationListVisibility => _sidebarMode == SidebarMode.Conversations
        ? Visibility.Visible
        : Visibility.Collapsed;

    public Visibility ContactListVisibility => _sidebarMode == SidebarMode.Contacts
        ? Visibility.Visible
        : Visibility.Collapsed;

    public Brush ConversationsRailButtonBackground => _sidebarMode == SidebarMode.Conversations
        ? GetBrush("ToolbarButtonHoverBrush")
        : GetBrush("ToolbarButtonBrush");

    public Brush ConversationsRailButtonForeground => _sidebarMode == SidebarMode.Conversations
        ? GetBrush("PrimaryTextBrush")
        : GetBrush("SecondaryTextBrush");

    public Brush ConversationsRailButtonBorderBrush => _sidebarMode == SidebarMode.Conversations
        ? GetBrush("BrandBlueBrush")
        : GetBrush("SearchBorderBrush");

    public Brush ContactsRailButtonBackground => _sidebarMode == SidebarMode.Contacts
        ? GetBrush("ToolbarButtonHoverBrush")
        : GetBrush("ToolbarButtonBrush");

    public Brush ContactsRailButtonForeground => _sidebarMode == SidebarMode.Contacts
        ? GetBrush("PrimaryTextBrush")
        : GetBrush("SecondaryTextBrush");

    public Brush ContactsRailButtonBorderBrush => _sidebarMode == SidebarMode.Contacts
        ? GetBrush("BrandBlueBrush")
        : GetBrush("SearchBorderBrush");

    public Brush ConnectionIndicatorBrush => _backend.IsConnected
        ? GetBrush("StatusConnectedBrush")
        : GetBrush("StatusDisconnectedBrush");

    public Brush ConnectionIndicatorBorderBrush => _backend.IsConnected
        ? GetBrush("StatusConnectedBorderBrush")
        : GetBrush("StatusDisconnectedBorderBrush");

    public Brush ConnectionIndicatorPanelBrush => _backend.IsConnected
        ? GetBrush("StatusConnectedPanelBrush")
        : GetBrush("StatusDisconnectedPanelBrush");

    public string ConnectionStatusText
    {
        get => _connectionStatusText;
        private set => SetProperty(ref _connectionStatusText, value);
    }

    public string ActiveConversationTitle
    {
        get => _activeConversationTitle;
        private set => SetProperty(ref _activeConversationTitle, value);
    }

    public string ActiveConversationSubtitle
    {
        get => _activeConversationSubtitle;
        private set => SetProperty(ref _activeConversationSubtitle, value);
    }

    public string ActiveConversationInitials
    {
        get => _activeConversationInitials;
        private set => SetProperty(ref _activeConversationInitials, value);
    }

    public Brush ActiveConversationAvatarBackground
    {
        get => _activeConversationAvatarBackground;
        private set => SetProperty(ref _activeConversationAvatarBackground, value);
    }

    public Brush ActiveConversationAvatarForeground
    {
        get => _activeConversationAvatarForeground;
        private set => SetProperty(ref _activeConversationAvatarForeground, value);
    }

    public string ActiveConversationDetailNote
    {
        get => _activeConversationDetailNote;
        private set => SetProperty(ref _activeConversationDetailNote, value);
    }

    public string ClusterHealthText
    {
        get => _clusterHealthText;
        private set => SetProperty(ref _clusterHealthText, value);
    }

    public string FooterStatusText
    {
        get => _footerStatusText;
        private set => SetProperty(ref _footerStatusText, value);
    }

    public string ComposerHintText
    {
        get => _composerHintText;
        private set => SetProperty(ref _composerHintText, value);
    }

    public string RequestSummaryText
    {
        get => _requestSummaryText;
        private set => SetProperty(ref _requestSummaryText, value);
    }

    public string PrimaryActionText
    {
        get => _primaryActionText;
        private set => SetProperty(ref _primaryActionText, value);
    }

    public string RouteTitleText
    {
        get => _routeTitleText;
        private set => SetProperty(ref _routeTitleText, value);
    }

    public string RouteDetailText
    {
        get => _routeDetailText;
        private set => SetProperty(ref _routeDetailText, value);
    }

    public Visibility TextComposerVisibility
    {
        get => _textComposerVisibility;
        private set => SetProperty(ref _textComposerVisibility, value);
    }

    public Visibility GemvSizeVisibility
    {
        get => _gemvSizeVisibility;
        private set => SetProperty(ref _gemvSizeVisibility, value);
    }

    public Visibility RequestSummaryVisibility
    {
        get => _requestSummaryVisibility;
        private set => SetProperty(ref _requestSummaryVisibility, value);
    }

    public Visibility Conv2dSettingsVisibility
    {
        get => _conv2dSettingsVisibility;
        private set => SetProperty(ref _conv2dSettingsVisibility, value);
    }

    public Visibility AutoContentVisibility
    {
        get => _autoContentVisibility;
        private set => SetProperty(ref _autoContentVisibility, value);
    }

    public Visibility VectorSeedVisibility
    {
        get => _vectorSeedVisibility;
        private set => SetProperty(ref _vectorSeedVisibility, value);
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    private void LoadBrandmarkImage()
    {
        var imagePath = BrandingPaths.TryGetBrandImagePath();
        if (string.IsNullOrWhiteSpace(imagePath))
        {
            return;
        }

        BrandmarkImage.Source = new BitmapImage(new Uri(imagePath));
    }

    private void SeedContacts()
    {
        Contacts.Clear();
        Conversations.Clear();

        Contacts.Add(new ContactProfile
        {
            Key = GemvMethod,
            Title = "GEMV",
            Subtitle = "deterministic vector route",
            Description = "Pick a small / mid / large matrix on the right pane, then send a deterministic request vector.",
            MethodKind = ContactMethodKind.Gemv,
            DefaultSize = "large",
            DefaultObjectId = string.Empty,
            Initials = "GV",
            AvatarBackground = GetBrush("AvatarBlueBrush"),
            AvatarForeground = GetBrush("AvatarLetterBrush"),
        });
        Contacts.Add(new ContactProfile
        {
            Key = Conv2dMethod,
            Title = "Conv2d",
            Subtitle = "artifact-first runtime",
            Description = "Launch conv2d by small / mid / large workload; results usually land as artifacts.",
            MethodKind = ContactMethodKind.Conv2d,
            DefaultSize = "small",
            DefaultObjectId = "conv2d/small",
            Initials = "CV",
            AvatarBackground = GetBrush("AvatarOrangeBrush"),
            AvatarForeground = GetBrush("AvatarLetterBrush"),
        });
        Contacts.Add(new ContactProfile
        {
            Key = FreeContentMethod,
            Title = "Free Content",
            Subtitle = "text route / system overview",
            Description = "Kept as a text entry point. The main node currently replies with a system overview, useful for control-plane probing and readable output.",
            MethodKind = ContactMethodKind.FreeContent,
            DefaultSize = string.Empty,
            DefaultObjectId = string.Empty,
            Initials = "FC",
            AvatarBackground = GetBrush("AvatarTealBrush"),
            AvatarForeground = GetBrush("AvatarLetterBrush"),
        });
    }

    private ConversationPreview EnsureConversationForContact(ContactProfile contact, bool activate)
    {
        var existing = Conversations.FirstOrDefault(item => item.Key == contact.Key);
        if (existing is not null)
        {
            if (activate)
            {
                ActivateConversation(existing);
            }

            return existing;
        }

        var conversation = new ConversationPreview
        {
            Key = contact.Key,
            Title = contact.Title,
            Presence = contact.Subtitle,
            Snippet = contact.Description,
            Timestamp = "new",
            Initials = contact.Initials,
            AvatarBackground = contact.AvatarBackground,
            AvatarForeground = contact.AvatarForeground,
            DetailNote = contact.Description,
            MethodKind = contact.MethodKind,
            Size = contact.DefaultSize,
            ObjectId = contact.DefaultObjectId,
        };

        Conversations.Insert(0, conversation);
        AddContactIntro(conversation, contact);

        if (activate)
        {
            ActivateConversation(conversation);
        }

        return conversation;
    }

    private void AddContactIntro(ConversationPreview conversation, ContactProfile contact)
    {
        if (conversation.Transcript.Count > 0)
        {
            return;
        }

        AddMessageToConversation(
            conversation,
            alignment: HorizontalAlignment.Left,
            bubbleBackground: GetBrush("SystemBubbleBrush"),
            bubbleBorder: GetBrush("SystemBubbleBorderBrush"),
            foreground: GetBrush("PrimaryTextBrush"),
            header: "System",
            body: contact.MethodKind switch
            {
                ContactMethodKind.Gemv => "This is the GEMV compute method.\nUse the right pane to switch between small / mid / large matrix sizes, then hit the primary button to send a deterministic vector request.",
                ContactMethodKind.Conv2d => "This is the Conv2d compute method.\nPick a workload size on the right pane and start a new conv2d conversation from here.",
                _ => "This is the Free Content compute method.\nThe cluster currently returns a system overview, handy as a text entry point for the control plane.",
            },
            meta: DateTime.Now.ToString("HH:mm"),
            avatarText: "SYS",
            avatarBackground: GetBrush("AvatarPinkBrush"),
            avatarForeground: GetBrush("AvatarLetterBrush"),
            avatarVisibility: Visibility.Visible,
            headerVisibility: Visibility.Visible);
    }

    private void SeedWelcomeMessages(ConversationPreview initialConversation)
    {
        ActivateConversation(initialConversation);
        AddSystemMessage(
            "The WinUI 3 client is now laid out in three Fluent-Design panes.\n"
            + "Left: recent conversations and compute methods. Middle: chat stream. Right: pinned automation.");
        AddSystemMessage(
            "Connect to main node has moved into the top-left logo dropdown.\n"
            + "GEMV / Conv2d / Free Content in the compute methods list each open their own method-specific conversation.");
        AddSystemMessage(
            "Auto-send fires every 1 second by default and follows the active conversation's method.\n"
            + "iteration_count means how many times the cluster repeats execution, not how many times the client re-sends.");
    }

    private void ActivateConversation(ConversationPreview conversation)
    {
        _activeConversation = conversation;
        conversation.BadgeText = string.Empty;
        conversation.BadgeVisibility = Visibility.Collapsed;

        ApplyConversationPresentation(conversation);
        Messages.Clear();
        foreach (var message in conversation.Transcript)
        {
            Messages.Add(message);
        }

        if (!ReferenceEquals(ConversationList.SelectedItem, conversation))
        {
            ConversationList.SelectedItem = conversation;
        }

        var matchingContact = Contacts.FirstOrDefault(item => item.Key == conversation.Key);
        if (matchingContact is not null && !ReferenceEquals(ContactList.SelectedItem, matchingContact))
        {
            ContactList.SelectedItem = matchingContact;
        }
    }

    private void ApplyConversationPresentation(ConversationPreview conversation)
    {
        ActiveConversationTitle = conversation.Title;
        ActiveConversationSubtitle = conversation.Presence;
        ActiveConversationInitials = conversation.Initials;
        ActiveConversationAvatarBackground = conversation.AvatarBackground;
        ActiveConversationAvatarForeground = conversation.AvatarForeground;
        ActiveConversationDetailNote = conversation.DetailNote;

        RefreshRouteState(conversation);
        ApplyConversationEditors(conversation);
    }

    private void RefreshRouteState(ConversationPreview conversation)
    {
        var routeLabel = conversation.MethodKind switch
        {
            ContactMethodKind.Gemv => "gemv",
            ContactMethodKind.Conv2d => "conv2d",
            _ => "free_content",
        };

        RouteTitleText = routeLabel;
        RouteDetailText =
            $"size={(string.IsNullOrWhiteSpace(conversation.Size) ? "(default)" : conversation.Size)}"
            + $" · object_id={(string.IsNullOrWhiteSpace(conversation.ObjectId) ? "(auto)" : conversation.ObjectId)}"
            + $" · stream_id={(string.IsNullOrWhiteSpace(conversation.StreamId) ? "(empty)" : conversation.StreamId)}";
        if (conversation.MethodKind == ContactMethodKind.Conv2d)
        {
            RouteDetailText += conversation.Conv2dStatsOnly
                ? $" · response=stats_only · stats_max_samples={conversation.Conv2dStatsMaxSamples}"
                : $" · response=full · download_name={(string.IsNullOrWhiteSpace(conversation.Conv2dDownloadName) ? "(auto)" : conversation.Conv2dDownloadName)}";
        }

        switch (conversation.MethodKind)
        {
            case ContactMethodKind.Gemv:
                ComposerHintText = "Click the primary button to send a deterministic vector request for the current matrix size.";
                RequestSummaryText = BuildGemvRequestSummary(conversation.Size);
                PrimaryActionText = "Send GEMV request";
                GemvSizeVisibility = Visibility.Visible;
                TextComposerVisibility = Visibility.Collapsed;
                RequestSummaryVisibility = Visibility.Visible;
                Conv2dSettingsVisibility = Visibility.Collapsed;
                AutoContentVisibility = Visibility.Collapsed;
                VectorSeedVisibility = Visibility.Visible;
                break;
            case ContactMethodKind.Conv2d:
                ComposerHintText = "Conv2d conversations send requests based on the workload size selected in the right pane.";
                RequestSummaryText = BuildConv2dRequestSummary(conversation);
                PrimaryActionText = "Run conv2d";
                GemvSizeVisibility = Visibility.Collapsed;
                TextComposerVisibility = Visibility.Collapsed;
                RequestSummaryVisibility = Visibility.Visible;
                Conv2dSettingsVisibility = Visibility.Visible;
                AutoContentVisibility = Visibility.Collapsed;
                VectorSeedVisibility = Visibility.Collapsed;
                break;
            default:
                ComposerHintText = "Enter some text to send to the main node as a free_content entry point.";
                RequestSummaryText = "This conversation sends the input as a text-style request; the response is usually a readable system overview.";
                PrimaryActionText = "Send free content";
                GemvSizeVisibility = Visibility.Collapsed;
                TextComposerVisibility = Visibility.Visible;
                RequestSummaryVisibility = Visibility.Collapsed;
                Conv2dSettingsVisibility = Visibility.Collapsed;
                AutoContentVisibility = Visibility.Visible;
                VectorSeedVisibility = Visibility.Collapsed;
                break;
        }
    }

    private void ApplyConversationEditors(ConversationPreview conversation)
    {
        if (ObjectIdTextBox is not null && ObjectIdTextBox.Text != conversation.ObjectId)
        {
            ObjectIdTextBox.Text = conversation.ObjectId;
        }

        if (StreamIdTextBox is not null && StreamIdTextBox.Text != conversation.StreamId)
        {
            StreamIdTextBox.Text = conversation.StreamId;
        }

        if (conversation.MethodKind == ContactMethodKind.Conv2d && Conv2dSizeComboBox is not null)
        {
            var desiredSize = NormalizeConv2dSize(conversation.Size);
            foreach (var item in Conv2dSizeComboBox.Items)
            {
                if (item is ComboBoxItem comboItem
                    && string.Equals(comboItem.Content?.ToString(), desiredSize, StringComparison.OrdinalIgnoreCase))
                {
                    Conv2dSizeComboBox.SelectedItem = comboItem;
                    break;
                }
            }

            if (Conv2dStatsOnlyToggle is not null && Conv2dStatsOnlyToggle.IsOn != conversation.Conv2dStatsOnly)
            {
                Conv2dStatsOnlyToggle.IsOn = conversation.Conv2dStatsOnly;
            }
            if (Conv2dDownloadNameTextBox is not null && Conv2dDownloadNameTextBox.Text != conversation.Conv2dDownloadName)
            {
                Conv2dDownloadNameTextBox.Text = conversation.Conv2dDownloadName;
            }
            if (Conv2dStatsMaxSamplesTextBox is not null && Conv2dStatsMaxSamplesTextBox.Text != conversation.Conv2dStatsMaxSamples)
            {
                Conv2dStatsMaxSamplesTextBox.Text = conversation.Conv2dStatsMaxSamples;
            }
            UpdateConv2dEditorState(conversation);
        }

        if (conversation.MethodKind == ContactMethodKind.Gemv && GemvSizeComboBox is not null)
        {
            var desiredSize = NormalizeGemvSize(conversation.Size);
            foreach (var item in GemvSizeComboBox.Items)
            {
                if (item is ComboBoxItem comboItem
                    && string.Equals(comboItem.Content?.ToString(), desiredSize, StringComparison.OrdinalIgnoreCase))
                {
                    GemvSizeComboBox.SelectedItem = comboItem;
                    break;
                }
            }
        }
    }

    private void UpdateConv2dEditorState(ConversationPreview conversation)
    {
        if (Conv2dDownloadNameTextBox is not null)
        {
            Conv2dDownloadNameTextBox.IsEnabled = !conversation.Conv2dStatsOnly;
        }
        if (Conv2dStatsMaxSamplesTextBox is not null)
        {
            Conv2dStatsMaxSamplesTextBox.IsEnabled = conversation.Conv2dStatsOnly;
        }
    }

    private void TouchActiveConversation(string snippet, string? detailNote = null)
    {
        if (_activeConversation is null)
        {
            return;
        }

        _activeConversation.Snippet = CollapsePreview(snippet);
        _activeConversation.Timestamp = DateTime.Now.ToString("HH:mm");
        if (!string.IsNullOrWhiteSpace(detailNote))
        {
            _activeConversation.DetailNote = detailNote;
        }

        ApplyConversationPresentation(_activeConversation);
    }

    private async void ConnectButton_Click(object sender, RoutedEventArgs e)
    {
        await ConnectAsync();
    }

    private void DisconnectButton_Click(object sender, RoutedEventArgs e)
    {
        StopAutoSend(logMessage: false);
        _backend.Disconnect();
        ConnectionStatusText = "Not connected to a main node yet. Click the logo on the top-left to open the connection menu.";
        FooterStatusText = "Disconnected";
        RefreshConnectionIndicatorBindings();
        ClusterHealthText = "Worker / client status and this session's task summary will appear after the client connects.";
        AddSystemMessage("Disconnected from the main node.");
    }

    private async void PrimaryActionButton_Click(object sender, RoutedEventArgs e)
    {
        await DispatchActiveConversationActionAsync(isAutomated: false);
    }

    private void StartAutoSendButton_Click(object sender, RoutedEventArgs e)
    {
        if (!TryParseIterationCount(out var iterationCount))
        {
            return;
        }

        _autoTimer.Interval = TimeSpan.FromSeconds(AutoSendCadenceSeconds);
        _autoTimer.Start();
        AddSystemMessage(
            $"Auto-send started. cadence={AutoSendCadenceSeconds:0.##}s, "
            + $"method={RouteTitleText}, iteration_count={iterationCount}");
    }

    private void StopAutoSendButton_Click(object sender, RoutedEventArgs e)
    {
        StopAutoSend(logMessage: true);
    }

    private void ConversationList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ConversationList.SelectedItem is ConversationPreview conversation)
        {
            ActivateConversation(conversation);
        }
    }

    private void ContactList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (ContactList.SelectedItem is ContactProfile contact)
        {
            EnsureConversationForContact(contact, activate: true);
        }
    }

    private void ConversationsRailButton_Click(object sender, RoutedEventArgs e)
    {
        SetSidebarMode(SidebarMode.Conversations);
    }

    private void ContactsRailButton_Click(object sender, RoutedEventArgs e)
    {
        SetSidebarMode(SidebarMode.Contacts);
    }

    private void ObjectIdTextBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (_activeConversation is null)
        {
            return;
        }

        _activeConversation.ObjectId = NormalizeOptionalText(ObjectIdTextBox.Text) ?? string.Empty;
        RefreshRouteState(_activeConversation);
    }

    private void StreamIdTextBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (_activeConversation is null)
        {
            return;
        }

        _activeConversation.StreamId = NormalizeOptionalText(StreamIdTextBox.Text) ?? string.Empty;
        RefreshRouteState(_activeConversation);
    }

    private void GemvSizeComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_activeConversation is null || _activeConversation.MethodKind != ContactMethodKind.Gemv)
        {
            return;
        }

        _activeConversation.Size = GetSelectedGemvSize();
        RefreshRouteState(_activeConversation);
    }

    private void Conv2dSizeComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_activeConversation is null || _activeConversation.MethodKind != ContactMethodKind.Conv2d)
        {
            return;
        }

        var size = GetSelectedConv2dSize();
        _activeConversation.Size = size;
        if (string.IsNullOrWhiteSpace(_activeConversation.ObjectId) || _activeConversation.ObjectId.StartsWith("conv2d/", StringComparison.OrdinalIgnoreCase))
        {
            _activeConversation.ObjectId = $"conv2d/{size}";
            if (ObjectIdTextBox is not null && ObjectIdTextBox.Text != _activeConversation.ObjectId)
            {
                ObjectIdTextBox.Text = _activeConversation.ObjectId;
            }
        }

        RefreshRouteState(_activeConversation);
    }

    private void Conv2dStatsOnlyToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_activeConversation is null || _activeConversation.MethodKind != ContactMethodKind.Conv2d)
        {
            return;
        }

        _activeConversation.Conv2dStatsOnly = Conv2dStatsOnlyToggle.IsOn;
        UpdateConv2dEditorState(_activeConversation);
        RefreshRouteState(_activeConversation);
    }

    private void Conv2dDownloadNameTextBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (_activeConversation is null || _activeConversation.MethodKind != ContactMethodKind.Conv2d)
        {
            return;
        }

        _activeConversation.Conv2dDownloadName = NormalizeOptionalText(Conv2dDownloadNameTextBox.Text) ?? string.Empty;
        RefreshRouteState(_activeConversation);
    }

    private void Conv2dStatsMaxSamplesTextBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (_activeConversation is null || _activeConversation.MethodKind != ContactMethodKind.Conv2d)
        {
            return;
        }

        _activeConversation.Conv2dStatsMaxSamples = NormalizeOptionalText(Conv2dStatsMaxSamplesTextBox.Text) ?? "16";
        RefreshRouteState(_activeConversation);
    }

    private void MessageActionButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button)
        {
            return;
        }

        var targetPath = button.Tag as string;
        if (string.IsNullOrWhiteSpace(targetPath))
        {
            AddSystemMessage("This message has no download location to open.");
            return;
        }

        try
        {
            OpenDownloadLocation(targetPath);
        }
        catch (Exception ex)
        {
            AddSystemMessage($"Failed to open download location: {ex.Message}");
        }
    }

    private async void AutoTimer_Tick(DispatcherQueueTimer sender, object args)
    {
        try
        {
            await DispatchActiveConversationActionAsync(isAutomated: true);
        }
        catch (Exception ex)
        {
            StopAutoSend(logMessage: false);
            AddSystemMessage($"Auto-send stopped due to an error: {ex.Message}");
        }
    }

    private void MainWindow_Closed(object sender, WindowEventArgs args)
    {
        StopAutoSend(logMessage: false);
        _backend.Dispose();
        _firewallScope?.Dispose();
        _sendGate.Dispose();
    }

    private void MainWindow_Activated(object sender, WindowActivatedEventArgs args)
    {
        if (_reportedDisplayDiagnostics)
        {
            return;
        }

        QueueDisplayDiagnostics();
    }

    private void Root_Loaded(object sender, RoutedEventArgs e)
    {
        QueueDisplayDiagnostics();
    }

    private async Task DispatchActiveConversationActionAsync(bool isAutomated)
    {
        if (_activeConversation is null)
        {
            AddSystemMessage("Pick a compute method from the left panel or a recent conversation first.");
            return;
        }

        switch (_activeConversation.MethodKind)
        {
            case ContactMethodKind.Gemv:
                await SendFixedVectorAsync(isAutomated);
                break;
            case ContactMethodKind.Conv2d:
                await SendConv2dAsync(isAutomated);
                break;
            default:
                await SendManualContentAsync(isAutomated);
                break;
        }
    }

    private async Task ConnectAsync()
    {
        try
        {
            var summary = await _backend.ConnectAsync(
                clientName: GetClientName(),
                hostOverride: NormalizeOptionalText(HostOverrideTextBox.Text),
                portOverride: ParsePort());

            ConnectionStatusText =
                $"Connected to {summary.Host}:{summary.Port} via {summary.Source}."
                + $" client_id={summary.ClientId}, worker_count={summary.WorkerCount}, client_count={summary.ClientCount}";
            FooterStatusText = $"Connected {summary.Host}:{summary.Port}";
            RefreshConnectionIndicatorBindings();
            ClusterHealthText =
                $"{summary.WorkerCount} worker(s) online, "
                + $"{summary.ClientCount} client(s) attached, "
                + $"client_id={summary.ClientId}";

            TouchActiveConversation(
                $"client_id={summary.ClientId} · {summary.WorkerCount} workers · {summary.ClientCount} clients",
                ConnectionStatusText);
            AddSystemMessage($"Connected {summary.Host}:{summary.Port} · {summary.Source}");
        }
        catch (Exception ex)
        {
            AddSystemMessage($"Connection failed: {ex.Message}");
        }
    }

    private async Task EnsureConnectedAsync()
    {
        if (_backend.IsConnected)
        {
            return;
        }

        await ConnectAsync();
        if (!_backend.IsConnected)
        {
            throw new InvalidOperationException("Connection is still unavailable.");
        }
    }

    private async Task SendFixedVectorAsync(bool isAutomated)
    {
        if (_activeConversation is null)
        {
            return;
        }

        var conversation = _activeConversation;

        if (!TryParseVectorSeed(out var vectorSeed))
        {
            return;
        }
        if (!TryParseIterationCount(out var iterationCount))
        {
            return;
        }

        await _sendGate.WaitAsync();
        try
        {
            await EnsureConnectedAsync();

            var size = NormalizeGemvSize(conversation.Size);
            var vectorLength = ResolveGemvVectorLength(size);
            var objectId = ResolveObjectId(conversation);
            var streamId = ResolveStreamId(conversation);
            AddUserMessage(
                conversation,
                header: isAutomated ? "Auto-send" : "Me",
                body:
                $"Send {GemvMethod}\n"
                + $"size={size}\n"
                + $"vector_length={vectorLength}\n"
                + $"seed=0x{vectorSeed:X16}\n"
                + $"iteration_count={iterationCount}\n"
                + $"object_id={(string.IsNullOrWhiteSpace(objectId) ? "(auto)" : objectId)}",
                meta: BuildRequestMeta(isAutomated));

            var summary = await _backend.SendFixedVectorAsync(
                clientName: GetClientName(),
                size: size,
                vectorSeed: vectorSeed,
                iterationCount: iterationCount,
                objectId: objectId,
                streamId: streamId,
                onAccepted: acknowledgement =>
                {
                    DispatcherQueue.TryEnqueue(() => AddRequestAcceptedMessage(conversation, acknowledgement));
                });

            AddClusterResponse(conversation, summary);
        }
        catch (Exception ex)
        {
            AddSystemMessage(conversation, $"GEMV request failed: {ex.Message}");
        }
        finally
        {
            _sendGate.Release();
        }
    }

    private async Task SendConv2dAsync(bool isAutomated)
    {
        if (_activeConversation is null)
        {
            return;
        }

        var conversation = _activeConversation;

        if (!TryParseIterationCount(out var iterationCount))
        {
            return;
        }

        await _sendGate.WaitAsync();
        try
        {
            await EnsureConnectedAsync();

            var size = NormalizeConv2dSize(conversation.Size);
            var shape = ResolveConv2dShape(size);
            var objectId = ResolveObjectId(conversation, $"conv2d/{size}");
            var streamId = ResolveStreamId(conversation);
            var responseMode = conversation.Conv2dStatsOnly ? 1U : 0U;
            var statsMaxSamples = TryParseConv2dStatsMaxSamples(conversation, out var parsedStatsMaxSamples)
                ? parsedStatsMaxSamples
                : 0U;
            if (conversation.Conv2dStatsOnly && statsMaxSamples == 0U)
            {
                return;
            }
            var preferredOutputName = conversation.Conv2dStatsOnly
                ? null
                : ResolveConv2dDownloadName(conversation) ?? BuildAutoConv2dDownloadName(size);

            AddUserMessage(
                conversation,
                header: isAutomated ? "Auto-send" : "Me",
                body:
                $"Run {Conv2dMethod}\n"
                + $"size={size}\n"
                + $"shape={shape.TensorH}x{shape.TensorW} · {shape.ChannelsIn}->{shape.ChannelsOut} · k={shape.KernelSize} · pad={shape.Padding} · stride={shape.Stride}\n"
                + $"iteration_count={iterationCount}\n"
                + $"client_response_mode={(responseMode == 1U ? "stats_only" : "full")}\n"
                + (responseMode == 1U
                    ? $"stats_max_samples={statsMaxSamples}"
                    : $"download_name={(string.IsNullOrWhiteSpace(preferredOutputName) ? "(auto)" : preferredOutputName)}"),
                meta: BuildRequestMeta(isAutomated));

            var summary = await _backend.SendConv2dAsync(
                clientName: GetClientName(),
                size: size,
                shape: shape,
                clientResponseMode: responseMode,
                statsMaxSamples: statsMaxSamples,
                iterationCount: iterationCount,
                objectId: objectId,
                streamId: streamId,
                preferredOutputName: preferredOutputName,
                onAccepted: acknowledgement =>
                {
                    DispatcherQueue.TryEnqueue(() => AddRequestAcceptedMessage(
                        conversation,
                        acknowledgement,
                        localDownloadName: preferredOutputName));
                });

            AddClusterResponse(conversation, summary);
        }
        catch (Exception ex)
        {
            AddSystemMessage(conversation, $"Conv2d request failed: {ex.Message}");
        }
        finally
        {
            _sendGate.Release();
        }
    }

    private async Task SendManualContentAsync(bool isAutomated)
    {
        if (_activeConversation is null)
        {
            return;
        }

        var conversation = _activeConversation;

        var content = isAutomated ? AutoContentTextBox.Text : ManualInputTextBox.Text;
        if (string.IsNullOrWhiteSpace(content))
        {
            AddSystemMessage(conversation, "No text content to send yet.");
            return;
        }
        if (!TryParseIterationCount(out var iterationCount))
        {
            return;
        }

        await _sendGate.WaitAsync();
        try
        {
            await EnsureConnectedAsync();

            AddUserMessage(
                conversation,
                header: isAutomated ? "Auto-send" : "Me",
                body: content.Trim(),
                meta: $"{BuildRequestMeta(isAutomated)} method={FreeContentMethod} iteration_count={iterationCount}");

            var summary = await _backend.SendFreeContentAsync(
                clientName: GetClientName(),
                method: FreeContentMethod,
                content: content,
                iterationCount: iterationCount,
                objectId: ResolveObjectId(conversation),
                streamId: ResolveStreamId(conversation),
                onAccepted: acknowledgement =>
                {
                    DispatcherQueue.TryEnqueue(() => AddRequestAcceptedMessage(conversation, acknowledgement));
                });

            AddClusterResponse(conversation, summary);
            if (!isAutomated)
            {
                ManualInputTextBox.Text = string.Empty;
            }
        }
        catch (Exception ex)
        {
            AddSystemMessage(conversation, $"Text request failed: {ex.Message}");
        }
        finally
        {
            _sendGate.Release();
        }
    }

    private void AddClusterResponse(ConversationPreview conversation, RequestSummary summary)
    {
        AddMessageToConversation(
            conversation,
            alignment: HorizontalAlignment.Left,
            bubbleBackground: GetBrush("PeerBubbleBrush"),
            bubbleBorder: GetBrush("PeerBubbleBorderBrush"),
            foreground: GetBrush("PrimaryTextBrush"),
            header: "Main node",
            body: summary.StatusCode == 200 ? BuildSuccessBody(summary) : $"status_code={summary.StatusCode}\n{summary.ErrorMessage}",
            meta:
                $"request_id={summary.RequestId} · method={summary.Method} · "
                + $"iteration_count={summary.IterationCount} · elapsed={summary.ElapsedMs}ms"
                + $" · client={summary.ClientElapsedMs}ms · overhead={summary.TransmissionOverheadMs}ms",
            avatarText: "SW",
            avatarBackground: GetBrush("AvatarTealBrush"),
            avatarForeground: GetBrush("AvatarLetterBrush"),
            avatarVisibility: Visibility.Visible,
            headerVisibility: Visibility.Visible);

        if (summary.OutputSaved && !string.IsNullOrWhiteSpace(summary.LocalOutputPath))
        {
            AddMessageToConversation(
                conversation,
                alignment: HorizontalAlignment.Left,
                bubbleBackground: GetBrush("SystemBubbleBrush"),
                bubbleBorder: GetBrush("SystemBubbleBorderBrush"),
                foreground: GetBrush("PrimaryTextBrush"),
                header: summary.ResultArtifact is not null ? "Download complete" : "Output saved",
                body:
                    $"{(summary.ResultArtifact is not null ? "Cluster result saved locally." : "Inline output saved locally.")}\n"
                    + $"{summary.LocalOutputPath}"
                    + (string.IsNullOrWhiteSpace(summary.OutputChecksum) ? string.Empty : $"\nchecksum={summary.OutputChecksum}"),
                meta: DateTime.Now.ToString("HH:mm"),
                avatarText: "DL",
                avatarBackground: GetBrush("AvatarPinkBrush"),
                avatarForeground: GetBrush("AvatarLetterBrush"),
                avatarVisibility: Visibility.Visible,
                headerVisibility: Visibility.Visible,
                actionText: "Open download location",
                actionTargetPath: summary.LocalOutputPath,
                actionVisibility: Visibility.Visible);
        }
        else if (!string.IsNullOrWhiteSpace(summary.OutputSaveError))
        {
            AddSystemMessage(conversation, $"Saving cluster output failed: {summary.OutputSaveError}");
        }

        ClusterHealthText =
            $"{summary.WorkerCount} worker(s), "
            + $"{summary.ClientCount} client(s), "
            + $"latest request_id={summary.RequestId}";
        if (ReferenceEquals(conversation, _activeConversation))
        {
            TouchActiveConversation(
                $"request_id={summary.RequestId} · {summary.Method} · status={summary.StatusCode}",
                string.IsNullOrWhiteSpace(summary.ClientId)
                    ? ConnectionStatusText
                    : $"client_id={summary.ClientId} · worker_count={summary.WorkerCount} · client_count={summary.ClientCount}");
        }
    }

    private string BuildSuccessBody(RequestSummary summary)
    {
        if (!string.IsNullOrWhiteSpace(summary.OutputText))
        {
            return summary.OutputText;
        }

        var lines = new List<string>
        {
            $"status_code={summary.StatusCode}",
            $"request_accept_latency_ms={summary.RequestAcceptLatencyMs}",
            $"client_elapsed_ms={summary.ClientElapsedMs}",
            $"transmission_overhead_ms={summary.TransmissionOverheadMs}",
        };

        if (summary.Method == Conv2dMethod)
        {
            if (summary.Conv2dStats is not null && summary.Conv2dStats.StatsElementCount > 0)
            {
                var mean = summary.Conv2dStats.StatsElementCount > 0
                    ? summary.Conv2dStats.StatsSum / summary.Conv2dStats.StatsElementCount
                    : 0.0;
                var variance = summary.Conv2dStats.StatsElementCount > 0
                    ? Math.Max(summary.Conv2dStats.StatsSumSquares / summary.Conv2dStats.StatsElementCount - (mean * mean), 0.0)
                    : 0.0;
                lines.Add($"stats_only element_count={summary.Conv2dStats.StatsElementCount}");
                lines.Add($"sum={summary.Conv2dStats.StatsSum:0.000000}");
                lines.Add($"sum_squares={summary.Conv2dStats.StatsSumSquares:0.000000}");
                lines.Add($"mean={mean:0.000000}");
                lines.Add($"variance={variance:0.000000}");
                if (summary.Conv2dStats.StatsSamples.Count > 0)
                {
                    lines.Add($"samples=[{string.Join(", ", summary.Conv2dStats.StatsSamples.Select(value => value.ToString("0.000000")))}]");
                }
                AppendTimingLines(lines, summary.Timing);
                return string.Join("\n", lines);
            }

            if (summary.ResultArtifact is not null)
            {
                lines.Add($"output_length={summary.OutputLength}");
                lines.Add($"artifact_id={summary.ResultArtifact.ArtifactId}");
                lines.Add($"artifact_bytes={summary.ResultArtifact.SizeBytes}");
                lines.Add($"artifact_host={summary.ResultArtifact.TransferHost}:{summary.ResultArtifact.TransferPort}");
                AppendTimingLines(lines, summary.Timing);
                return string.Join("\n", lines);
            }

            if (summary.OutputValues.Count > 0)
            {
                var preview = string.Join(", ", summary.OutputValues.Take(8).Select(value => value.ToString("0.000000")));
                lines.Add($"output_length={summary.OutputLength}");
                lines.Add($"inline_preview=[{preview}]");
                AppendTimingLines(lines, summary.Timing);
                return string.Join("\n", lines);
            }

            lines.Add("conv2d finished, but there is no inline output.");
            AppendTimingLines(lines, summary.Timing);
            return string.Join("\n", lines);
        }

        if (summary.OutputValues.Count == 0)
        {
            lines.Add("Request finished, but no float vector payload was returned.");
            AppendTimingLines(lines, summary.Timing);
            return string.Join("\n", lines);
        }

        var previewCount = Math.Min(8, summary.OutputValues.Count);
        var vectorPreview = string.Join(", ", summary.OutputValues.Take(previewCount).Select(value => value.ToString("0.000000")));
        lines.Add($"iteration_count={summary.IterationCount}");
        lines.Add($"output_length={summary.OutputLength}");
        lines.Add($"preview=[{vectorPreview}]");
        AppendTimingLines(lines, summary.Timing);
        return string.Join("\n", lines);
    }

    private static void AppendTimingLines(List<string> lines, ResponseTimingSummary? timing)
    {
        if (timing is null)
        {
            return;
        }

        lines.Add($"timing dispatch_ms={timing.DispatchMs} task_window_ms={timing.TaskWindowMs} aggregate_ms={timing.AggregateMs}");
        foreach (var worker in timing.Workers)
        {
            var extras = new List<string>();
            if (worker.ArtifactFetchMs > 0)
            {
                extras.Add($"fetch_ms={worker.ArtifactFetchMs}");
            }
            if (worker.ComputationMs > 0)
            {
                extras.Add($"compute_ms={worker.ComputationMs}");
            }
            if (worker.PeripheralMs > 0)
            {
                extras.Add($"peripheral_ms={worker.PeripheralMs}");
            }

            var suffix = extras.Count > 0 ? $" {string.Join(" ", extras)}" : string.Empty;
            lines.Add(
                $"worker node={(string.IsNullOrWhiteSpace(worker.NodeId) ? "<?>" : worker.NodeId)} "
                + $"slice={(string.IsNullOrWhiteSpace(worker.Slice) ? "<?>" : worker.Slice)} "
                + $"wall_ms={worker.WallMs}{suffix}");
        }
    }

    private void AddRequestAcceptedMessage(
        ConversationPreview conversation,
        RequestAcceptedSummary acknowledgement,
        string? localDownloadName = null)
    {
        var acceptedAt = acknowledgement.AcceptedTimestampMs > 0
            ? DateTimeOffset.FromUnixTimeMilliseconds((long)acknowledgement.AcceptedTimestampMs).LocalDateTime.ToString("HH:mm:ss")
            : DateTime.Now.ToString("HH:mm:ss");
        var acceptanceNote = string.IsNullOrWhiteSpace(localDownloadName)
            ? "The main node accepted the request and is waiting on cluster results, please wait."
            : $"local_download_name={localDownloadName}\n"
                + "The main node accepted the request and is waiting on cluster results, please wait.";
        var dataPlaneDetail = string.IsNullOrWhiteSpace(acknowledgement.UploadId)
            && string.IsNullOrWhiteSpace(acknowledgement.DownloadId)
            ? string.Empty
            : "\n"
                + $"upload_id={(string.IsNullOrWhiteSpace(acknowledgement.UploadId) ? "<none>" : acknowledgement.UploadId)}\n"
                + $"download_id={(string.IsNullOrWhiteSpace(acknowledgement.DownloadId) ? "<none>" : acknowledgement.DownloadId)}\n"
                + $"data_endpoint={(string.IsNullOrWhiteSpace(acknowledgement.DataEndpointHost) ? "<none>" : acknowledgement.DataEndpointHost)}"
                + (acknowledgement.DataEndpointPort > 0 ? $":{acknowledgement.DataEndpointPort}" : string.Empty);

        AddMessageToConversation(
            conversation,
            alignment: HorizontalAlignment.Left,
            bubbleBackground: GetBrush("SystemBubbleBrush"),
            bubbleBorder: GetBrush("SystemBubbleBorderBrush"),
            foreground: GetBrush("PrimaryTextBrush"),
            header: "Accepted by main node",
            body:
                $"task_id={acknowledgement.TaskId}\n"
                + $"method={acknowledgement.Method}\n"
                + $"size={(string.IsNullOrWhiteSpace(acknowledgement.Size) ? "(default)" : acknowledgement.Size)}\n"
                + acceptanceNote
                + dataPlaneDetail,
            meta: $"accepted {acceptedAt}",
            avatarText: "ACK",
            avatarBackground: GetBrush("AvatarPinkBrush"),
            avatarForeground: GetBrush("AvatarLetterBrush"),
            avatarVisibility: Visibility.Visible,
            headerVisibility: Visibility.Visible);
    }

    private void AddUserMessage(ConversationPreview conversation, string header, string body, string meta)
    {
        AddMessageToConversation(
            conversation,
            alignment: HorizontalAlignment.Right,
            bubbleBackground: GetBrush("UserBubbleBrush"),
            bubbleBorder: GetBrush("UserBubbleBorderBrush"),
            foreground: GetBrush("PrimaryTextBrush"),
            header: header,
            body: body,
            meta: meta,
            avatarText: "Me",
            avatarBackground: GetBrush("UserBubbleBrush"),
            avatarForeground: GetBrush("PrimaryTextBrush"),
            avatarVisibility: Visibility.Collapsed,
            headerVisibility: Visibility.Collapsed);
    }

    private void AddUserMessage(string header, string body, string meta)
    {
        if (_activeConversation is null)
        {
            return;
        }

        AddUserMessage(_activeConversation, header, body, meta);
    }

    private void AddSystemMessage(ConversationPreview conversation, string body)
    {
        AddMessageToConversation(
            conversation,
            alignment: HorizontalAlignment.Left,
            bubbleBackground: GetBrush("SystemBubbleBrush"),
            bubbleBorder: GetBrush("SystemBubbleBorderBrush"),
            foreground: GetBrush("PrimaryTextBrush"),
            header: "System",
            body: body,
            meta: DateTime.Now.ToString("HH:mm"),
            avatarText: "SYS",
            avatarBackground: GetBrush("AvatarPinkBrush"),
            avatarForeground: GetBrush("AvatarLetterBrush"),
            avatarVisibility: Visibility.Visible,
            headerVisibility: Visibility.Visible);
    }

    private void AddSystemMessage(string body)
    {
        if (_activeConversation is null)
        {
            return;
        }

        AddSystemMessage(_activeConversation, body);
    }

    private void AddMessageToActiveConversation(
        HorizontalAlignment alignment,
        Brush bubbleBackground,
        Brush bubbleBorder,
        Brush foreground,
        string header,
        string body,
        string meta,
        string avatarText,
        Brush avatarBackground,
        Brush avatarForeground,
        Visibility avatarVisibility,
        Visibility headerVisibility)
    {
        if (_activeConversation is null)
        {
            return;
        }

        AddMessageToConversation(
            _activeConversation,
            alignment,
            bubbleBackground,
            bubbleBorder,
            foreground,
            header,
            body,
            meta,
            avatarText,
            avatarBackground,
            avatarForeground,
            avatarVisibility,
            headerVisibility);
        TouchActiveConversation($"{header}: {body}");
    }

    private void AddMessageToConversation(
        ConversationPreview conversation,
        HorizontalAlignment alignment,
        Brush bubbleBackground,
        Brush bubbleBorder,
        Brush foreground,
        string header,
        string body,
        string meta,
        string avatarText,
        Brush avatarBackground,
        Brush avatarForeground,
        Visibility avatarVisibility,
        Visibility headerVisibility,
        string actionText = "",
        string actionTargetPath = "",
        Visibility? actionVisibility = null)
    {
        var margin = alignment == HorizontalAlignment.Right
            ? new Thickness(110, 0, 0, 0)
            : new Thickness(0, 0, 110, 0);

        var message = new ChatMessage
        {
            Header = header,
            Body = body,
            Meta = meta,
            ColumnIndex = alignment == HorizontalAlignment.Right ? 1 : 0,
            Alignment = alignment,
            OuterMargin = margin,
            BubbleBackground = bubbleBackground,
            BubbleBorder = bubbleBorder,
            Foreground = foreground,
            ActionText = actionText,
            ActionTargetPath = actionTargetPath,
            ActionVisibility = actionVisibility ?? (string.IsNullOrWhiteSpace(actionText) ? Visibility.Collapsed : Visibility.Visible),
            AvatarText = avatarText,
            AvatarBackground = avatarBackground,
            AvatarForeground = avatarForeground,
            AvatarVisibility = avatarVisibility,
            HeaderVisibility = headerVisibility,
        };

        conversation.Transcript.Add(message);
        conversation.Snippet = CollapsePreview($"{header}: {body}");
        conversation.Timestamp = DateTime.Now.ToString("HH:mm");

        if (ReferenceEquals(conversation, _activeConversation))
        {
            Messages.Add(message);
            if (MessagesView is not null)
            {
                MessagesView.ScrollIntoView(message);
            }
        }
        else
        {
            conversation.BadgeText = "New";
            conversation.BadgeVisibility = Visibility.Visible;
        }
    }

    private void StopAutoSend(bool logMessage)
    {
        if (!_autoTimer.IsRunning)
        {
            return;
        }

        _autoTimer.Stop();
        if (logMessage)
        {
            AddSystemMessage("Auto-send stopped.");
        }
    }

    private bool TryParseVectorSeed(out ulong vectorSeed)
    {
        var raw = NormalizeOptionalText(VectorSeedTextBox.Text) ?? "0x0FEDCBA987654321";
        var normalized = raw.StartsWith("0x", StringComparison.OrdinalIgnoreCase) ? raw[2..] : raw;
        if (ulong.TryParse(normalized, System.Globalization.NumberStyles.HexNumber, null, out vectorSeed))
        {
            return true;
        }

        AddSystemMessage("Vector seed must be a valid 64-bit unsigned integer, e.g. 0x0FEDCBA987654321.");
        return false;
    }

    private bool TryParseIterationCount(out uint iterationCount)
    {
        if (uint.TryParse(IterationCountTextBox.Text, out iterationCount) && iterationCount > 0)
        {
            return true;
        }

        AddSystemMessage("iteration_count must be a positive integer.");
        return false;
    }

    private int? ParsePort()
    {
        if (string.IsNullOrWhiteSpace(PortTextBox.Text))
        {
            return null;
        }

        if (int.TryParse(PortTextBox.Text.Trim(), out var port) && port is > 0 and <= 65535)
        {
            return port;
        }

        AddSystemMessage("TCP port must be an integer between 1 and 65535.");
        return null;
    }

    private string GetClientName()
    {
        return string.IsNullOrWhiteSpace(ClientNameTextBox.Text)
            ? "windows winui3 client"
            : ClientNameTextBox.Text.Trim();
    }

    private string? ResolveObjectId(ConversationPreview conversation, string? fallback = null)
    {
        return NormalizeOptionalText(ObjectIdTextBox.Text)
            ?? NormalizeOptionalText(conversation.ObjectId)
            ?? NormalizeOptionalText(fallback);
    }

    private string? ResolveStreamId(ConversationPreview conversation)
    {
        return NormalizeOptionalText(StreamIdTextBox.Text)
            ?? NormalizeOptionalText(conversation.StreamId);
    }

    private static string? ResolveConv2dDownloadName(ConversationPreview conversation)
    {
        return NormalizeOptionalText(conversation.Conv2dDownloadName);
    }

    private static string BuildAutoConv2dDownloadName(string size)
    {
        return $"{BuildLocalRequestId("conv2d")}-{NormalizeConv2dSize(size)}";
    }

    private static string BuildLocalRequestId(string prefix)
    {
        var normalizedPrefix = (prefix ?? string.Empty).Trim().ToLowerInvariant();
        if (string.IsNullOrWhiteSpace(normalizedPrefix))
        {
            normalizedPrefix = "req";
        }

        normalizedPrefix = normalizedPrefix.Split('-', 2)[0];
        if (normalizedPrefix.Length > 4)
        {
            normalizedPrefix = normalizedPrefix[..4];
        }

        var next = Interlocked.Increment(ref _localRequestCounter);
        return $"{normalizedPrefix}-{next:x}";
    }

    private bool TryParseConv2dStatsMaxSamples(ConversationPreview conversation, out uint statsMaxSamples)
    {
        statsMaxSamples = 0;
        if (!conversation.Conv2dStatsOnly)
        {
            return true;
        }

        var raw = NormalizeOptionalText(Conv2dStatsMaxSamplesTextBox?.Text)
            ?? NormalizeOptionalText(conversation.Conv2dStatsMaxSamples)
            ?? "16";
        if (uint.TryParse(raw, out statsMaxSamples) && statsMaxSamples > 0)
        {
            return true;
        }

        AddSystemMessage("Conv2d max stats samples must be a positive integer.");
        return false;
    }

    private string GetSelectedGemvSize()
    {
        if (GemvSizeComboBox.SelectedItem is ComboBoxItem item && item.Content is string value)
        {
            return NormalizeGemvSize(value);
        }

        return NormalizeGemvSize(_activeConversation?.Size);
    }

    private string GetSelectedConv2dSize()
    {
        if (Conv2dSizeComboBox.SelectedItem is ComboBoxItem item && item.Content is string value)
        {
            return NormalizeConv2dSize(value);
        }

        return NormalizeConv2dSize(_activeConversation?.Size);
    }

    private static string NormalizeGemvSize(string? size)
    {
        return (size ?? string.Empty).Trim().ToLowerInvariant() switch
        {
            "small" => "small",
            "mid" => "mid",
            "medium" => "mid",
            _ => "large",
        };
    }

    private static uint ResolveGemvVectorLength(string? size)
    {
        return NormalizeGemvSize(size) switch
        {
            "small" => 4096U,
            "mid" => 16384U,
            _ => 32768U,
        };
    }

    private static string NormalizeConv2dSize(string? size)
    {
        return (size ?? string.Empty).Trim().ToLowerInvariant() switch
        {
            "mid" => "mid",
            "large" => "large",
            _ => "small",
        };
    }

    private static Conv2dRequestShape ResolveConv2dShape(string size)
    {
        return NormalizeConv2dSize(size) switch
        {
            "mid" => new Conv2dRequestShape(1024, 1024, 128, 256, 3, 1, 1),
            "large" => new Conv2dRequestShape(2048, 2048, 128, 256, 3, 1, 1),
            _ => new Conv2dRequestShape(256, 256, 32, 64, 3, 1, 1),
        };
    }

    private static string BuildConv2dRequestSummary(ConversationPreview conversation)
    {
        var shape = ResolveConv2dShape(conversation.Size ?? "small");
        var modeDetail = conversation.Conv2dStatsOnly
            ? $"stats_only · max_samples={conversation.Conv2dStatsMaxSamples}"
            : $"full · download_name={(string.IsNullOrWhiteSpace(conversation.Conv2dDownloadName) ? "(auto)" : conversation.Conv2dDownloadName)}";
        return
            $"Current Conv2d shape: {shape.TensorH}x{shape.TensorW} · {shape.ChannelsIn}->{shape.ChannelsOut} · "
            + $"k={shape.KernelSize} · pad={shape.Padding} · stride={shape.Stride}\n"
            + $"response_mode={modeDetail}";
    }

    private static string BuildGemvRequestSummary(string? size)
    {
        var resolvedSize = NormalizeGemvSize(size);
        var vectorLength = ResolveGemvVectorLength(resolvedSize);
        return
            $"Current GEMV matrix size: {resolvedSize} · request_vector_length={vectorLength} · "
            + "the seed maps to a deterministic float32 request vector.";
    }

    private static string? NormalizeOptionalText(string? value)
    {
        return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    }

    private string BuildRequestMeta(bool isAutomated)
    {
        var source = isAutomated ? "Auto" : "Manual";
        return $"{source} {DateTime.Now:HH:mm}";
    }

    private string BuildDisplayDiagnosticsMessage()
    {
        var windowHandle = WinRT.Interop.WindowNative.GetWindowHandle(this);
        var dpiContext = windowHandle != IntPtr.Zero ? GetWindowDpiAwarenessContext(windowHandle) : IntPtr.Zero;
        var awareness = DescribeDpiAwareness(dpiContext);
        var windowDpi = windowHandle != IntPtr.Zero ? GetDpiForWindow(windowHandle) : 0U;
        var rasterizationScale = 0.0;
        if (Content is FrameworkElement root && root.XamlRoot is not null)
        {
            rasterizationScale = root.XamlRoot.RasterizationScale;
        }

        var message =
            $"Display diagnostics: awareness={awareness}, window_dpi={windowDpi}, "
            + $"rasterization_scale={rasterizationScale:0.###}, "
            + RuntimeBootstrap.DescribeStartupState();
        if (awareness != "PerMonitorV2")
        {
            message += "\nIn this mode Windows may apply bitmap scaling to the whole app, which can blur the UI.";
        }
        else
        {
            message +=
                "\nPerMonitorV2 should avoid bitmap scaling in theory. "
                + "If the UI still looks blurry, check the EXE's Compatibility -> High DPI settings.";
        }

        return message;
    }

    private static void OpenDownloadLocation(string targetPath)
    {
        if (File.Exists(targetPath))
        {
            var absolute = Path.GetFullPath(targetPath);
            Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{absolute}\"")
            {
                UseShellExecute = true,
            });
            return;
        }

        if (Directory.Exists(targetPath))
        {
            var absolute = Path.GetFullPath(targetPath);
            Process.Start(new ProcessStartInfo("explorer.exe", $"\"{absolute}\"")
            {
                UseShellExecute = true,
            });
            return;
        }

        throw new FileNotFoundException("Download location does not exist.", targetPath);
    }

    private void QueueDisplayDiagnostics()
    {
        if (_reportedDisplayDiagnostics)
        {
            return;
        }

        if (!DispatcherQueue.TryEnqueue(() =>
            {
                if (_reportedDisplayDiagnostics)
                {
                    return;
                }

                _reportedDisplayDiagnostics = true;
                try
                {
                    AddSystemMessage(BuildDisplayDiagnosticsMessage());
                }
                catch (Exception ex)
                {
                    AddSystemMessage($"Failed to read display diagnostics: {ex.Message}");
                }
            }))
        {
            _reportedDisplayDiagnostics = true;
            AddSystemMessage("Unable to queue display diagnostics onto the UI dispatcher.");
        }
    }

    private static string DescribeDpiAwareness(nint dpiContext)
    {
        if (AreDpiAwarenessContextsEqual(dpiContext, DpiAwarenessContextPerMonitorAwareV2))
        {
            return "PerMonitorV2";
        }
        if (AreDpiAwarenessContextsEqual(dpiContext, DpiAwarenessContextPerMonitorAware))
        {
            return "PerMonitor";
        }
        if (AreDpiAwarenessContextsEqual(dpiContext, DpiAwarenessContextSystemAware))
        {
            return "SystemAware";
        }
        if (AreDpiAwarenessContextsEqual(dpiContext, DpiAwarenessContextUnawareGdiScaled))
        {
            return "UnawareGdiScaled";
        }
        if (AreDpiAwarenessContextsEqual(dpiContext, DpiAwarenessContextUnaware))
        {
            return "Unaware";
        }

        return "Unknown";
    }

    private Brush GetBrush(string resourceKey)
    {
        return (Brush)Application.Current.Resources[resourceKey];
    }

    private void RefreshConnectionIndicatorBindings()
    {
        RaisePropertyChanged(nameof(ConnectionIndicatorBrush));
        RaisePropertyChanged(nameof(ConnectionIndicatorBorderBrush));
        RaisePropertyChanged(nameof(ConnectionIndicatorPanelBrush));
    }

    private void SetSidebarMode(SidebarMode sidebarMode)
    {
        if (_sidebarMode == sidebarMode)
        {
            return;
        }

        _sidebarMode = sidebarMode;
        RaisePropertyChanged(nameof(SidebarSectionTitle));
        RaisePropertyChanged(nameof(SidebarSearchPlaceholderText));
        RaisePropertyChanged(nameof(ConversationListVisibility));
        RaisePropertyChanged(nameof(ContactListVisibility));
        RaisePropertyChanged(nameof(ConversationsRailButtonBackground));
        RaisePropertyChanged(nameof(ConversationsRailButtonForeground));
        RaisePropertyChanged(nameof(ConversationsRailButtonBorderBrush));
        RaisePropertyChanged(nameof(ContactsRailButtonBackground));
        RaisePropertyChanged(nameof(ContactsRailButtonForeground));
        RaisePropertyChanged(nameof(ContactsRailButtonBorderBrush));
    }

    private static string CollapsePreview(string text)
    {
        var normalized = string.Join(
            " ",
            text
                .Replace('\r', ' ')
                .Replace('\n', ' ')
                .Replace('\t', ' ')
                .Split(' ', StringSplitOptions.RemoveEmptyEntries));

        return normalized.Length <= 72 ? normalized : $"{normalized[..69]}...";
    }

    private void SetProperty<T>(ref T storage, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(storage, value))
        {
            return;
        }

        storage = value;
        RaisePropertyChanged(propertyName);
    }

    private void RaisePropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }

    [DllImport("user32.dll")]
    private static extern nint GetWindowDpiAwarenessContext(nint hwnd);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AreDpiAwarenessContextsEqual(nint dpiContextA, nint dpiContextB);

    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(nint hwnd);
}
