# MCP server (`crm mcp`)

This CLI does not ship an MCP (Model Context Protocol) server wrapping its verbs
as agent-callable tools. AI agents drive crm the way they already do: the shipped
skill (`crm/skills/`) plus `crm <verb> --json` over the stable JSON contract
(ADR 0008).

## Why this is out of scope

**The real audience is already served.** The shipped skill + JSON contract make
every verb agent-driveable today for any *shell-capable* host (Claude Code, Codex,
IDE terminals). An MCP wrapper adds value only for *shell-less* hosts (hosted
assistants, MCP-panel-only IDEs) — a hypothetical audience with **zero current
demand**. Building a second invocation surface ahead of a single asking user is
speculative.

**Permanent maintenance cost for a redundant surface.** An MCP server is not a
thin wrapper: a new `crm/commands/mcp.py`, an optional `crm[mcp]` extra and its
cold-start-safe lazy import, a curated tool surface with a read-default /
write-gated safety model to keep correct forever, MCP tool-annotation spec drift
(the 2025-11-25 hints), PyInstaller bundling, docs, and an ADR. All of it shadows
verbs the JSON contract already exposes — a parallel path to maintain, in lockstep,
for capability the CLI already has.

**Safety model duplication.** crm's destructive-operation gating (permission hooks,
`--dry-run`, mutation confirmation) is keyed on CLI verbs. An MCP surface has to
re-implement that gate as `--allow-writes` + per-tool `destructiveHint`, a second
enforcement point that can drift out of agreement with the CLI's.

**Not the current direction.** Strategic bet, not a felt need. Revisit only if a
concrete shell-less-host user materializes and the shipped-skill path is proven
insufficient for them — not before.

## Prior requests

- #610 — "Expose crm CLI as an MCP server (crm mcp) for AI-agent-driven D365 ops"
