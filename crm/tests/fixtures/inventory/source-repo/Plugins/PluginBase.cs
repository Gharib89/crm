namespace Contoso.Plugins
{
    public abstract class PluginBase : Microsoft.Xrm.Sdk.IPlugin
    {
        public abstract void Execute(System.IServiceProvider serviceProvider);
    }
}
