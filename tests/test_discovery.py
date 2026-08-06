"""Discovery finds intended worktrees and nothing else."""

from __future__ import annotations

from repo_manager.discovery import DEFAULT_EXCLUDES, discover_worktrees


def test_finds_real_repos_and_ignores_noise(discovery_workspace):
    root = discovery_workspace.workspace
    result = discover_worktrees(root, max_depth=4, exclude=DEFAULT_EXCLUDES)
    found = {r.relative_path for r in result.repositories}

    # Real repositories that must be found.
    assert "clean-current" in found
    assert "ahead" in found
    assert "services/payments-api" in found

    # Excluded directories must never yield their nested repos.
    assert not any("node_modules" in p for p in found)
    assert not any(".venv" in p for p in found)

    # A repo deeper than max_depth must not be found.
    assert not any("too-deep" in p for p in found)

    # A plain directory tree is not a repository.
    assert not any("plain-dir" in p for p in found)


def test_linked_worktree_excluded_by_default(discovery_workspace):
    root = discovery_workspace.workspace
    result = discover_worktrees(root, max_depth=4, exclude=DEFAULT_EXCLUDES)
    found = {r.relative_path for r in result.repositories}

    assert not any("linked-wt" in p for p in found)
    assert any("linked-wt" in r.relative_path for r in result.linked_worktrees)
    assert any("linked worktree" in w for w in result.warnings)


def test_linked_worktree_included_when_requested(discovery_workspace):
    root = discovery_workspace.workspace
    result = discover_worktrees(
        root, max_depth=4, exclude=DEFAULT_EXCLUDES, include_linked_worktrees=True
    )
    found = {r.relative_path for r in result.repositories}
    assert any("linked-wt" in p for p in found)


def test_does_not_descend_into_found_repo(discovery_workspace):
    """A repo's own subdirectories must not be reported as separate repos."""
    root = discovery_workspace.workspace
    result = discover_worktrees(root, max_depth=6, exclude=DEFAULT_EXCLUDES)
    paths = [r.relative_path for r in result.repositories]
    # services/payments-api is found; nothing under it should appear.
    assert not any(
        p.startswith("services/payments-api/") for p in paths
    )


def test_depth_limit_respected(discovery_workspace):
    root = discovery_workspace.workspace
    shallow = discover_worktrees(root, max_depth=1, exclude=DEFAULT_EXCLUDES)
    # payments-api is two levels deep, so depth 1 must miss it.
    assert not any(
        "payments-api" in r.relative_path for r in shallow.repositories
    )


def test_missing_root_warns(tmp_path):
    result = discover_worktrees(tmp_path / "nope", max_depth=4)
    assert result.repositories == []
    assert any("not a directory" in w for w in result.warnings)
