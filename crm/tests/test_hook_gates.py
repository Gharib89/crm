# pyright: basic
"""Offline behaviour tests for the git-discipline and secret-scan PreToolUse hooks.

Both hooks are pure-stdlib scripts under .claude/hooks/ (not importable
packages) — load them by file path like test_destructive_sync.py does. The
end-to-end cases drive the real hook contract: JSON payload on stdin, exit 0
(pass) / 2 (block), reason on stderr.

Secret-looking test inputs are built by RUNTIME CONCATENATION so no literal in
this file can ever match the very patterns the hook scans commits for.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "hooks"
_GATE_PATH = _HOOKS_DIR / "destructive_op_gate.py"
_SCAN_PATH = _HOOKS_DIR / "secret_scan_gate.py"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None, f"Cannot load hook from {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_gate = _load(_GATE_PATH)
_scan = _load(_SCAN_PATH)

# Fake secrets, assembled at runtime (see module docstring).
FAKE_AZURE_SECRET = "abc" + "8Q" + "~" + "x" * 34
FAKE_KEY_BLOCK = "-----BEGIN " + "PRIVATE KEY-----"
FAKE_URL_CRED = "https" + "://user:" + "hunter22" + "@example.com/org"


def _run_hook(path: Path, command: str, cwd: str, env: dict | None = None) -> tuple[int, str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})
    proc = subprocess.run(
        [sys.executable, str(path)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    return proc.returncode, proc.stderr


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True)


@pytest.fixture()
def crm_repo(tmp_path: Path) -> Path:
    """A throwaway repo shaped like the shared main checkout: branch `main`,
    origin pointing at this project, one initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", "https://github.com/Gharib89/crm.git")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


class TestGitAddGate:
    @pytest.mark.parametrize("arg", ["-A", "--all", ".", "./", ":/"])
    def test_blanket_add_blocked(self, arg):
        assert _gate._git_add_reason("add", [arg]) is not None

    def test_explicit_paths_allowed(self):
        assert _gate._git_add_reason("add", ["crm/cli.py", "README.md"]) is None

    def test_other_subcommands_ignored(self):
        assert _gate._git_add_reason("status", ["-A"]) is None

    def test_end_to_end_block(self, tmp_path):
        code, err = _run_hook(_GATE_PATH, "git add -A", str(tmp_path))
        assert code == 2
        assert "explicit paths" in err

    def test_end_to_end_compound_command(self, tmp_path):
        code, _ = _run_hook(_GATE_PATH, "true && git add --all", str(tmp_path))
        assert code == 2


class TestGitParts:
    def test_repo_override_and_rest(self):
        assert _gate._git_parts(["-C", "/x", "commit", "-m", "hi"]) == (
            "/x",
            "commit",
            ["-m", "hi"],
        )

    def test_plain_subcommand(self):
        assert _gate._git_parts(["status"]) == (None, "status", [])

    def test_c_config_value_skipped(self):
        assert _gate._git_parts(["-c", "core.editor=true", "commit"]) == (None, "commit", [])

    @pytest.mark.parametrize("flag", ["--git-dir", "--work-tree", "--namespace"])
    def test_separated_value_globals_skip_their_value(self, flag):
        for fn in (_gate._git_parts, _scan._git_subcommand):
            assert fn([flag, "/some/value", "commit", "-m", "x"]) == (
                None,
                "commit",
                ["-m", "x"],
            )


class TestDocsPath:
    @pytest.mark.parametrize("path", ["README.md", "docs/how-to/query.md", "mkdocs.yml", "CLAUDE.md"])
    def test_docs_paths(self, path):
        assert _gate._is_docs_path(path) is True

    @pytest.mark.parametrize("path", ["crm/cli.py", ".claude/settings.json", "setup.py"])
    def test_non_docs_paths(self, path):
        assert _gate._is_docs_path(path) is False


