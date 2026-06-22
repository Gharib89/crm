using System;
using Microsoft.Xrm.Sdk;

namespace CrmCli
{
    /// <summary>
    /// A no-op plug-in that exists only to give the assembly register/unregister
    /// lifecycle e2e test a real, strong-named <see cref="IPlugin"/> type to
    /// register a step against. It is never executed: the test registers a step
    /// then unregisters it (and the assembly) without ever triggering the message,
    /// so <see cref="Execute"/> is intentionally empty.
    /// </summary>
    public sealed class NoOpPlugin : IPlugin
    {
        public void Execute(IServiceProvider serviceProvider)
        {
            // Registration is asserted, not execution — nothing to do here.
        }
    }
}
