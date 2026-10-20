using System.IO;

namespace SuperWebClient.WinUI3.Services;

internal static class BrandingPaths
{
    private static readonly string BrandingRoot = Path.Combine(AppContext.BaseDirectory, "Assets", "Branding");

    public static string? TryGetAppIconPath()
    {
        return TryGetBrandingAssetPath("superweb-client.ico");
    }

    public static string? TryGetBrandImagePath()
    {
        return TryGetBrandingAssetPath("superweb-cluster.png");
    }

    private static string? TryGetBrandingAssetPath(string fileName)
    {
        var path = Path.Combine(BrandingRoot, fileName);
        return File.Exists(path) ? path : null;
    }
}
