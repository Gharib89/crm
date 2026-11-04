using Microsoft.Xrm.Sdk;

namespace Contoso.Plugins
{
    // class NotAType : IPlugin {}
    public sealed partial class ProjectCreate : IPlugin
    {
        private const string Brace = "{";
        public void Execute(System.IServiceProvider serviceProvider) { }
    }

    public class ProjectCreateLegacy : IPlugin
    {
        public void Execute(System.IServiceProvider serviceProvider) { }
    }
}
