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


def test_init_on_empty_dir_fails(home, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["init", str(empty), "--yes"])
    assert result.exit_code == 1
    assert "no git repositories" in result.stderr.lower()


def test_profiles_command_lists_and_marks_active(home, workspace):
    runner.invoke(app, ["init", str(workspace), "--name", "fixture", "--yes"])
    result = runner.invoke(app, ["profiles"])
    assert result.exit_code == 0
    assert "fixture" in result.stdout
    assert "active" in result.stdout


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
