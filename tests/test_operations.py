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


def _run(coord, spec, operation, dry_run=False, checkout_branch=None, sync_rebase=False):
    plan = coord.build_plan(
        [spec], operation, checkout_branch=checkout_branch, sync_rebase=sync_rebase
    )
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

    for op in (Operation.UPDATE, Operation.DEFAULT, Operation.SYNC):
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


# -- sync ---------------------------------------------------------------------------


def _advance_default(builder, name: str) -> None:
    from conftest import commit_file

    seed = builder.seeds / name
    commit_file(seed, "src/app.py", "VERSION = 2\nSHARED = 'upstream'\n", "Main update")
    git(seed, "push", "-q", "origin", "main")


def test_sync_merges_default_into_current_branch_without_upstream(coord, builder):
    from conftest import commit_file

    repo = builder.build_clean_current()
    git(repo, "switch", "-q", "-c", "feature/work")
    commit_file(repo, "feature.txt", "feature\n", "Feature work")
    _advance_default(builder, "clean-current")

    result = _run(coord, _spec("clean-current", repo), Operation.SYNC)

    assert result.verdict is Verdict.UPDATED
    assert result.source_branch == "origin/main"
    assert GitBackend().current_branch(repo) == "feature/work"
    assert GitBackend().in_progress_operation(repo) == "none"


def test_sync_rebase_is_explicit(coord, builder):
    from conftest import commit_file

    repo = builder.build_clean_current()
    git(repo, "switch", "-q", "-c", "feature/work")
    commit_file(repo, "feature.txt", "feature\n", "Feature work")
    _advance_default(builder, "clean-current")

    result = _run(coord, _spec("clean-current", repo), Operation.SYNC, sync_rebase=True)

    assert result.verdict is Verdict.UPDATED
    assert result.commands == ["rebase origin/main"]
    assert GitBackend().current_branch(repo) == "feature/work"


def test_sync_skips_dirty_unknown_default_and_unreachable(coord, builder):
    for name, repo in (
        ("dirty", builder.build_dirty()),
        ("ambiguous-default", builder.build_ambiguous_default()),
        ("unreachable-remote", builder.build_unreachable_remote()),
    ):
        result = _run(coord, _spec(name, repo), Operation.SYNC)
        assert result.verdict is Verdict.SKIPPED


def test_sync_merge_conflict_is_left_for_manual_resolution(coord, builder):
    from conftest import commit_file

    repo = builder.build_clean_current()
    git(repo, "switch", "-q", "-c", "feature/work")
    commit_file(repo, "src/app.py", "VERSION = 1\nSHARED = 'feature'\n", "Feature edit")
    _advance_default(builder, "clean-current")

    result = _run(coord, _spec("clean-current", repo), Operation.SYNC)

    assert result.verdict is Verdict.FAILED
    assert result.error == "sync merge conflict"
    assert GitBackend().in_progress_operation(repo) == "merge"
    assert result.next_action and "merge --continue" in result.next_action


def test_sync_rebase_conflict_is_left_for_manual_resolution(coord, builder):
    from conftest import commit_file

    repo = builder.build_clean_current()
    git(repo, "switch", "-q", "-c", "feature/work")
    commit_file(repo, "src/app.py", "VERSION = 1\nSHARED = 'feature'\n", "Feature edit")
    _advance_default(builder, "clean-current")

    result = _run(
        coord, _spec("clean-current", repo), Operation.SYNC, sync_rebase=True
    )

    assert result.verdict is Verdict.FAILED
    assert result.error == "sync rebase conflict"
    assert GitBackend().in_progress_operation(repo) == "rebase"
    assert result.next_action and "rebase --continue" in result.next_action


