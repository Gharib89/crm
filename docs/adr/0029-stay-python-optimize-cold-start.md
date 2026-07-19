# Stay in Python; per-invocation cold-start is the perf metric that matters

`crm` stays a Python CLI. Performance work targets **per-invocation cold-start**
(interpreter start + import tax), not throughput, and the standing rule that
protects it is: **preserve the lazy imports** — the lazy command group
(`_LazyJsonAwareGroup` / `_lazy_commands` in `crm/cli.py`) and the deferred
HTTP-transport imports. A language migration (Go/Rust/C#) and C-compilation
(Nuitka/Cython/mypyc) were both evaluated and rejected. Decided 2026-06-11; the
cold-start fix shipped in [#247](https://github.com/Gharib89/crm/issues/247).

## Why cold-start is the metric

The primary consumer is an agent driving the shipped skill with hundreds of
one-shot `crm <group> <verb>` calls per session — each invocation pays the
interpreter + import tax once. Bulk data work is network-bound (the Dataverse
Web API round-trip dominates at 50–500 ms server-side), so throughput is rarely
the bottleneck and single-request runtime is invisible next to it. Frame every
perf discussion around startup latency per invocation.

## Why the bottleneck is import, not execution

Cold start was dominated by eager HTTP-transport imports that local/offline
commands never use: `requests` + `charset_normalizer`, and the
`requests_ntlm` → `spnego` → `cryptography` NTLM chain (which loaded even for
OAuth profiles). Lazy command-loading already existed but was defeated by
module-top transport imports. The #247 fix deferred transport to session
construction, put the NTLM chain on the NTLM branch only, and moved
typing-only imports under `TYPE_CHECKING`.

Consequence for reviewers and refactors: a well-meaning "clean up the imports"
change that makes these eager regresses the one metric that matters. The lazy
shape is deliberate, not an accident to tidy.

## Alternatives rejected

- **Language migration.** A full evaluation (2026-06-11) of rewriting the CLI
  picked Go as the best candidate (first-class equivalents for every dep:
  `go-ntlmssp`, MSAL Go, OS keyring), runner-up C#/.NET. Rejected because a
  compiled language only buys cold-start — and cold-start was fixable in
  Python (above). Do not re-run this research or re-pitch a rewrite unless the
  constraints change.
- **C-compilation (Nuitka / Cython / mypyc).** Speeds *execution* 2–4×, but the
  bottleneck is *import*; compiled modules still link the full CPython runtime
  and import machinery. No meaningful gain.
