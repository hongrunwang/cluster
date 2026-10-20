using System;
using System.Runtime.InteropServices;

namespace SuperWebClient.WinUI3;

internal static class RuntimeBootstrap
{
    private static readonly nint DpiAwarenessContextPerMonitorAwareV2 = new(-4);
    private static bool _dpiCallSucceeded;
    private static int _dpiCallLastError;

    internal static void PrepareProcessEnvironment()
    {
        // Ask Windows for per-monitor DPI awareness before WinUI creates any UI,
        // which keeps text and controls crisp on Hi-DPI displays.
        _dpiCallSucceeded = SetProcessDpiAwarenessContext(DpiAwarenessContextPerMonitorAwareV2);
        _dpiCallLastError = _dpiCallSucceeded ? 0 : Marshal.GetLastWin32Error();

        // Single-file Windows App SDK apps expect this base directory hint so
        // the runtime can resolve its unpacked support files consistently.
        Environment.SetEnvironmentVariable(
            "MICROSOFT_WINDOWSAPPRUNTIME_BASE_DIRECTORY",
            AppContext.BaseDirectory);
    }

    internal static string DescribeStartupState()
    {
        return _dpiCallSucceeded
            ? "startup_dpi_call=ok"
            : $"startup_dpi_call=failed win32_error={_dpiCallLastError}";
    }

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetProcessDpiAwarenessContext(nint dpiContext);
}
