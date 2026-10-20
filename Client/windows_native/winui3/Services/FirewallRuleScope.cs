using System.Diagnostics;

namespace SuperWebClient.WinUI3.Services;

public sealed class FirewallRuleScope : IDisposable
{
    private static readonly (string Name, string Direction, string PortArgument)[] Rules =
    [
        ("Allow SuperWeb-Client UDP 5353 Inbound", "in", "localport=5353"),
        ("Allow SuperWeb-Client UDP 5353 Outbound", "out", "remoteport=5353"),
    ];

    private bool _active;

    private FirewallRuleScope()
    {
    }

    public static bool TryCreate(out FirewallRuleScope? scope, out string? error)
    {
        scope = null;
        error = null;

        var candidate = new FirewallRuleScope();
        try
        {
            candidate.AddRules();
            scope = candidate;
            return true;
        }
        catch (Exception ex)
        {
            candidate.Dispose();
            error = ex.Message;
            return false;
        }
    }

    public void Dispose()
    {
        if (!_active)
        {
            return;
        }

        foreach (var rule in Rules)
        {
            RunNetsh(ignoreFailures: true, "delete", "rule", $"name={rule.Name}");
        }

        _active = false;
    }

    private void AddRules()
    {
        foreach (var rule in Rules)
        {
            RunNetsh(
                ignoreFailures: false,
                "add",
                "rule",
                $"name={rule.Name}",
                $"dir={rule.Direction}",
                "action=allow",
                "protocol=UDP",
                rule.PortArgument,
                "enable=yes",
                "profile=any");
        }

        _active = true;
    }

    private static void RunNetsh(bool ignoreFailures, params string[] arguments)
    {
        using var process = new Process();
        process.StartInfo.FileName = "netsh";
        process.StartInfo.UseShellExecute = false;
        process.StartInfo.RedirectStandardOutput = true;
        process.StartInfo.RedirectStandardError = true;
        process.StartInfo.CreateNoWindow = true;
        process.StartInfo.ArgumentList.Add("advfirewall");
        process.StartInfo.ArgumentList.Add("firewall");
        foreach (var argument in arguments)
        {
            process.StartInfo.ArgumentList.Add(argument);
        }

        process.Start();
        var stdOut = process.StandardOutput.ReadToEnd();
        var stdErr = process.StandardError.ReadToEnd();
        process.WaitForExit();

        if (!ignoreFailures && process.ExitCode != 0)
        {
            var detail = string.IsNullOrWhiteSpace(stdErr) ? stdOut : stdErr;
            throw new InvalidOperationException($"Unable to update Windows Firewall for mDNS discovery: {detail.Trim()}");
        }
    }
}
