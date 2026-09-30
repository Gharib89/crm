# Rust

## Signals
Kind: stack
Manifest: Cargo.toml
Lockfile: Cargo.lock
Workspace: Cargo.toml `[workspace]`
Extensions: .rs
Shebangs: None.
Runtime version: rust-toolchain.toml, rust-toolchain, Cargo.toml `rust-version`, .tool-versions, mise.toml
Library: a lib target (`src/lib.rs` or `[lib]`) in a `Cargo.toml` without `publish = false`

## format

### rustfmt
Publisher: Rust project
Tier: 2: https://github.com/rust-lang/rustfmt
Evidence: `rustfmt.toml`, `.rustfmt.toml`
Rung: edit
Run: `rustfmt {files}`
Hook: local
Pin: None.
Route: `rustup component add rustfmt`; Blocked: None.
Constraints: a toolchain component, pinned with the channel in `rust-toolchain.toml` `components`; needs rustup.
Traps: standalone rustfmt defaults to edition 2015 and reads `rustfmt.toml`, not `Cargo.toml`, so the run sets `edition` in the root's `rustfmt.toml` to the crates' `Cargo.toml` edition where none is set (false diffs otherwise, measured). `rustfmt <file>` also formats that file's out-of-line `mod` children. rustup's `rustfmt` proxy exists before the component does, so the cloud setup's done test is `rustfmt --version`, not `command -v`.

## typecheck

### cargo check
Publisher: Rust project
Tier: 2: https://doc.rust-lang.org/cargo/commands/cargo-check.html
Evidence: None.
Rung: turn
Run: `cargo check`
Hook: local
Pin: None.
Route: None.
Constraints: ships with cargo, which the image carries; a crate is the smallest unit it takes.
Traps: None.

### cargo clippy
Publisher: Rust project
Tier: 2: https://github.com/rust-lang/rust-clippy
Evidence: `clippy.toml`, `.clippy.toml`, `[lints.clippy]` in `Cargo.toml`
Rung: turn
Run: `cargo clippy -- -D warnings`
Hook: local
Pin: None.
Route: `rustup component add clippy`; Blocked: None.
Constraints: a toolchain component like rustfmt; it runs `cargo check` and adds its lints, so it replaces `cargo check` rather than sitting beside it.
Traps: `--all-targets` fails on stable when benches use `#![feature(test)]`. rustup's `cargo-clippy` proxy exists before the component does, so the cloud setup's done test is `cargo clippy --version`, not `command -v`.

## test runner

### cargo test
Publisher: Rust project
Tier: 2: https://doc.rust-lang.org/cargo/commands/cargo-test.html
Evidence: `#[test]` functions, a `tests/` directory
Rung: turn
Run: `cargo test`
Hook: local
Pin: None.
Route: None.
Constraints: ships with cargo.
Traps: None.

### cargo-nextest
Publisher: nextest-rs
Tier: 2: https://github.com/nextest-rs/nextest
Evidence: `.config/nextest.toml`, `cargo nextest` in CI or a make or just target
Rung: turn
Run: `cargo nextest run`
Hook: local
Pin: package crates cargo-nextest
Route: `cargo install --locked cargo-nextest@{version}`; Blocked: `get.nexte.st` (redirects to release binaries)
Constraints: the source build takes about 219 s (measured), most of the default 300 s cloud setup budget, so proposing it also proposes a budget override. `--locked` is mandatory since 0.9.124.
Traps: doctests do not run under nextest; `cargo test --doc` still covers them.

## affected tests

### nextest rdeps
Publisher: nextest-rs
Tier: 2: https://nexte.st/docs/filtersets/reference/
Evidence: `cargo-nextest` evidence, as above
Rung: turn
Run: `cargo nextest run --workspace -E 'rdeps({package})'`
Hook: local
Pin: package crates cargo-nextest
Route: `cargo install --locked cargo-nextest@{version}`; Blocked: `get.nexte.st` (redirects to release binaries)
Constraints: `{package}` is the member's `[package] name`; the filterset selects that crate and every crate depending on it, from the dependency graph. Run inside a member without `--workspace`, cargo builds only that crate (measured), so `Run:` carries `--workspace`, which selects from the whole workspace from any member directory. Cargo has no changed-files mode, so this is a gap proposal per the catalog README's `## Affected tests`.
Traps: the same 219 s build as `cargo-nextest`, and no doctests.

## language server

### rust-analyzer
Publisher: Rust project
Tier: 2: https://rust-analyzer.github.io/book/rust_analyzer_binary.html
Evidence: `.claude/skills/harness-rust-analyzer-lsp/`, `rust-analyzer-lsp@claude-plugins-official` in `.claude/settings.json` `enabledPlugins`
Rung: None.
Run: `rust-analyzer`
Hook: None.
Pin: None.
Route: None.
Constraints: vendored from the Anthropic `rust-analyzer-lsp` plugin per [reference/language-servers.md](../reference/language-servers.md). The binary is the toolchain's `rust-analyzer` component, pinned with the channel in `rust-toolchain.toml` `components`, and rustup is its launcher: with no `rustup` on `PATH` the tool is `Unavailable: rust-analyzer needs rustup`.
Local-only: cloud sessions start no plugin language server.
Traps: rustup's `rust-analyzer` proxy exists before the component does, so probe with `rust-analyzer --version`, not `command -v`; a failing probe is filled by adding `rust-analyzer` to `rust-toolchain.toml` `components`.

## public API

### cargo-semver-checks
Publisher: obi1kenobi
Tier: 2: https://github.com/obi1kenobi/cargo-semver-checks
Evidence: `cargo-semver-checks` or `cargo semver-checks` in CI or a script
Rung: full
Run: `cargo semver-checks check-release`
Hook: local
Pin: package crates cargo-semver-checks
Route: `cargo install --locked cargo-semver-checks@{version}`; Blocked: `cargo binstall` (GitHub release assets)
Constraints: the baseline is the crate's newest crates.io release, so it is proposed only where one exists ([reference/surfaces.md](../reference/surfaces.md) `## Library`); `--baseline-rev <ref>` is kept where the repo's own CI or a script passes it. It reads rustdoc JSON, whose format each release supports only for the then-current stable and beta, so its pin moves with `rust-toolchain.toml`.
Local-only: cloud install 351 s exceeds the 300 s cloud setup budget
Traps: None.