class TestMainCheckoutCommitGate:
    def test_non_docs_commit_on_main_blocked(self, crm_repo):
        (crm_repo / "code.py").write_text("x = 1\n", encoding="utf-8")
        _git(crm_repo, "add", "code.py")
        code, err = _run_hook(_GATE_PATH, "git commit -m 'change'", str(crm_repo))
        assert code == 2
        assert "worktree" in err

    def test_docs_only_commit_on_main_allowed(self, crm_repo):
        (crm_repo / "NOTES.md").write_text("notes\n", encoding="utf-8")
        _git(crm_repo, "add", "NOTES.md")
        code, _ = _run_hook(_GATE_PATH, "git commit -m 'docs'", str(crm_repo))
        assert code == 0

    def test_feature_branch_allowed(self, crm_repo):
        _git(crm_repo, "checkout", "-b", "feat/x")
        (crm_repo / "code.py").write_text("x = 1\n", encoding="utf-8")
        _git(crm_repo, "add", "code.py")
        code, _ = _run_hook(_GATE_PATH, "git commit -m 'change'", str(crm_repo))
        assert code == 0

    def test_linked_worktree_allowed(self, crm_repo, tmp_path):
        wt = tmp_path / "wt"
        _git(crm_repo, "worktree", "add", "-b", "feat/wt", str(wt))
        (wt / "code.py").write_text("x = 1\n", encoding="utf-8")
        _git(wt, "add", "code.py")
        code, _ = _run_hook(_GATE_PATH, "git commit -m 'change'", str(wt))
        assert code == 0

    def test_other_repo_allowed(self, crm_repo):
        _git(crm_repo, "remote", "set-url", "origin", "https://github.com/other/project.git")
        (crm_repo / "code.py").write_text("x = 1\n", encoding="utf-8")
        _git(crm_repo, "add", "code.py")
        code, _ = _run_hook(_GATE_PATH, "git commit -m 'change'", str(crm_repo))
        assert code == 0

    def test_unresolvable_cd_fails_open(self, crm_repo):
        (crm_repo / "code.py").write_text("x = 1\n", encoding="utf-8")
        _git(crm_repo, "add", "code.py")
        code, _ = _run_hook(_GATE_PATH, "cd $WT && git commit -m 'change'", str(crm_repo))
        assert code == 0


class TestSecretPatterns:
    def _findings(self, text: str):
        return _scan._scan([("test", text)], _scan.PATTERNS)

    def test_azure_client_secret_matches(self):
        assert self._findings(f"secret = '{FAKE_AZURE_SECRET}'")

    def test_private_key_block_matches(self):
        assert self._findings(FAKE_KEY_BLOCK)

    def test_url_credential_matches(self):
        assert self._findings(f"url = '{FAKE_URL_CRED}'")

    @pytest.mark.parametrize(
        "benign",
        [
            "https://example.crm.dynamics.com/api/data/v9.2",
            "password = 'fake-test-password'",
            "D365_CLIENT_SECRET is read from the env",
            "user@example.com",
        ],
    )
    def test_benign_lines_pass(self, benign):
        assert self._findings(benign) == []


class TestSegmentSplitting:
    def test_operators_inside_quotes_do_not_shred_the_segment(self):
        segments = _scan._split_segments('true && git commit -m "fix (a|b) thing"')
        assert ["git", "commit", "-m", "fix (a|b) thing"] in segments

    def test_glued_operator_still_splits(self):
        segments = _scan._split_segments("x&&git commit")
        assert ["git", "commit"] in segments

    @pytest.mark.parametrize("splitter", ["scan", "gate"])
    def test_newline_separates_segments(self, splitter):
        fn = _scan._split_segments if splitter == "scan" else _gate._split_segments_lex
        assert fn("cd /x\ngit commit") == [["cd", "/x"], ["git", "commit"]]


class TestDestructiveSegmentUnion:
    """#675: a destructive crm verb must not slip past the gate when a quoted
    argument contains a shell operator, and backtick coverage must survive."""

    def test_quoted_operator_argument_still_blocked(self, tmp_path):
        code, err = _run_hook(
            _GATE_PATH, 'crm entity delete account 123 --filter "a|b"', str(tmp_path)
        )
        assert code == 2
        assert "entity delete" in err

    def test_backtick_substitution_still_blocked(self, tmp_path):
        code, _ = _run_hook(_GATE_PATH, "echo `crm entity delete account 1`", str(tmp_path))
        assert code == 2

    def test_yes_flag_still_confirms(self, tmp_path):
        code, _ = _run_hook(
            _GATE_PATH, 'crm entity delete account 123 --filter "a|b" --yes', str(tmp_path)
        )
        assert code == 0

    def test_quoted_mention_in_commit_message_not_blocked(self, tmp_path):
        code, _ = _run_hook(
            _GATE_PATH, 'git commit -m "docs: covers crm entity delete verb"', str(tmp_path)
        )
        assert code == 0


