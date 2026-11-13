# Plug-in assembly fixtures

Built for the tests of the `plugin-binary-sdk-net462` rule; none comes from a live org.
`Microsoft.Xrm.Sdk` here is a stub (`src/Xrm5`, `src/Xrm9`) carrying only the name and
version the rule reads, not Microsoft's assembly.

| File | Project | Target framework | References |
|---|---|---|---|
| `net40-sdk5.dll` | `src/Legacy` | .NET Framework 4.0 | `Microsoft.Xrm.Sdk` 5.0.0.0 |
| `net462-sdk9.dll` | `src/Current` | .NET Framework 4.6.2 | `Microsoft.Xrm.Sdk` 9.0.0.0 |
| `net35.dll` | `src/Ancient` | .NET Framework 3.5 (no `TargetFrameworkAttribute`) | none |

Rebuild with the .NET SDK (8 or later) from this folder, then copy each
`src/<Project>/bin/Release/<tfm>/Contoso.Plugins.<Project>.dll` over its file above:

```sh
for p in Legacy Current Ancient; do dotnet build -c Release src/$p/$p.csproj; done
```
