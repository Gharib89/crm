# .NET

## Signals
Kind: stack
Manifest: `*.csproj`
Lockfile: packages.lock.json (opt-in, `RestorePackagesWithLockFile`)
Workspace: `*.sln`, `*.slnx`
Extensions: .cs
Shebangs: None.
Runtime version: global.json, .tool-versions, mise.toml
Library: a `*.csproj` setting `<IsPackable>true</IsPackable>` or a `<PackageId>`

## format

### dotnet format whitespace
Publisher: Microsoft
Tier: 2: https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-format
Evidence: `.editorconfig`
Rung: edit
Run: `dotnet format whitespace . --folder --include {files}`
Hook: local
Pin: apt dotnet-sdk-10.0
Route: `sudo apt-get update && sudo apt-get install -y dotnet-sdk-10.0`; Blocked: `dotnet-install.sh` (redirects to `builds.dotnet.microsoft.com`)
Constraints: ships with the SDK, which noble's own apt carries. `--folder` formats the files without loading the project (1.6 s, measured); plain `dotnet format --include` still loads the solution (6.1 s) and misses the edit budget. `packages.microsoft.com` passes but carries no .NET for Ubuntu 24.04. The route updates the apt index first: the image's index goes stale, and its SDK `.deb`s then 404 (measured in the entry trial).
Traps: whitespace only: style and analyzer rules need the project loaded, so they ride the build (see `dotnet build`).

## typecheck

### dotnet build
Publisher: Microsoft
Tier: 2: https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-build
Evidence: None.
Rung: turn
Run: `dotnet build -warnaserror`
Hook: local
Pin: apt dotnet-sdk-10.0
Route: `sudo apt-get update && sudo apt-get install -y dotnet-sdk-10.0`; Blocked: `dotnet-install.sh` (redirects to `builds.dotnet.microsoft.com`)
Constraints: a project is the smallest unit it takes. Canonical ships the 1xx feature band only, so a `global.json` pinning another band or major fails on this route unless its `rollForward` admits 10.0.1xx.
Traps: Roslyn analyzers configured in `.editorconfig` fail the build only with `EnforceCodeStyleInBuild=true`. The build restores implicitly, reaching `api.nuget.org`, which the cloud sandbox passes.

## test runner

### dotnet test
Publisher: Microsoft
Tier: 2: https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-test
Evidence: a project referencing `Microsoft.NET.Test.Sdk`
Rung: turn
Run: `dotnet test`
Hook: local
Pin: apt dotnet-sdk-10.0
Route: `sudo apt-get update && sudo apt-get install -y dotnet-sdk-10.0`; Blocked: `dotnet-install.sh` (redirects to `builds.dotnet.microsoft.com`)
Constraints: None.
Traps: None.

## affected tests

### dotnet test by project reference
Publisher: Microsoft
Tier: 2: https://learn.microsoft.com/dotnet/core/testing/selective-unit-tests
Evidence: `ProjectReference` items in test projects
Rung: turn
Run: `dotnet test`
Hook: local
Pin: apt dotnet-sdk-10.0
Route: `sudo apt-get update && sudo apt-get install -y dotnet-sdk-10.0`; Blocked: `dotnet-install.sh` (redirects to `builds.dotnet.microsoft.com`)
Constraints: `--filter` selects by name or trait only, so selection is by project. A test-project member's row is the `Run:` line. Any other member's row is `dotnet test <project>` for each test project whose `ProjectReference` chain reaches it (its reverse dependents), joined with `&&`. MSBuild skips projects nothing changed.
Traps: a project added later changes the reverse dependents; re-run setup-harness to rewrite the row.

## language server

### csharp-ls
Publisher: razzmatazz
Tier: 1: https://github.com/anthropics/claude-plugins-official (the `csharp-lsp` entry names it)
Evidence: `.claude/skills/harness-csharp-lsp/`, `csharp-lsp@claude-plugins-official` in `.claude/settings.json` `enabledPlugins`, `csharp-ls` in `.config/dotnet-tools.json`
Rung: None.
Run: `dotnet tool run csharp-ls`
Hook: None.
Pin: package nuget csharp-ls
Route: None.
Constraints: vendored from the Anthropic `csharp-lsp` plugin per [reference/language-servers.md](../reference/language-servers.md), pinned as a local tool at the stack root (`dotnet new tool-manifest`, then `dotnet tool install csharp-ls --version <v>`), which `dotnet tool restore` installs. Needs the .NET 10 SDK (0.28.0 targets `net10.0`; the plugin README's ".NET SDK 6.0 or later" is stale); with no `dotnet` on `PATH` the tool is `Unavailable: csharp-ls needs the .NET 10 SDK`.
Local-only: cloud sessions start no plugin language server.
Traps: Microsoft's own `roslyn-language-server` ships on NuGet as a prerelease only and no Anthropic plugin runs it.

## public API

### package validation
Publisher: Microsoft
Tier: 2: https://learn.microsoft.com/en-us/dotnet/fundamentals/apicompat/package-validation/overview
Evidence: `<EnablePackageValidation>` in a `*.csproj` or `Directory.Build.props`
Rung: full
Run: `dotnet pack -p:EnablePackageValidation=true`
Hook: local
Pin: apt dotnet-sdk-10.0
Route: `sudo apt-get update && sudo apt-get install -y dotnet-sdk-10.0`; Blocked: `dotnet-install.sh` (redirects to `builds.dotnet.microsoft.com`)
Constraints: part of the SDK, so no unit is added: it checks that the package's target frameworks agree with one another, and against a released version only where the project sets `PackageValidationBaselineVersion`, whose package is restored from NuGet.
Traps: None.
