# `plugin_src` — no-op plug-in for the assembly-lifecycle e2e test

This directory holds the C# source the e2e `plugin_assembly` fixture
(`crm/tests/e2e/conftest.py`) compiles with `dotnet build` to exercise the
`plugin register-assembly` → `register-step` → `unregister-step` →
`unregister-assembly` lifecycle against a live org. The `.dll` is **built from
source on demand, never committed** — there is no opaque binary here.

| File | Purpose |
|------|---------|
| `NoOpPlugin.cs` | A no-op `IPlugin` (it is registered, never executed). |
| `NoOpPlugin.csproj` | SDK-style **net462** project, strong-name signed; a post-build target writes the assembly's public key token to `assembly-identity.txt` in the build output. |
| `NoOpPlugin.snk` | The strong-name key the assembly is signed with. |
| `gen_snk.py` | Regenerates `NoOpPlugin.snk`. |

## Why net462 + signed

Dataverse plug-in assemblies must target .NET Framework 4.6.2 and be strong-name
signed; the cloud sandbox (isolation mode 2) is mandatory and validates the
registered `publickeytoken` against the uploaded content. The fixture reads the
token the build emits and passes it to `register-assembly`, so it always matches
the signed `.dll`.

The build runs on the Linux CI runner via the .NET SDK using
`Microsoft.NETFramework.ReferenceAssemblies` (net462 targeting pack) — no Windows
install needed. NuGet restore needs network access; absent `dotnet`, the fixture
**skips with instructions** rather than failing.

## The `.snk` is a throwaway test key, not a secret

A strong name is an identity/versioning mechanism, not a security boundary, and
this key signs only this test plug-in. It is committed so the build is
self-contained, and kept auditable by being regenerable:

```bash
python crm/tests/e2e/plugin_src/gen_snk.py
```

Regenerating produces a fresh RSA key (hence a different public key token), which
needs no other change: the token is read back from the built assembly at test
time, never pinned in code.