class TestDiffParsing:
    def test_added_lines_carry_file_origin(self):
        diff = (
            "diff --git a/crm/x.py b/crm/x.py\n"
            "--- a/crm/x.py\n"
            "+++ b/crm/x.py\n"
            "@@ -1 +1,2 @@\n"
            "+added = 1\n"
            "-removed = 2\n"
            " context = 3\n"
        )
        assert _scan._added_lines(diff) == [("crm/x.py", "added = 1")]


class TestMessageValues:
    def test_forms(self):
        rest = ["-m", "one", "--message=two", "-mthree", "--amend"]
        assert _scan._message_values(rest) == ["one", "two", "three"]


class TestLocalTokens:
    def test_tokens_load_and_match_case_insensitively(self, tmp_path, monkeypatch):
        token_file = tmp_path / "tokens.txt"
        token_file.write_text("# comment\n\nsampletoken\n", encoding="utf-8")
        monkeypatch.setattr(_scan, "TOKEN_FILE", str(token_file))
        checks = _scan._load_local_tokens()
        assert len(checks) == 1
        assert _scan._scan([("t", "has SampleToken inside")], checks)

    def test_missing_file_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_scan, "TOKEN_FILE", str(tmp_path / "absent.txt"))
        assert _scan._load_local_tokens() == []


class TestSecretScanEndToEnd:
    def _isolated_env(self, tmp_path: Path) -> dict:
        """Env whose HOME has no token file, so only generic patterns run —
        keeps the test independent of any real ~/.claude token file."""
        import os

        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)  # Windows expanduser
        return env

    def test_staged_secret_blocks_commit(self, crm_repo, tmp_path):
        (crm_repo / "config.py").write_text(f"secret = '{FAKE_AZURE_SECRET}'\n", encoding="utf-8")
        _git(crm_repo, "add", "config.py")
        code, err = _run_hook(
            _SCAN_PATH, "git commit -m 'add config'", str(crm_repo), env=self._isolated_env(tmp_path)
        )
        assert code == 2
        assert "config.py" in err

    def test_clean_staged_diff_passes(self, crm_repo, tmp_path):
        (crm_repo / "clean.py").write_text("x = 1\n", encoding="utf-8")
        _git(crm_repo, "add", "clean.py")
        code, _ = _run_hook(
            _SCAN_PATH, "git commit -m 'clean'", str(crm_repo), env=self._isolated_env(tmp_path)
        )
        assert code == 0

    def test_secret_in_commit_message_blocks(self, crm_repo, tmp_path):
        (crm_repo / "clean.py").write_text("x = 1\n", encoding="utf-8")
        _git(crm_repo, "add", "clean.py")
        code, err = _run_hook(
            _SCAN_PATH,
            f"git commit -m 'creds {FAKE_URL_CRED}'",
            str(crm_repo),
            env=self._isolated_env(tmp_path),
        )
        assert code == 2
        assert "commit message" in err

    def test_non_commit_command_ignored(self, tmp_path):
        code, _ = _run_hook(
            _SCAN_PATH, "git status", str(tmp_path), env=self._isolated_env(tmp_path)
        )
        assert code == 0

    def test_message_with_quoted_operators_still_scanned(self, crm_repo, tmp_path):
        (crm_repo / "clean.py").write_text("x = 1\n", encoding="utf-8")
        _git(crm_repo, "add", "clean.py")
        code, err = _run_hook(
            _SCAN_PATH,
            f'true && git commit -m "creds (a|b) {FAKE_URL_CRED}"',
            str(crm_repo),
            env=self._isolated_env(tmp_path),
        )
        assert code == 2
        assert "commit message" in err

    def test_env_var_tokens_used_when_no_token_file(self, crm_repo, tmp_path):
        (crm_repo / "cfg.py").write_text("org = 'EnvToken'\n", encoding="utf-8")
        _git(crm_repo, "add", "cfg.py")
        env = self._isolated_env(tmp_path)
        env["CRM_SECRET_SCAN_TOKENS"] = "envtoken, otherorg"
        code, err = _run_hook(_SCAN_PATH, "git commit -m 'cfg'", str(crm_repo), env=env)
        assert code == 2
        assert "org identifier" in err
