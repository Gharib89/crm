# No `crm codegen` wrapper — document the external tools instead

Early-bound class generation stays external (`CrmSvcUtil.exe` for on-prem,
`pac modelbuilder build` for Dataverse online); the skill documents the toolchain
rather than shipping a `crm codegen` verb. Wrapping `CrmSvcUtil.exe` would create a
verb that is Windows / .NET-Framework-only (this CLI also ships Linux binaries) and
would force a credential handoff that either leaks the secret onto a subprocess
command line or degrades to interactive login — adding nothing over a documented
command. The SolutionPackager bridge this once paralleled has since migrated to
the cross-platform `pac solution` verbs (#500); `CrmSvcUtil.exe`, by contrast,
has no cross-platform equivalent for on-prem codegen, so a `crm codegen` verb
would still be Windows-only without payoff. See issue #194.
