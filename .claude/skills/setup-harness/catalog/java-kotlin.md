# Java/Kotlin

## Signals
Kind: stack
Manifest: build.gradle, build.gradle.kts, pom.xml
Lockfile: gradle.lockfile (Gradle, opt-in; Maven has none, so a pom.xml no root's `<modules>` names is a candidate root)
Workspace: settings.gradle `include`, settings.gradle.kts `include`, pom.xml `<modules>`
Extensions: .java .kt .kts
Shebangs: None.
Runtime version: .java-version, .sdkmanrc, .tool-versions, mise.toml, the build's toolchain block
Library: the `maven-publish` plugin in a Gradle build, or `<distributionManagement>` in a `pom.xml`

## lint

### ktlint
Publisher: pinterest
Tier: 2: https://github.com/pinterest/ktlint
Evidence: `.editorconfig` `ktlint_*` keys, `ktlint` in the build or CI
Rung: edit
Files: .kt .kts
Run: `ktlint {files}`
Hook: local
Pin: package maven com.pinterest.ktlint:ktlint-cli
Route: `mkdir -p "$HOME/.local/lib" "$HOME/.local/bin" && for i in 1 2 3 4 5 6; do [ "$i" = 1 ] || sleep 5; curl -fsSL -o "$HOME/.local/lib/ktlint-{version}.jar" https://repo1.maven.org/maven2/com/pinterest/ktlint/ktlint-cli/{version}/ktlint-cli-{version}-all.jar && break; done && printf '#!/bin/sh\nexec java -jar "%s" "$@"\n' "$HOME/.local/lib/ktlint-{version}.jar" > "$HOME/.local/bin/ktlint" && chmod +x "$HOME/.local/bin/ktlint"`; Blocked: release binaries
Constraints: Kotlin files only; Java has no vendor linter, so a Java-only member takes no lint default. Needs a JVM, which the image carries (OpenJDK 21). The route puts the Maven Central jar behind a `ktlint` launcher in `~/.local/bin`, so the hook entry names the tool's own binary.
Traps: 2.0 moves to the `io.github.ktlint` group; the pin stays on `com.pinterest.ktlint` through 1.x. Maven Central's Cloudflare front answers about one cloud-sandbox request in four 429, in bursts, and `curl --retry` kept getting 429 where a fresh `curl` got 200 (measured), so the route runs up to six fresh `curl`s 5 s apart; Maven and Gradle back off on their own.

## format

### google-java-format
Publisher: google
Tier: 2: https://github.com/google/google-java-format
Evidence: `google-java-format` in CI or a make or just target
Rung: edit
Files: .java
Run: `google-java-format --replace {files}`
Hook: local
Pin: package maven com.google.googlejavaformat:google-java-format
Route: `mkdir -p "$HOME/.local/lib" "$HOME/.local/bin" && for i in 1 2 3 4 5 6; do [ "$i" = 1 ] || sleep 5; curl -fsSL -o "$HOME/.local/lib/google-java-format-{version}.jar" https://repo1.maven.org/maven2/com/google/googlejavaformat/google-java-format/{version}/google-java-format-{version}-all-deps.jar && break; done && printf '#!/bin/sh\nexec java -jar "%s" "$@"\n' "$HOME/.local/lib/google-java-format-{version}.jar" > "$HOME/.local/bin/google-java-format" && chmod +x "$HOME/.local/bin/google-java-format"`; Blocked: release binaries
Constraints: Needs JDK 21 or later. The route puts the jar behind a launcher, as ktlint's does.
Traps: Spotless (`googleJavaFormat()` or `ktlint()` in the build) is a tool this entry does not list, so it is kept and wired through its own `spotlessApply` task, never beside this jar; its `ratchetFrom 'origin/main'` fails on a shallow clone until `git fetch origin main`.

### ktlint format
Publisher: pinterest
Tier: 2: https://github.com/pinterest/ktlint
Evidence: `ktlint` evidence, as above
Rung: edit
Files: .kt .kts
Run: `ktlint --format {files}`
Hook: local
Pin: package maven com.pinterest.ktlint:ktlint-cli
Route: `mkdir -p "$HOME/.local/lib" "$HOME/.local/bin" && for i in 1 2 3 4 5 6; do [ "$i" = 1 ] || sleep 5; curl -fsSL -o "$HOME/.local/lib/ktlint-{version}.jar" https://repo1.maven.org/maven2/com/pinterest/ktlint/ktlint-cli/{version}/ktlint-cli-{version}-all.jar && break; done && printf '#!/bin/sh\nexec java -jar "%s" "$@"\n' "$HOME/.local/lib/ktlint-{version}.jar" > "$HOME/.local/bin/ktlint" && chmod +x "$HOME/.local/bin/ktlint"`; Blocked: release binaries
Constraints: one ktlint pin serves both roles.
Traps: None.

## typecheck

### gradle classes
Publisher: Gradle
Tier: 2: https://docs.gradle.org/current/userguide/java_plugin.html
Evidence: `build.gradle`, `build.gradle.kts`
Rung: turn
Run: `gradle classes`
Hook: local
Pin: None.
Route: None.
Constraints: the image's preinstalled `gradle`, never `./gradlew`: every `services.gradle.org` distribution the wrapper downloads redirects to a GitHub release asset, which the cloud sandbox refuses. Plugins and dependencies come from the Gradle plugin portal and Maven Central, which pass. A project is the smallest unit it takes. With no `gradle` on `PATH` the tool is `Unavailable: gradle classes needs a gradle on PATH`.
Traps: the preinstalled Gradle's version is the image's, not the wrapper's `distributionUrl`, so a build needing a newer Gradle fails; installing noble's apt `gradle` (4.4.1) does not fix it; report the rung under Not acted on with the wrapper's version. The image's is 8.14.3. The first run starts a daemon and pays every compile (13.6 s cold, 0.9 s warm on a toy, measured).

### maven compile
Publisher: Apache Maven
Tier: 2: https://maven.apache.org/plugins/maven-compiler-plugin/
Evidence: `pom.xml`
Rung: turn
Run: `mvn -q compile`
Hook: local
Pin: None.
Route: None.
Constraints: a repo's `./mvnw` where it has one (its distribution comes from Maven Central, which passes), else the image's preinstalled `mvn`. In a multi-module build a module built alone cannot resolve sibling modules missing from `~/.m2`, so the row runs from the stack root as `mvn -q -pl {member} -am compile`.
Traps: None.

## test runner

### gradle test
Publisher: Gradle
Tier: 2: https://docs.gradle.org/current/userguide/java_testing.html
Evidence: `src/test/` in a Gradle project
Rung: turn
Run: `gradle test`
Hook: local
Pin: None.
Route: None.
Constraints: the preinstalled `gradle`, as for `gradle classes`.
Traps: None.

### maven test
Publisher: Apache Maven
Tier: 2: https://maven.apache.org/surefire/maven-surefire-plugin/
Evidence: `src/test/` in a Maven project
Rung: turn
Run: `mvn -q test`
Hook: local
Pin: None.
Route: None.
Constraints: `./mvnw` or `mvn`, as for `maven compile`; in a multi-module build, `mvn -q -pl {member} -am test` from the stack root.
Traps: None.

## affected tests

### gradle up-to-date test
Publisher: Gradle
Tier: 2: https://docs.gradle.org/current/userguide/incremental_build.html
Evidence: a Gradle project
Rung: turn
Run: `gradle test`
Hook: local
Pin: None.
Route: None.
Constraints: native and exact at module level, no git: a test task whose input fingerprints did not change is up to date and skipped, so the member's test command is its affected-test command.
Traps: Develocity's Predictive Test Selection is a commercial server, not a default.

### maven also-make-dependents
Publisher: Apache Maven
Tier: 2: https://maven.apache.org/guides/mini/guide-multiple-modules.html
Evidence: a Maven project
Rung: turn
Run: `mvn -q -pl {member} -amd test`
Hook: local
Pin: None.
Route: None.
Constraints: Maven skips no up-to-date tests and `-Dtest=` filters by name only, so the row runs the member's module and every module depending on it (`-amd`, its reverse dependents). A reactor started inside the module cannot see its dependents, so the row runs from the stack root (`cd <root> && mvn -q -pl {member} -amd test`), `{member}` the module's path relative to it.
Traps: None.

## language server

### jdtls
Publisher: Eclipse
Tier: 2: https://github.com/eclipse-jdtls/eclipse.jdt.ls
Evidence: `.claude/skills/harness-jdtls-lsp/`, `jdtls-lsp@claude-plugins-official` in `.claude/settings.json` `enabledPlugins`
Rung: None.
Run: `${CLAUDE_PLUGIN_ROOT}/jdtls-launch.sh {version} {sha256}`
Hook: None.
Pin: package maven https://repo.eclipse.org/content/repositories/jdtls-releases org.eclipse.jdt.ls:org.eclipse.jdt.ls.product
Route: None.
Constraints: needs a JDK 21 and python3 on `PATH`, and `curl` for a version's first launch. The launch is [templates/jdtls-launch.sh](../templates/jdtls-launch.sh), copied into the plugin beside `.lsp.json`, in place of the upstream's bare `jdtls` from `PATH`: its first launch of a version downloads `<repository>/org/eclipse/jdt/ls/org.eclipse.jdt.ls.product/<version>/org.eclipse.jdt.ls.product-<version>.tar.gz` into `${XDG_CACHE_HOME:-~/.cache}/harness-jdtls/<version>` and runs it only when its sha256 is `{sha256}`, the install check's download digest, because the repository publishes sha1 and md5 alone. Maven Central carries no jdtls.
Local-only: cloud sessions start no plugin language server.
Traps: the first launch of a version downloads about 50 MB before jdtls starts, inside the upstream's 120 s `startupTimeout`, which the vendored `.lsp.json` keeps; later launches start from the cache.

### kotlin-lsp
Publisher: JetBrains
Tier: 2: https://github.com/Kotlin/kotlin-lsp
Evidence: `.claude/skills/harness-kotlin-lsp/`, `kotlin-lsp@claude-plugins-official` in `.claude/settings.json` `enabledPlugins`
Rung: None.
Run: `kotlin-lsp --stdio`
Hook: None.
Pin: None.
Route: None.
Constraints: Alpha and partially closed-source.
Unavailable: no Linux route: it downloads only from `download.jetbrains.com`, and the plugin's Homebrew formula is macOS-only.
Local-only: cloud sessions start no plugin language server.
Traps: None.
