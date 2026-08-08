"""Summary service tests.

Fixture commits are pinned to 2026-01-01 (see conftest), so a window starting before
that date includes them and one starting after excludes them. That determinism is
what makes the date-filter assertions exact.
"""

from __future__ import annotations

import pytest

from conftest import commit_file
from repo_manager.git_backend import GitBackend
from repo_manager.repository_service import RepoSpec
from repo_manager.summaries import SummaryService, parse_log, resolve_since

WIDE = "2025-12-01"   # before the fixture commit date -> includes everything
NARROW = "2026-06-01"  # after the fixture commit date -> excludes everything


def _spec(name, path):
    return RepoSpec(
        name=name, absolute_path=path, relative_path=name, groups=[], remote="origin"
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("7d", "7 days ago"),
        ("2w", "2 weeks ago"),
        ("24h", "24 hours ago"),
        ("2026-07-01", "2026-07-01"),
        ("yesterday", "yesterday"),
    ],
)
def test_resolve_since(value, expected):
    assert resolve_since(value) == expected


def test_summary_collects_commits_and_stats(builder):
    repo = builder.build_clean_current()
    commit_file(repo, "src/a.py", "x = 1\n", "feat(api): add ABC-123")
    commit_file(repo, "src/b.py", "y = 2\nz = 3\n", "fix: bug PROJ-9")

    svc = SummaryService(backend=GitBackend(), jobs=1)
    report = svc.build([_spec("r", repo)], WIDE, project_name="t", project_root="/")
    r = report.repositories[0]

    assert r.commit_count == 5  # 3 seed commits + 2 here
    assert r.total_insertions > 0
    assert r.branch == "main"
    assert "ABC-123" in r.references and "PROJ-9" in r.references
    assert "src" in r.top_directories
    # Conventional types parsed on the two we authored.
    types = {c.commit_type for c in r.commits}
    assert "feat" in types and "fix" in types


def test_summary_date_window_excludes_older_commits(builder):
    repo = builder.build_clean_current()
    svc = SummaryService(backend=GitBackend(), jobs=1)
    report = svc.build([_spec("r", repo)], NARROW, project_name="t", project_root="/")
    assert report.repositories[0].commit_count == 0


def test_summary_totals_across_repos(builder):
    r1 = builder.build_clean_current()
    r2 = builder.build_ahead()
    commit_file(r1, "f.py", "a\n", "feat: one")
    svc = SummaryService(backend=GitBackend(), jobs=2)
    report = svc.build(
        [_spec("r1", r1), _spec("r2", r2)], WIDE, project_name="t", project_root="/"
    )
    totals = report.totals()
    assert totals["total_commits"] == sum(x.commit_count for x in report.repositories)
    assert totals["repositories_with_changes"] == 2


def test_summary_preserves_input_order(builder):
    r1 = builder.build_clean_current()
    r2 = builder.build_ahead()
    svc = SummaryService(backend=GitBackend(), jobs=4)
    report = svc.build(
        [_spec("zzz", r1), _spec("aaa", r2)], WIDE, project_name="t", project_root="/"
    )
    assert [r.name for r in report.repositories] == ["zzz", "aaa"]


def test_summary_json_and_markdown_shapes(builder):
    from repo_manager.output import render_summary_json, render_summary_markdown
    import json

    repo = builder.build_clean_current()
    commit_file(repo, "x.py", "1\n", "feat: thing TEAM-1")
    svc = SummaryService(backend=GitBackend(), jobs=1)
    report = svc.build([_spec("r", repo)], WIDE, project_name="proj", project_root="/")

    data = json.loads(render_summary_json(report))
    assert data["schema_version"] == 1
    assert data["since"] == WIDE
    assert data["totals"]["total_commits"] >= 1
    assert data["repositories"][0]["commits"][0]["short_sha"]

    md = render_summary_markdown(report)
    assert md.startswith("# Change summary: proj")
    assert "TEAM-1" in md


def test_parse_log_handles_binary_and_empty():
    assert parse_log("") == []
    fs = GitBackend.LOG_FS
    raw = f"{fs}abc123{fs}Alice{fs}2026-01-01T00:00:00Z{fs}feat: x\n-\t-\timage.png\n5\t2\tsrc/a.py\n"
    commits = parse_log(raw)
    assert len(commits) == 1
    c = commits[0]
    assert c.files_changed == 2
    assert c.insertions == 5 and c.deletions == 2  # binary "-" ignored
    assert c.commit_type == "feat"
