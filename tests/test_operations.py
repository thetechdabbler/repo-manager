"""Operation execution against freshly built fixtures.

These tests mutate repositories, so they use the function-scoped `builder` and build
only the states each case needs. The central safety properties get their own tests:
dry-run touches nothing, and an in-progress repo is never mutated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import git
from repo_manager.git_backend import GitBackend
from repo_manager.models import Operation, Verdict
from repo_manager.operations import OperationCoordinator
from repo_manager.repository_service import RepoSpec


def _spec(name: str, path: Path) -> RepoSpec:
    return RepoSpec(
        name=name, absolute_path=path, relative_path=name, groups=[], remote="origin"
    )


def _run(coord, spec, operation, dry_run=False, checkout_branch=None):
    plan = coord.build_plan([spec], operation, checkout_branch=checkout_branch)
    report = coord.execute(
        plan, operation, project_name="t", project_root="/", dry_run=dry_run
    )
    return report.results[0]


@pytest.fixture
def coord():
    return OperationCoordinator(backend=GitBackend(), jobs=2)


# -- update -------------------------------------------------------------------------


def test_update_fast_forwards_behind_repo(coord, builder):
    repo = builder.build_clean_behind()
    spec = _spec("clean-behind", repo)
    before = GitBackend().head_sha(repo)

    result = _run(coord, spec, Operation.UPDATE)

    assert result.verdict is Verdict.UPDATED
    after = GitBackend().head_sha(repo)
    assert after != before
    assert result.after_head == after


def test_update_current_repo_is_noop(coord, builder):
    repo = builder.build_clean_current()
    spec = _spec("clean-current", repo)
    result = _run(coord, spec, Operation.UPDATE)
    assert result.verdict is Verdict.NOOP


def test_update_skips_dirty_and_preserves_changes(coord, builder):
    repo = builder.build_dirty()
    spec = _spec("dirty", repo)
    before = GitBackend().head_sha(repo)

    result = _run(coord, spec, Operation.UPDATE)

    assert result.verdict is Verdict.SKIPPED
    assert result.reason
    assert result.next_action
    # Nothing moved, local edits intact.
    assert GitBackend().head_sha(repo) == before
    assert (repo / "untracked.txt").exists()
    assert (repo / "staged.txt").exists()


def test_update_skips_diverged(coord, builder):
    repo = builder.build_diverged()
    result = _run(coord, _spec("diverged", repo), Operation.UPDATE)
    assert result.verdict is Verdict.SKIPPED
    assert "diverged" in result.reason.lower()


def test_dry_run_makes_no_change(coord, builder):
    repo = builder.build_clean_behind()
    spec = _spec("clean-behind", repo)
    before = GitBackend().head_sha(repo)

    result = _run(coord, spec, Operation.UPDATE, dry_run=True)

    assert result.verdict is Verdict.PROCEED  # "would proceed"
    assert GitBackend().head_sha(repo) == before  # untouched
    assert result.commands == []


def test_in_progress_never_mutated(coord, builder):
    """No operation, dry-run or live, may touch a rebasing repo."""
    repo = builder.build_rebase_in_progress()
    gd = GitBackend().git_dir(repo)
    assert (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists()

    for op in (Operation.UPDATE, Operation.SWITCH_DEFAULT):
        result = _run(coord, _spec("rebase-in-progress", repo), op)
        assert result.verdict is Verdict.SKIPPED
        assert result.commands == []
    # Still rebasing, nothing disturbed.
    assert (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists()


# -- switch-default -----------------------------------------------------------------


def test_switch_default_from_feature_branch(coord, builder):
    repo = builder.build_clean_current()
    # Move onto a feature branch, then switch-default should return to main and ff.
    git(repo, "switch", "-q", "-c", "feature/work")
    spec = _spec("clean-current", repo)

    result = _run(coord, spec, Operation.SWITCH_DEFAULT)

    assert result.verdict is Verdict.UPDATED
    assert GitBackend().current_branch(repo) == "main"


def test_switch_default_ff_when_behind(coord, builder):
    repo = builder.build_clean_behind()
    result = _run(coord, _spec("clean-behind", repo), Operation.SWITCH_DEFAULT)
    assert result.verdict is Verdict.UPDATED
    assert GitBackend().current_branch(repo) == "main"


def test_switch_default_skips_dirty(coord, builder):
    repo = builder.build_dirty()
    result = _run(coord, _spec("dirty", repo), Operation.SWITCH_DEFAULT)
    assert result.verdict is Verdict.SKIPPED


def test_switch_default_skips_when_default_unknown(coord, builder):
    repo = builder.build_ambiguous_default()
    result = _run(coord, _spec("ambiguous-default", repo), Operation.SWITCH_DEFAULT)
    assert result.verdict is Verdict.SKIPPED
    assert "default branch" in result.reason.lower()


# -- checkout -----------------------------------------------------------------------


def test_checkout_existing_local_branch(coord, builder):
    repo = builder.build_clean_current()
    git(repo, "branch", "feature/x")
    result = _run(
        coord, _spec("clean-current", repo), Operation.CHECKOUT, checkout_branch="feature/x"
    )
    assert result.verdict is Verdict.UPDATED
    assert GitBackend().current_branch(repo) == "feature/x"


def test_checkout_creates_tracking_branch_from_remote(coord, builder):
    """A branch that exists only on the remote yields a local tracking branch."""
    repo = builder.build_clean_current()
    # Create a branch on the remote via the seed clone.
    seed = builder.seeds / "clean-current"
    git(seed, "switch", "-q", "-c", "release/1")
    from conftest import commit_file

    commit_file(seed, "rel.txt", "rel\n", "Release commit")
    git(seed, "push", "-q", "origin", "release/1")
    git(repo, "fetch", "-q", "origin")

    result = _run(
        coord, _spec("clean-current", repo), Operation.CHECKOUT, checkout_branch="release/1"
    )
    assert result.verdict is Verdict.UPDATED
    assert GitBackend().current_branch(repo) == "release/1"


def test_checkout_default_resolves_per_repo(coord, builder):
    repo = builder.build_clean_current()
    git(repo, "switch", "-q", "-c", "feature/y")
    result = _run(
        coord, _spec("clean-current", repo), Operation.CHECKOUT, checkout_branch="default"
    )
    assert result.verdict is Verdict.UPDATED
    assert GitBackend().current_branch(repo) == "main"


def test_checkout_missing_branch_skipped(coord, builder):
    repo = builder.build_clean_current()
    result = _run(
        coord, _spec("clean-current", repo), Operation.CHECKOUT, checkout_branch="ghost"
    )
    assert result.verdict is Verdict.SKIPPED
    assert "neither" in result.reason.lower() or "not found" in result.reason.lower()


# -- report shape -------------------------------------------------------------------


def test_report_exit_code_and_json(coord, builder):
    behind = builder.build_clean_behind()
    dirty = builder.build_dirty()
    specs = [_spec("clean-behind", behind), _spec("dirty", dirty)]
    plan = coord.build_plan(specs, Operation.UPDATE)
    report = coord.execute(
        plan, Operation.UPDATE, project_name="t", project_root="/", dry_run=False
    )
    # One updated, one skipped -> exit code 3 (skip), no failures.
    assert report.exit_code() == 3

    import json

    data = json.loads(__import__("json").dumps(report.to_dict()))
    assert data["operation"] == "update"
    assert data["exit_code"] == 3
    assert len(data["results"]) == 2


def test_ignore_skips_downgrades_exit_code(coord, builder):
    dirty = builder.build_dirty()
    plan = coord.build_plan([_spec("dirty", dirty)], Operation.UPDATE)
    report = coord.execute(
        plan, Operation.UPDATE, project_name="t", project_root="/",
        dry_run=False, ignore_skips=True,
    )
    assert report.exit_code() == 0
