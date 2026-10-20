using System.Threading;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;

namespace SuperWebClient.WinUI3;

public static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        RuntimeBootstrap.PrepareProcessEnvironment();
        WinRT.ComWrappersSupport.InitializeComWrappers();

        Application.Start(static _ =>
        {
            var context = new DispatcherQueueSynchronizationContext(DispatcherQueue.GetForCurrentThread());
            SynchronizationContext.SetSynchronizationContext(context);
            new App();
        });
    }
}
