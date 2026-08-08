"""End-to-end CLI tests for init and status."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from repo_manager import config
from repo_manager.cli import app

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated config home so tests never touch the real ~/.config."""
    h = tmp_path / "home"
    monkeypatch.setenv("REPO_MANAGER_HOME", str(h))
    return h


@pytest.fixture
def workspace(discovery_workspace):
    return discovery_workspace.workspace


def test_init_creates_profile(home, workspace):
    result = runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert config.profile_path("fixture").exists()

    profile = config.load_profile("fixture")
    names = {r.path for r in profile.repositories}
    assert "clean-current" in names
    assert "services/payments-api" in names
    # Excluded and too-deep repos are absent.
    assert not any("node_modules" in p for p in names)
    assert not any("too-deep" in p for p in names)


def test_init_records_default_branch_inference(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    profile = config.load_profile("fixture")
    by_path = {r.path: r for r in profile.repositories}
    # The nested agent was cloned on dev with origin/HEAD -> dev.
    assert by_path["services/payments-api"].default_branch == "dev"
    assert by_path["clean-current"].default_branch == "main"


def test_init_sets_active_project(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    assert config.load_active_project() == "fixture"


def test_status_json_shape(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)

    assert data["schema_version"] == 1
    assert data["project"]["name"] == "fixture"
    assert "totals" in data
    assert len(data["repositories"]) >= 3
    for repo in data["repositories"]:
        assert "classification" in repo
        assert "remote" in repo
        assert "data_is_current" in repo["remote"]


def test_status_uses_active_project_without_flag(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert "fixture" in result.stdout


def test_status_group_filter(home, workspace, monkeypatch):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    # Hand-assign a group so we can filter on it.
    profile = config.load_profile("fixture")
    profile.repositories[0].groups = ["solo"]
    config.save_profile(profile)

    result = runner.invoke(app, ["status", "--group", "solo", "--json"])
    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert len(data["repositories"]) == 1


def test_status_unknown_group_is_usage_error(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    result = runner.invoke(app, ["status", "--group", "nonexistent"])
    assert result.exit_code == 2
    assert "group" in result.stderr.lower()


def test_status_without_profile_errors(home):
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 2
    assert "no project" in result.stderr.lower()


def test_help_command_lists_commands_and_states(home):
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    assert "repo-manager" in result.stdout
    assert "status" in result.stdout
    assert "remote-unavailable" in result.stdout
    assert "Exit codes" in result.stdout


def test_init_include_nested_discovers_umbrella_children(home, builder):
    umbrella = builder.build_umbrella_root()

    # Without --include-nested: only the umbrella repo is found.
    plain = runner.invoke(app, ["init", str(umbrella), "--name", "plain", "--yes"])
    assert plain.exit_code == 0, plain.stdout + plain.stderr
    plain_paths = {r.path for r in config.load_profile("plain").repositories}
    assert plain_paths == {"."}

    # With --include-nested: the nested independent clones appear too.
    nested = runner.invoke(
        app, ["init", str(umbrella), "--name", "nested", "--include-nested", "--yes"]
    )
    assert nested.exit_code == 0, nested.stdout + nested.stderr
    profile = config.load_profile("nested")
    paths = {r.path for r in profile.repositories}
    assert {".", "platform", "domain-agents/agent-a", "mcps/mcp-a"} <= paths
    assert "libs/shared" not in paths           # submodule skipped
    assert not any("node_modules" in p for p in paths)
    # The setting is persisted so the layout is documented in the profile.
    assert profile.discovery.descend_into_repositories is True


def test_init_on_empty_dir_fails(home, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["init", str(empty), "--yes"])
    assert result.exit_code == 1
    assert "no git repositories" in result.stderr.lower()


def test_forget_removes_profile_and_clears_active(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    assert config.profile_path("fixture").exists()
    assert config.load_active_project() == "fixture"

    result = runner.invoke(app, ["forget", "fixture", "--yes"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert not config.profile_path("fixture").exists()
    assert config.load_active_project() is None
    # The workspace on disk is untouched.
    assert (workspace / "clean-current").is_dir()


def test_forget_unknown_profile_errors(home):
    result = runner.invoke(app, ["forget", "nope", "--yes"])
    assert result.exit_code == 2
    assert "no profile named" in result.stderr.lower()


def test_forget_requires_yes_non_interactively(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    result = runner.invoke(app, ["forget", "fixture"])
    assert result.exit_code == 2
    assert "--yes" in result.stderr
    # Nothing removed.
    assert config.profile_path("fixture").exists()


def test_forget_keeps_other_active_pointer(home, workspace, tmp_path):
    runner.invoke(app, ["init", str(workspace), "--name", "one", "--yes"])
    runner.invoke(app, ["init", str(workspace), "--name", "two", "--yes"])
    # 'two' is active (last init). Forgetting 'one' must not clear it.
    assert config.load_active_project() == "two"
    result = runner.invoke(app, ["forget", "one", "--yes"])
    assert result.exit_code == 0, result.stderr
    assert config.load_active_project() == "two"


def test_profiles_command_lists_and_marks_active(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    result = runner.invoke(app, ["profiles"])
    assert result.exit_code == 0
    assert "fixture" in result.stdout
    assert "active" in result.stdout


def _init(workspace, name="fixture"):
    return runner.invoke(app, ["init", str(workspace), "--name", name, "--yes"])


@pytest.fixture
def mutws(builder):
    """A workspace with the states the mutation commands exercise."""
    builder.build_clean_current()
    builder.build_clean_behind()
    builder.build_dirty()
    builder.build_ahead()
    return builder


def test_update_dry_run_changes_nothing(home, mutws):
    _init(mutws.workspace)
    from repo_manager.git_backend import GitBackend

    behind = mutws.workspace / "clean-behind"
    before = GitBackend().head_sha(behind)
    result = runner.invoke(app, ["update", "--dry-run", "--json"])
    # clean-behind would proceed, but dirty/ahead repos are skipped -> exit 3.
    assert result.exit_code == 3, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["dry_run"] is True
    assert GitBackend().head_sha(behind) == before


def test_update_requires_yes_non_interactively(home, mutws):
    _init(mutws.workspace)
    # No TTY in the test runner and no --yes: must refuse to mutate.
    result = runner.invoke(app, ["update", "--repo", "clean-behind"])
    assert result.exit_code == 2
    assert "--yes" in result.stderr


def test_update_yes_fast_forwards(home, mutws):
    _init(mutws.workspace)
    from repo_manager.git_backend import GitBackend

    behind = mutws.workspace / "clean-behind"
    before = GitBackend().head_sha(behind)
    result = runner.invoke(app, ["update", "--repo", "clean-behind", "--yes", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["results"][0]["verdict"] == "updated"
    assert GitBackend().head_sha(behind) != before


def test_update_dirty_repo_skips_with_exit_3(home, mutws):
    _init(mutws.workspace)
    result = runner.invoke(app, ["update", "--repo", "dirty", "--yes", "--json"])
    assert result.exit_code == 3
    data = json.loads(result.stdout)
    assert data["results"][0]["verdict"] == "skipped"
    assert data["results"][0]["next_action"]


def test_update_ignore_skips_exits_zero(home, mutws):
    _init(mutws.workspace)
    result = runner.invoke(
        app, ["update", "--repo", "dirty", "--yes", "--ignore-skips", "--json"]
    )
    assert result.exit_code == 0


def test_select_expression_filters(home, mutws):
    _init(mutws.workspace)
    result = runner.invoke(app, ["update", "--select", "ready", "--dry-run", "--json"])
    assert result.exit_code == 0, result.stderr  # only 'ready' repos, all proceed
    data = json.loads(result.stdout)
    assert all(r["verdict"] == "proceed" for r in data["results"])
    assert len(data["results"]) >= 1


def test_stash_and_update_dirty_repo(home, mutws):
    _init(mutws.workspace)
    from repo_manager.git_backend import GitBackend

    dirty = mutws.workspace / "dirty"
    before = GitBackend().head_sha(dirty)
    result = runner.invoke(
        app, ["update", "--repo", "dirty", "--stash-and-update", "--yes", "--json"]
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    res = data["results"][0]
    assert res["verdict"] == "updated"
    assert res["stash"]["stashed"] is True
    assert res["stash"]["restore"] == "clean"
    # Fast-forward happened and local work is intact.
    assert GitBackend().head_sha(dirty) != before
    assert (dirty / "untracked.txt").exists()


def test_switch_default_returns_to_main(home, mutws):
    _init(mutws.workspace)
    from repo_manager.git_backend import GitBackend
    from conftest import git as raw_git

    behind = mutws.workspace / "clean-behind"
    raw_git(behind, "switch", "-q", "-c", "feature/z")
    result = runner.invoke(
        app, ["switch-default", "--repo", "clean-behind", "--yes", "--json"]
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert GitBackend().current_branch(behind) == "main"


def test_sync_alias_warns_and_works(home, mutws):
    _init(mutws.workspace)
    result = runner.invoke(app, ["sync", "--repo", "clean-current", "--yes", "--json"])
    assert result.exit_code == 0
    assert "deprecated" in result.stderr.lower()


def test_checkout_default_across_repos(home, mutws):
    _init(mutws.workspace)
    from repo_manager.git_backend import GitBackend
    from conftest import git as raw_git

    cur = mutws.workspace / "clean-current"
    raw_git(cur, "switch", "-q", "-c", "feature/q")
    result = runner.invoke(
        app,
        ["checkout", "--branch", "default", "--repo", "clean-current", "--yes", "--json"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert GitBackend().current_branch(cur) == "main"


def test_summary_json_over_workspace(home, mutws):
    _init(mutws.workspace)
    from conftest import commit_file

    commit_file(mutws.workspace / "clean-current", "z.py", "1\n", "feat: new AB-1")
    result = runner.invoke(app, ["summary", "--since", "2025-12-01", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["since"] == "2025-12-01"
    assert data["totals"]["total_commits"] >= 1
    # A window after the fixed fixture date finds nothing.
    narrow = runner.invoke(app, ["summary", "--since", "2026-06-01", "--json"])
    assert json.loads(narrow.stdout)["totals"]["total_commits"] == 0


def test_summary_writes_markdown_file(home, mutws, tmp_path):
    _init(mutws.workspace)
    out = tmp_path / "report.md"
    result = runner.invoke(
        app, ["summary", "--since", "2025-12-01", "--markdown", str(out)]
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert out.exists()
    text = out.read_text()
    assert text.startswith("# Change summary:")
    assert "commits" in text


def test_status_fetch_flags_unreachable(home, tmp_path, builder):
    """A repo with a broken remote reports remote-unavailable under --fetch."""
    repo = builder.build_unreachable_remote()
    ws = builder.workspace
    runner.invoke(app, ["init", str(ws), "--name", "fx", "--yes"])
    result = runner.invoke(app, ["status", "--fetch", "--json"])
    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    target = next(
        r for r in data["repositories"] if r["identity"]["name"] == "unreachable-remote"
    )
    assert target["classification"] == "remote-unavailable"
    assert target["remote"]["data_is_current"] is False
