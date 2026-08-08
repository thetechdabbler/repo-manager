"""The state model, asserted against the fixture matrix.

The matrix in conftest.py is the specification. This test builds every fixture,
snapshots it with a real fetch (the only honest way to learn true remote state),
and checks each field the matrix pins down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import MATRIX, Expected, WorkspaceBuilder
from repo_manager.repository_service import RepoSpec, RepositoryService


def _spec(name: str, path: Path) -> RepoSpec:
    return RepoSpec(
        name=name,
        absolute_path=path,
        relative_path=name,
        groups=[],
        remote="origin",
    )


@pytest.fixture
def snapshots(matrix_snapshots) -> dict:
    # Built once per session in conftest; the matrix suite only reads.
    return matrix_snapshots


@pytest.mark.parametrize("expected", MATRIX, ids=lambda e: e.name)
def test_classification(snapshots, expected: Expected):
    snap = snapshots[expected.name]
    assert snap.classification.value == expected.classification, (
        f"{expected.name}: {expected.note}\n"
        f"warnings: {snap.warnings}"
    )


@pytest.mark.parametrize("expected", MATRIX, ids=lambda e: e.name)
def test_relationship(snapshots, expected: Expected):
    snap = snapshots[expected.name]
    assert snap.remote.relationship.value == expected.relationship


@pytest.mark.parametrize("expected", MATRIX, ids=lambda e: e.name)
def test_dirty_flag(snapshots, expected: Expected):
    snap = snapshots[expected.name]
    assert snap.worktree.is_dirty is expected.is_dirty


@pytest.mark.parametrize("expected", MATRIX, ids=lambda e: e.name)
def test_in_progress(snapshots, expected: Expected):
    snap = snapshots[expected.name]
    assert snap.worktree.in_progress_operation.value == expected.in_progress


@pytest.mark.parametrize("expected", MATRIX, ids=lambda e: e.name)
def test_branch_and_detached(snapshots, expected: Expected):
    snap = snapshots[expected.name]
    assert snap.checkout.is_detached is expected.detached
    if not expected.detached:
        assert snap.checkout.current_branch == expected.current_branch


@pytest.mark.parametrize("expected", MATRIX, ids=lambda e: e.name)
def test_upstream_presence(snapshots, expected: Expected):
    snap = snapshots[expected.name]
    assert (snap.checkout.upstream_branch is not None) is expected.has_upstream


@pytest.mark.parametrize("expected", MATRIX, ids=lambda e: e.name)
def test_ahead_behind(snapshots, expected: Expected):
    snap = snapshots[expected.name]
    if expected.ahead is not None:
        assert snap.remote.ahead_count == expected.ahead
    if expected.behind is not None:
        assert snap.remote.behind_count == expected.behind


@pytest.mark.parametrize("expected", MATRIX, ids=lambda e: e.name)
def test_default_branch_inference(snapshots, expected: Expected):
    snap = snapshots[expected.name]
    assert snap.checkout.default_branch == expected.default_branch
    assert (
        snap.checkout.default_branch_inference_source.value
        == expected.inference_source
    )


def test_unreachable_never_reports_current_data(snapshots):
    """The core safety property: a failed fetch must not present stale refs."""
    snap = snapshots["unreachable-remote"]
    assert snap.remote.data_is_current is False
    assert snap.remote.relationship.value == "unknown"
    assert snap.remote.ahead_count is None
    assert snap.remote.behind_count is None


def test_fetch_failure_surfaces_git_error(builder: WorkspaceBuilder):
    """A failed fetch must report git's actual reason, not a generic 'failed'."""
    repo = builder.build_unreachable_remote()
    service = RepositoryService(jobs=1)
    snap = service.snapshot_one(_spec("unreachable-remote", repo), fetch=True)

    assert snap.remote.fetch_attempted is True
    assert snap.remote.fetch_error and snap.remote.fetch_error != "fetch failed"
    # The warning carries the specific reason after the colon.
    warning = next(w for w in snap.warnings if "fetch from 'origin' failed" in w)
    assert ": " in warning and len(warning.split(": ", 1)[1].strip()) > 0


def test_default_branch_override_beats_inference(builder: WorkspaceBuilder):
    """A profile override is the top-priority evidence source."""
    repo = builder.build_clean_current()
    service = RepositoryService(jobs=1)
    spec = RepoSpec(
        name="clean-current",
        absolute_path=repo,
        relative_path="clean-current",
        groups=[],
        default_branch_override="release/2026",
    )
    snap = service.snapshot_one(spec, fetch=False)
    assert snap.checkout.default_branch == "release/2026"
    assert snap.checkout.default_branch_inference_source.value == "profile-override"


def test_snapshot_is_json_serializable(snapshots):
    import json

    for snap in snapshots.values():
        text = json.dumps(snap.to_dict())
        assert '"schema_version": 1' in text


def test_origin_url_is_redacted(builder: WorkspaceBuilder):
    repo = builder.build_clean_current()
    from conftest import git

    git(repo, "remote", "set-url", "origin", "https://user:secrettoken@example.com/x.git")
    service = RepositoryService(jobs=1)
    spec = _spec("clean-current", repo)
    snap = service.snapshot_one(spec, fetch=False)
    assert "secrettoken" not in (snap.identity.origin_url_redacted or "")
    assert "***" in (snap.identity.origin_url_redacted or "")