def test_sync_conflict_does_not_stop_later_repositories(coord, builder):
    from conftest import commit_file

    conflict = builder.build_clean_current()
    git(conflict, "switch", "-q", "-c", "feature/work")
    commit_file(conflict, "src/app.py", "VERSION = 1\nSHARED = 'feature'\n", "Feature edit")
    _advance_default(builder, "clean-current")
    later = builder.build_clean_behind()
    specs = [_spec("conflict", conflict), _spec("later", later)]

    plan = coord.build_plan(specs, Operation.SYNC)
    report = coord.execute(plan, Operation.SYNC, "t", "/", dry_run=False)

    assert report.results[0].verdict is Verdict.FAILED
    assert report.results[1].verdict in {Verdict.UPDATED, Verdict.NOOP}


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


# -- stash-and-update ---------------------------------------------------------------


def _run_stash(coord, spec, operation=Operation.UPDATE):
    plan = coord.build_plan([spec], operation, stash=True)
    report = coord.execute(
        plan, operation, project_name="t", project_root="/", dry_run=False
    )
    return report.results[0]


def test_stash_update_clean_restore(coord, builder):
    """Local work untouched by upstream: stash, ff, restore cleanly, drop the stash."""
    repo = builder.build_stash_ok()
    g = GitBackend()
    before = g.head_sha(repo)

    result = _run_stash(coord, _spec("stash-ok", repo))

    assert result.verdict is Verdict.UPDATED
    assert result.restore == "clean"
    assert g.head_sha(repo) != before          # fast-forward happened
    assert g.stash_list(repo) == []            # stash consumed
    assert (repo / "docs" / "notes.md").read_text() == "my local notes\n"


def test_stash_update_conflict_preserves_work(coord, builder):
    """Conflicting restore: report failure, keep the stash, lose nothing."""
    repo = builder.build_stash_conflict()
    g = GitBackend()
    before = g.head_sha(repo)

    result = _run_stash(coord, _spec("stash-conflict", repo))

    assert result.verdict is Verdict.FAILED
    assert result.restore == "conflict"
    assert result.stashed is True
    assert result.next_action and "stash" in result.next_action.lower()
    # The update still happened, and the stash is retained for recovery.
    assert g.head_sha(repo) != before
    stashes = g.stash_list(repo)
    assert len(stashes) == 1
    # The preserved stash carries our uniquely named entry.
    assert "repo auto-stash" in stashes[0][1]


def test_stash_never_touches_in_progress(coord, builder):
    """Even with --stash-and-update, a rebasing repo is skipped and never stashed."""
    repo = builder.build_rebase_in_progress()
    g = GitBackend()
    result = _run_stash(coord, _spec("rebase-in-progress", repo))
    assert result.verdict is Verdict.SKIPPED
    assert result.stashed is False
    assert g.stash_list(repo) == []


def test_stash_skips_dirty_diverged(coord, builder):
    """Stashing cannot resolve a divergence, so a dirty+diverged repo still skips."""
    repo = builder.build_diverged()
    from conftest import write

    write(repo / "local-edit.txt", "uncommitted\n")  # now dirty AND diverged
    result = _run_stash(coord, _spec("diverged", repo))
    assert result.verdict is Verdict.SKIPPED
    assert "diverged" in result.reason.lower()
    assert GitBackend().stash_list(repo) == []


def test_stash_switch_default_restores_onto_default(coord, builder):
    """Dirty on a feature branch: stash, switch to default, ff, restore there."""
    repo = builder.build_clean_behind()  # main is behind upstream
    git(repo, "switch", "-q", "-c", "feature/work")
    from conftest import write

    write(repo / "scratch.txt", "wip\n")  # dirty on the feature branch
    g = GitBackend()

    result = _run_stash(coord, _spec("clean-behind", repo), Operation.SWITCH_DEFAULT)

    assert result.verdict is Verdict.UPDATED
    assert result.restore == "clean"
    assert g.current_branch(repo) == "main"
    assert (repo / "scratch.txt").exists()   # local work carried over
    assert g.stash_list(repo) == []


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
