# Go

## Signals
Kind: stack
Manifest: go.mod
Lockfile: go.sum
Workspace: go.work
Extensions: .go
Shebangs: None.
Runtime version: go.mod `toolchain`, go.mod `go`, .go-version, .tool-versions, mise.toml
Library: a `go.mod` whose module holds a package other than `main` outside `internal/`

## lint

### golangci-lint
Publisher: golangci
Tier: 2: https://github.com/golangci/golangci-lint
Evidence: `.golangci.yml`, `.golangci.yaml`, `.golangci.toml`, `.golangci.json`
Rung: edit
Run: `golangci-lint run --fix`
Hook: https://github.com/golangci/golangci-lint
Pin: package go github.com/golangci/golangci-lint/v2/cmd/golangci-lint
Route: `GOBIN="$HOME/.local/bin" go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@{version}`; Blocked: `install.sh`, release binaries
Constraints: takes packages, not files: the vendor repo's `golangci-lint-full` id, or a local hook of its shape (`pass_filenames: false`, entry `golangci-lint run --fix`), never its `golangci-lint` id. It lints the file's whole module, so a large module can outrun the 5 s edit budget; its per-package cache makes a warm run pay only for changed packages. Needs a Go toolchain.
Traps: the vendor's install docs say `go install` "isn't guaranteed to work"; it is the only route the cloud sandbox passes, and it builds cleanly. The `golangci-lint` hook id's `--new-from-rev HEAD` narrows the run to modified files, where the `unused` linter misreports.

## format

### gofmt
Publisher: Go team
Tier: 2: https://pkg.go.dev/cmd/gofmt
Evidence: None.
Rung: edit
Run: `gofmt -w {files}`
Hook: local
Pin: None.
Route: None.
Constraints: ships with the Go toolchain, whose version the repo's `go.mod` names; the image carries Go.
Traps: gofmt has no configuration; `goimports` is a separate x/tools module, not the default.

## typecheck

### go vet
Publisher: Go team
Tier: 2: https://pkg.go.dev/cmd/vet
Evidence: None.
Rung: turn
Run: `go vet ./...`
Hook: local
Pin: None.
Route: None.
Constraints: ships with the Go toolchain. It type-checks each package before its analyzers run, so it is the typecheck; a package is the smallest unit it takes (a file argument fails on sibling symbols).
Traps: with no warm build cache a medium repo costs about 10 s per package set (0.3 s warm, measured). When `go.mod` asks for a newer Go than the image's, `GOTOOLCHAIN` fetches that toolchain through the module proxy (which passes) on the first `go` call, so that call pays the download.

## test runner

### go test
Publisher: Go team
Tier: 2: https://pkg.go.dev/cmd/go#hdr-Test_packages
Evidence: `*_test.go` files
Rung: turn
Run: `go test ./...`
Hook: local
Pin: None.
Route: None.
Constraints: ships with the Go toolchain.
Traps: None.

## affected tests

### go test cache
Publisher: Go team
Tier: 2: https://pkg.go.dev/cmd/go#hdr-Test_packages
Evidence: `*_test.go` files
Rung: turn
Run: `go test ./...`
Hook: local
Pin: None.
Route: None.
Constraints: native and exact by the dependency graph, no git: the test result cache re-runs only packages whose test binary or opened files changed, so the member's whole test command is its affected-test command. `-count=1` disables the cache.
Traps: the cache lives in `GOCACHE`, which survives an idle resume; a cold first run pays every build (29 s build plus 19 s tests on caddy, measured).

## language server

### gopls
Publisher: golang
Tier: 2: https://go.dev/gopls/
Evidence: `.claude/skills/harness-gopls-lsp/`, `gopls-lsp@claude-plugins-official` in `.claude/settings.json` `enabledPlugins`
Rung: None.
Run: `go run golang.org/x/tools/gopls@{version}`
Hook: None.
Pin: package go golang.org/x/tools/gopls
Route: None.
Constraints: vendored from the Anthropic `gopls-lsp` plugin per [reference/language-servers.md](../reference/language-servers.md), the version riding in the launch as pyright's does; needs Go, and with no `go` on `PATH` the tool is `Unavailable: gopls needs Go`.
Local-only: cloud sessions start no plugin language server.
Traps: gopls supports only the two newest Go releases. The first launch of a version builds it into the module cache, which the first `LSP` call waits out.
