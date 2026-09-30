# Shell

## Signals
Kind: file kind
Names: None.
Paths: None.
Extensions: .sh .bash
Shebangs: sh bash

## lint

### ShellCheck
Publisher: koalaman
Tier: 2: https://github.com/koalaman/shellcheck
Evidence: `.shellcheckrc`
Rung: edit
Run: `shellcheck {files}`
Hook: local
Pin: apt shellcheck
Route: `sudo apt-get install -y shellcheck`; Blocked: release binaries
Constraints: None.
Traps: the vendor hook, `koalaman/shellcheck-precommit`, is `language: docker_image`; this entry runs `local` instead since the cloud sandbox has no guaranteed Docker daemon. Results depend on cwd and `-P`/`source-path`. A release binary in `~/.local/bin` shadows apt's copy on `PATH`: report it, never replace it.

## format

### shfmt
Publisher: mvdan
Tier: 2: https://github.com/mvdan/sh
Evidence: `.editorconfig`
Rung: edit
Run: `shfmt -w {files}`
Hook: local
Pin: package go mvdan.cc/sh/v3/cmd/shfmt
Route: `GOBIN="$HOME/.local/bin" go install mvdan.cc/sh/v3/cmd/shfmt@{version}`; Blocked: None.
Constraints: needs a Go toolchain; with none, shfmt is proposed with that reason so the human can decline it. The route sets `GOBIN` because `go install`'s default, `~/go/bin`, is not on `PATH`.
Traps: reads `.editorconfig`; apt's `shfmt` lags several releases behind, so a repo pinning a specific shfmt style should carry it in `.editorconfig`.
