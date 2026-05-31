---
status: accepted
---

# CLI exit-code contract: 0 / 1 / 2

The `crm` CLI is consumed by coding agents that loop on the process exit code, but
command failures historically exited `0` (`CLIContext.emit` only printed). We make
`emit(ok=False)` raise `click.exceptions.Exit(1)`, fixing the contract to: `0` =
success, `1` = operational failure (D365 server error, in-command validation, or a
declined confirmation), `2` = Click usage error (unknown flag / bad parameter,
unchanged). See [CONTEXT.md](../../CONTEXT.md) for the term definitions.

## Considered options

- **Mechanism — direct-raise vs flag+boundary.** The issue suggested setting
  `self.failed` and converting it to `Exit(1)` at a root-group boundary. We chose
  direct-raise inside `emit` because every `emit(False)` is already followed by
  `return`, so there is nothing to defer; the flag approach adds a boundary hook, a
  per-command reset, and a subtle pitfall where the REPL-launching outer invocation
  inherits a failed flag. Direct-raise is safe because `Exit` propagates cleanly
  through `finally`/context managers and no broad `except` swallows it.
- **Single code per class vs granular per-class codes.** Granular codes (e.g.
  3=server, 4=validation, 5=abort) were rejected: this is a `--json` tool, so
  failure-class granularity already lives in the envelope (`error`, `meta.status`,
  `meta.code`), and granular codes would collide with Click's `2` and need a
  maintained registry.
- **Unifying usage errors to `1`.** Rejected — keeping Click's `2` preserves the
  malformed-invocation vs operation-failed distinction.

## Consequences

- Scripts and agents may now bind to these codes; changing them later is a breaking
  change.
- The sibling "JSON-error-envelope" issue may reshape usage-error *output* but must
  keep their exit code at `2`, or it contradicts this contract.
- Detail beyond the code is intentionally only in the JSON envelope, never the code.
- REPL behaviour is unchanged: in-REPL commands run with `standalone_mode=False`, so
  Click returns the code instead of terminating the session.
