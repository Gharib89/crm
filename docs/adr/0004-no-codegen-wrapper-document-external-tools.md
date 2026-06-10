# No `crm codegen` wrapper — document the external tools instead

Early-bound class generation stays external (`CrmSvcUtil.exe` for on-prem,
`pac modelbuilder build` for Dataverse online); the skill documents the toolchain
rather than shipping a `crm codegen` verb. Wrapping `CrmSvcUtil.exe` would create a
verb that is Windows / .NET-Framework-only (this CLI also ships Linux binaries) and
would force a credential handoff that either leaks the secret onto a subprocess
command line or degrades to interactive login — adding nothing over a documented
command. This mirrors the reach limits of the SolutionPackager bridge (same
`Microsoft.CrmSdk.CoreTools` NuGet) without its payoff. See issue #194.
