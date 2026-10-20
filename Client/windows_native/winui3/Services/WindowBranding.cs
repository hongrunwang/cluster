using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace SuperWebClient.WinUI3.Services;

internal static class WindowBranding
{
    public static void Apply(Window window)
    {
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
        if (hwnd == IntPtr.Zero)
        {
            return;
        }

        var windowId = Win32Interop.GetWindowIdFromWindow(hwnd);
        var appWindow = AppWindow.GetFromWindowId(windowId);
        window.SystemBackdrop = new DesktopAcrylicBackdrop();

        appWindow.Resize(new SizeInt32(1560, 960));
        appWindow.Title = "superweb-client";

        var iconPath = BrandingPaths.TryGetAppIconPath();
        if (!string.IsNullOrWhiteSpace(iconPath))
        {
            appWindow.SetIcon(iconPath);
        }

        if (!AppWindowTitleBar.IsCustomizationSupported())
        {
            return;
        }

        var titleBar = appWindow.TitleBar;
        titleBar.BackgroundColor = Colors.Transparent;
        titleBar.ForegroundColor = ColorHelper.FromArgb(255, 27, 29, 46);
        titleBar.ButtonBackgroundColor = Colors.Transparent;
        titleBar.ButtonForegroundColor = ColorHelper.FromArgb(255, 27, 29, 46);
        titleBar.ButtonHoverBackgroundColor = ColorHelper.FromArgb(255, 236, 237, 252);
        titleBar.ButtonHoverForegroundColor = ColorHelper.FromArgb(255, 27, 29, 46);
        titleBar.ButtonPressedBackgroundColor = ColorHelper.FromArgb(255, 217, 219, 239);
        titleBar.ButtonPressedForegroundColor = ColorHelper.FromArgb(255, 27, 29, 46);
        titleBar.ButtonInactiveBackgroundColor = Colors.Transparent;
        titleBar.InactiveBackgroundColor = Colors.Transparent;
        titleBar.InactiveForegroundColor = ColorHelper.FromArgb(255, 115, 118, 154);
    }
}
