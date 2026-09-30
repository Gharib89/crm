# Language servers

A language server is wired only as a vendored, repo-owned plugin, so every machine runs the same server config at the same binary pin. The official marketplace cannot be pinned to a commit (a marketplace source takes a branch or tag, and it has no tags), so enabling its plugins would give each machine whatever `main` is. Each catalog `## language server` tool names the Anthropic plugin it is vendored from.

## Vendoring

Per wired tool, from `anthropics/claude-plugins-official` at the head of `main`. Explore reads that full SHA (`git ls-remote https://github.com/anthropics/claude-plugins-official main`) and does step 1; Write does steps 2 to 4 at the same SHA, so what is written is what the human confirmed.

1. **Read the glue** in full and show it as the install-check row's glue: the upstream's `.claude-plugin/marketplace.json` entry (its inline `lspServers` is the whole server config) and `plugins/<upstream>/`. Check the tool's catalog `Constraints:` now: an `Unavailable:` they name goes under Not acted on, and nothing of the tool is proposed.
2. **Write `.claude/skills/harness-<upstream>/`**. Claude Code loads a plugin directory there as `harness-<upstream>@skills-dir` once the workspace is trusted, with no `enabledPlugins` entry, so the repo carries it like its skills:
   - `.claude-plugin/plugin.json`: `name` `harness-<upstream>`, the entry's `description` and `author`, `version` `<entry version>+<full sha>`.
   - `.lsp.json`: the entry's `lspServers` object, launched by the catalog `Run:` line: its first word is `command`, the rest `args`, with `{version}` the version `pick-version.sh` picked for the `Pin:`, `{sha256}` the install check's download digest for that version, `{root}` the stack root relative to the repo root (`.` at the root), and the `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PROJECT_DIR}` variables kept literal. `extensionToLanguage` as upstream has it. The entry's `Settings:`, where it has one, as `settings`, `{root}` substituted as above, except that at the root `{root}/` is dropped (pyright's `.venv/bin/python`). A server whose `Run:` or `Settings:` carries `{root}` serves one stack: with more than one root of the entry's stack, `{root}` is the first of them in detection's Roots list ([detection.md](detection.md) `## What detection reports`, item 1), and each other root goes under Not acted on as `<tool>: <root> not served`.
   - The launcher the catalog `Constraints:` names, where one does (jdtls): copied from this skill's `templates/` to the plugin directory's root, executable (`.claude/skills/harness-jdtls-lsp/jdtls-launch.sh`). The `Run:` line reaches it through `${CLAUDE_PLUGIN_ROOT}`, which Claude Code expands in an LSP server's `command` and `args`.
   - `README.md`: `Vendored from anthropics/claude-plugins-official@<full sha>`, the upstream entry's name, and the reason (the marketplace cannot be commit-pinned).
3. **Disable the upstream in `.claude/settings.json`**: `"enabledPlugins": {"<upstream>@claude-plugins-official": false}`, merged beside the repo's own keys.
4. **Pin the binary.** Where the entry's `Constraints:` pin the tool in the stack's own files (typescript-language-server and csharp-ls at the stack root, rust-analyzer with the channel in `rust-toolchain.toml`), the pin is the one Write step 1 wrote there. Otherwise the version rides in the `.lsp.json` launch, which fetches it, so Write step 1 installs nothing for the tool: pyright's `npx --yes --package=pyright@<v>`, gopls's `go run ...@<v>`, jdtls's launcher arguments `<version> <sha256>` (its build downloaded on first launch).

The name and the disable are the coexistence rule, measured with a dev's user-scope `pyright-lsp@claude-plugins-official` enabled (Claude Code 2.1.283):

- A vendored plugin keeping the upstream's name is not loaded: `the name "pyright-lsp" is already taken by an installed plugin (pyright-lsp@claude-plugins-official), which takes precedence`. It stays unloaded when the upstream is disabled, leaving no server for the extensions.
- Under its own name with the upstream still enabled, both register for `.py`, but only the user-scope server starts and answers. The vendored one never runs, so the pin is silently unused. Nothing duplicates.
- Under its own name with the upstream disabled, only the vendored server registers, starts and answers, and diagnostics arrive from one server.

An existing `.claude/skills/harness-<upstream>/` is the repo's own evidence: keep it and propose only what it lacks, per the Explore step.

## Local-only, never in the profile

A cloud session starts no plugin language server, so every language server is `local-only: cloud sessions start no plugin language server` in the install-check row's Cloud column and in the report's cloud table. That is true by construction, so it is never written to the harness profile: no `Local-only:` line and no proof line. A row the human drops with a reason is the one exception, recorded `Declined:` like any other.

## The proof

Local only. Its precondition is a trusted workspace; in a cloud session (`CLAUDE_CODE_REMOTE=true`) skip it and say so.

1. **Load the servers.** A plugin loads only on `/reload-plugins`, which the human types (Claude cannot), or in a new session. A run that wrote or changed a plugin asks for it and waits; any other run asks only when `ToolSearch` with `select:LSP` finds no `LSP` tool. A match alone never proves the vendored server, since the upstream's server, until its disable loads, also exposes the tool. After the reload, `ToolSearch` again to load the tool.
2. **Ask each server.** Per wired language, call `LSP` with `hover` (or `documentSymbol`) on one tracked file of that language. Pass when the result has no `Error performing`. A missing binary answers `Error performing hover: Command failed with ENOENT: <command>`.
3. **Report** one line per language: `<language>: answers (<operation> on <file>)`, or `<language>: fails (<the error>)`.

Two signals are not the proof. A diagnostic after an edit mutates a file and arrives on a later tool result or turn, never the edit's own. A `pgrep` of the command shows a process, not an answer.
