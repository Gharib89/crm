---
status: accepted
---

# Dual-position global options via runtime param injection

## Context

Five global options carry the CLI's most-used cross-cutting behavior — `--json`,
`--fields`, `--jq`, `--profile`, `--dry-run`. Click parses options per-context, so
a root-group option is only accepted *before* the subcommand (`crm --json entity
get …`). Humans and agents both reach for the trailing form (`crm entity get …
--json`) constantly — output shaping (`--jq`/`--fields`) is *decided* after the
command is composed, yet had to be typed first. Click rejected the trailing form
as `No such option`; a position hint (`_JsonAwareGroup._global_option_hint`) told
the caller to move the flag, burning a round-trip observed in nearly every long
agent session.

Click's per-context parsing is deliberate and a documented wontfix (pallets/click
#66, #108, #245, #1104); the endorsed workaround is **shared params on the
leaves**. dbt-core shipped exactly this capability (dbt-core #6497, PR #8670):
shared params on every leaf, a context-chain merge, and a hard error when a flag
is given in both positions.

## Decision

Make those five options **dual-position**: valid before OR after the subcommand,
position pure syntax with zero semantic difference. Scope is exactly those five;
the other root options stay root-only and keep the existing position hint.

- **Mechanism — runtime param injection at the root resolution seam.** In
  `_LazyJsonAwareGroup.get_command`, after a top-level command/group resolves, walk
  it recursively (subgroups below the root are eager `click.Group`s, so `.commands`
  is populated; only the root is lazy) and append the five `Option`s to every leaf's
  `params`. Injection is **idempotent by token check** — a leaf already declaring a
  token is skipped, which both prevents duplicate injection on re-resolution and
  preserves a leaf's OWN same-named option (`profile set-password --profile`,
  `profile delete-password --profile`) for free.
- **Not argv preprocessing / hoisting.** Rewriting argv to move a trailing flag to
  the front cannot distinguish a flag *token* from a flag string appearing as
  another option's *value* (`--data '--json'`) without reimplementing Click's
  tokenizer. Injection lets Click's own parser make that distinction.
- **Injected options are `expose_value=False`** with a callback writing into
  `CLIContext`, so leaf command signatures are untouched. A leaf-position value
  funnels through the **same merge semantics** as the root callback: `--profile`
  sticky across REPL lines, the rest per-invocation, `--jq` implying `--json` and
  compiling at parse time (validate-before-backend preserved), `--fields`/`--jq`
  mutually exclusive **cross-position**.
- **Both positions explicitly given → usage error (exit 2)**, uniform and
  dbt-style: *"`--profile` was provided both before and after the subcommand; pick
  one."* Detected via `ParameterSource.COMMANDLINE` on both the root context and the
  leaf, so a default fill never trips it. (dbt-core's precedence bug, dbt-core
  #10304, is why the choice is a uniform hard error rather than a per-flag
  last-wins merge.)
- **Usage-error rendering.** `_json_mode_active` now scans the whole argv (not just
  the leading root-option run) for `--json`, still skipping the values of
  root value-taking options. Accepted, test-pinned false positive: a literal
  `--json` passed as a *leaf* option's value (`entity get x --select --json`) is
  taken for the flag, so a co-occurring usage error renders as a JSON envelope —
  purely cosmetic, and cheaper than reimplementing Click's tokenizer just to shape
  error output.
- **Surfacing.** Injected options are visible (not hidden) on every leaf `--help`
  and in `crm describe`, help text suffixed `[global; also valid before the
  command]`. No cloup / option-sections dependency.

## Considered options

- **Argv hoisting / preprocessing**: rejected — token-stealing (a flag string in an
  option's value position is indistinguishable from the flag without Click's
  tokenizer).
- **Per-leaf option decorators**: rejected — drift; ~250 leaves would each have to
  re-declare the five, and a new command would silently miss them.
- **Framework migration**: rejected — Typer inherits Click's per-context limit;
  clap's `global=true` is after-the-parent only; Cobra's persistent flags are the
  one free lunch but not worth a rewrite.
- **Per-flag last-wins merge on both-positions**: rejected — dbt-core #10304 shows
  the precedence footguns; a uniform hard error is predictable.

## Consequences

- **Purely additive to output**: with a flag in a single position, behavior is
  byte-identical to before; the trailing form now simply works instead of hinting.
- The position hint (`_global_option_hint`) still covers the eight root-only global
  options; only the five are removed from its jurisdiction (they no longer reach the
  `NoSuchOption` path on a leaf).
- The contract terms **Global option** and **Dual-position global option** live in
  `CONTEXT.md`; the capability is documented in the README and the crm skill's
  flag-placement guidance. References ADR 0008 (output contract) and ADR 0023
  (client-side shaping, the home of `--fields`/`--jq`).
