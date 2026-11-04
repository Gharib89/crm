namespace Contoso.Plugins
{
    // Derives from the repo's own base class, not IPlugin directly.
    public class AccountMerge : PluginBase
    {
        public override void Execute(System.IServiceProvider serviceProvider) { }
    }
}
