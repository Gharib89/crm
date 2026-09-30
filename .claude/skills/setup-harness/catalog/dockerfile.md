# Dockerfile

## Signals
Kind: file kind
Names: Dockerfile Containerfile
Paths: None.
Extensions: .Dockerfile .Containerfile
Shebangs: None.

## lint

### hadolint
Publisher: hadolint
Tier: 2: https://github.com/hadolint/hadolint
Evidence: `.hadolint.yaml`, `.hadolint.yml`
Rung: edit
Run: `docker run --rm -v "$PWD:/src" -w /src hadolint/hadolint:{version} hadolint {files}`
Hook: local
Pin: package dockerhub hadolint/hadolint
Route: `docker pull hadolint/hadolint:{version}`; Blocked: `ghcr.io/hadolint/hadolint` (its blobs redirect to `pkg-containers.githubusercontent.com`), release binaries, Hackage (needs GHC 9.10, noble has 9.4.7)
Constraints: runs in the vendor's Docker Hub image, the only vendor route with no GitHub release asset, so it needs a running dockerd: the cloud setup starts it and prefetches the image ([reference/cloud.md](../reference/cloud.md)). The hook is `language: docker_image` with `entry: hadolint/hadolint:<tag>@<digest> hadolint`, which the runner mounts the repo into, the Run line being that hook spelled out. It links ShellCheck as a library, so `RUN` lines are linted with no `shellcheck` binary.
Traps: the vendor's own `hadolint-docker` hook pulls from ghcr.io, which the cloud sandbox refuses; the `docker_image` hook above replaces it. The PyPI `hadolint-py` and `hadolint-bin` wheels are third-party. Docker Hub allows 100 anonymous pulls per 6 hours per IPv4 address, and the cloud sandbox's egress is shared: the entry trial's pull got `429 Too Many Requests` once (2026-09-28). A quota outlasts a retry seconds later, so the pull row carries no retry loop, unlike the Maven jar routes; the row sits last in the cloud setup, which stops at its first failed row, so a 429 costs only this session's hadolint, and the next session's setup retries it.

### docker build --check
Publisher: Docker
Tier: 2: https://docs.docker.com/build/checks/
Evidence: `# check=` directives in a Dockerfile
Rung: edit
Run: `bash -c 'for f; do docker build --check -f "$f" . || exit 1; done' _ {files}`
Hook: local
Pin: None.
Route: None.
Constraints: needs the image's dockerd running (the cloud setup's dockerd step) and Buildx 0.15 or later (the image has 0.31.1).
Traps: it resolves every `FROM` image from its registry (1.7 s unpulled, 0.3 s warm), so a ghcr.io or private base fails as a pull does, and a `# syntax=` line pulls a frontend image too. A Docker Hub base shares hadolint's anonymous pull limit: the entry trial's check got `429 Too Many Requests` on `alpine` once (2026-09-28).

## format

### Dockerfile formatter
Publisher: Docker
Tier: 2: https://docs.docker.com/reference/dockerfile/
Evidence: None.
Rung: edit
Run: None.
Hook: local
Pin: None.
Route: None.
Constraints: None.
Unavailable: no vendor formats Dockerfiles: Docker ships no formatter, Prettier has no Dockerfile parser (measured), and the formatters that exist (`reteps/dockerfmt`, dprint's plugin) are not the format vendor's.
Traps: None.
