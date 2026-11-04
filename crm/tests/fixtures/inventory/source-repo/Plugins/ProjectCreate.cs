using Microsoft.Xrm.Sdk;

namespace Contoso.Plugins
{
    // class NotAType : IPlugin {}
    public sealed class ProjectCreate : IPlugin
    {
        private const string Brace = "{";
        public void Execute(System.IServiceProvider serviceProvider) { }
    }
}
