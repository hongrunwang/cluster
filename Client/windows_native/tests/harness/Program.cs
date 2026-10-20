using System.Text.Json;
using SuperWebClient.WinUI3.Services;

namespace SuperWebClient.NativeBackendHarness;

internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        try
        {
            var options = ParseOptions(args);
            var scenario = GetRequiredOption(options, "scenario");
            var host = GetOption(options, "host") ?? "127.0.0.1";
            var port = int.Parse(GetRequiredOption(options, "port"));
            var clientName = GetOption(options, "client-name") ?? "native backend harness";
            var iterationCount = uint.TryParse(GetOption(options, "iteration-count"), out var parsedIterationCount)
                ? parsedIterationCount
                : 1U;
            var size = GetOption(options, "size") ?? "small";
            var objectId = GetOption(options, "object-id");
            var streamId = GetOption(options, "stream-id");
            Action<RequestAcceptedSummary> captureAccepted = _ => { };

            using var backend = new NativeClientBackend();
            var connection = await backend.ConnectAsync(clientName, host, port);

            RequestSummary summary = scenario switch
            {
                "free-content" => await backend.SendFreeContentAsync(
                    clientName,
                    "free_content",
                    GetOption(options, "content") ?? "hello from harness",
                    iterationCount,
                    objectId,
                    streamId,
                    captureAccepted),
                "gemv" => await backend.SendFixedVectorAsync(
                    clientName,
                    size,
                    ParseUnsignedLong(options, "vector-seed", 0x0FEDCBA987654321UL),
                    iterationCount,
                    objectId,
                    streamId,
                    captureAccepted),
                "conv2d-full" => await backend.SendConv2dAsync(
                    clientName,
                    size,
                    ResolveConv2dShape(size),
                    clientResponseMode: 0U,
                    statsMaxSamples: 0U,
                    iterationCount: iterationCount,
                    objectId: objectId,
                    streamId: streamId,
                    preferredOutputName: GetOption(options, "download-name"),
                    onAccepted: captureAccepted),
                "conv2d-stats" => await backend.SendConv2dAsync(
                    clientName,
                    size,
                    ResolveConv2dShape(size),
                    clientResponseMode: 1U,
                    statsMaxSamples: ParseUnsignedInt(options, "stats-max-samples", 4U),
                    iterationCount: iterationCount,
                    objectId: objectId,
                    streamId: streamId,
                    preferredOutputName: null,
                    onAccepted: captureAccepted),
                _ => throw new ArgumentException($"Unknown scenario: {scenario}"),
            };

            var payload = new HarnessResult(connection, summary);
            var json = JsonSerializer.Serialize(payload, new JsonSerializerOptions
            {
                WriteIndented = true,
            });
            Console.Out.WriteLine(json);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            return 1;
        }
    }

    private static Conv2dRequestShape ResolveConv2dShape(string? size)
    {
        return NormalizeConv2dSize(size) switch
        {
            "mid" => new Conv2dRequestShape(1024, 1024, 128, 256, 3, 1, 1),
            "large" => new Conv2dRequestShape(2048, 2048, 128, 256, 3, 1, 1),
            _ => new Conv2dRequestShape(256, 256, 32, 64, 3, 1, 1),
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

    private static Dictionary<string, string> ParseOptions(IReadOnlyList<string> args)
    {
        var options = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (var index = 0; index < args.Count; index++)
        {
            var argument = args[index];
            if (!argument.StartsWith("--", StringComparison.Ordinal))
            {
                continue;
            }

            var key = argument[2..];
            if (index + 1 < args.Count && !args[index + 1].StartsWith("--", StringComparison.Ordinal))
            {
                options[key] = args[index + 1];
                index++;
            }
            else
            {
                options[key] = "true";
            }
        }

        return options;
    }

    private static string GetRequiredOption(IReadOnlyDictionary<string, string> options, string key)
    {
        if (options.TryGetValue(key, out var value) && !string.IsNullOrWhiteSpace(value))
        {
            return value;
        }

        throw new ArgumentException($"Missing required option --{key}");
    }

    private static string? GetOption(IReadOnlyDictionary<string, string> options, string key)
    {
        return options.TryGetValue(key, out var value) ? value : null;
    }

    private static uint ParseUnsignedInt(IReadOnlyDictionary<string, string> options, string key, uint fallback)
    {
        var raw = GetOption(options, key);
        return uint.TryParse(raw, out var parsed) ? parsed : fallback;
    }

    private static ulong ParseUnsignedLong(IReadOnlyDictionary<string, string> options, string key, ulong fallback)
    {
        var raw = GetOption(options, key);
        if (string.IsNullOrWhiteSpace(raw))
        {
            return fallback;
        }

        var normalized = raw.StartsWith("0x", StringComparison.OrdinalIgnoreCase) ? raw[2..] : raw;
        return ulong.TryParse(normalized, System.Globalization.NumberStyles.HexNumber, null, out var hexParsed)
            ? hexParsed
            : ulong.TryParse(raw, out var parsed)
                ? parsed
                : fallback;
    }

    private sealed record HarnessResult(ConnectionSummary Connection, RequestSummary Summary);
}
