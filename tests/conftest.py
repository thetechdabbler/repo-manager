"""Fixture repository matrix.

These fixtures are the specification for the state model. Every classification the
snapshot layer can produce has exactly one fixture here, and `MATRIX` is the single
source of truth that `test_snapshot.py` asserts against.

Repositories are built from local bare remotes so nothing here touches a network.
Git runs with global and system config neutralized so a developer's own settings
(default branch name, commit signing, hooks, templates) cannot change the outcome.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

FIXTURE_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Fixture Author",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture Author",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}

# Applied to every invocation so repository-local config cannot drift either.
GIT_FLAGS = [
    "-c", "commit.gpgsign=false",
    "-c", "tag.gpgsign=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "init.defaultBranch=main",
    "-c", "advice.detachedHead=false",
    "-c", "protocol.file.allow=always",
]


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(FIXTURE_ENV)
    return env


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run one git command inside `cwd` with a neutralized environment."""
    proc = subprocess.run(
        ["git", *GIT_FLAGS, *args],
        cwd=str(cwd),
        env=_env(),
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit_file(repo: Path, relpath: str, content: str, message: str) -> str:
    write(repo / relpath, content)
    git(repo, "add", "--", relpath)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


# --------------------------------------------------------------------------------------
# Expected-state matrix
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Expected:
    """What the snapshot layer must report for one fixture repository."""

    name: str
    classification: str
    relationship: str
    is_dirty: bool
    in_progress: str = "none"
    current_branch: str | None = "main"
    detached: bool = False
    has_upstream: bool = True
    default_branch: str | None = "main"
    inference_source: str = "remote-head"
    ahead: int | None = 0
    behind: int | None = 0
    note: str = ""


MATRIX: tuple[Expected, ...] = (
    Expected(
        name="clean-current",
        classification="current",
        relationship="current",
        is_dirty=False,
        note="Clean, tracking, identical to upstream.",
    ),
    Expected(
        name="clean-behind",
        classification="ready",
        relationship="behind",
        is_dirty=False,
        behind=2,
        note="The only state eligible for a default fast-forward update.",
    ),
    Expected(
        name="dirty",
        classification="dirty",
        relationship="behind",
        is_dirty=True,
        behind=1,
        note="Staged, unstaged, and untracked changes present at once.",
    ),
    Expected(
        name="ahead",
        classification="ahead",
        relationship="ahead",
        is_dirty=False,
        ahead=1,
        note="Local commits not pushed. Fast-forward is not possible.",
    ),
    Expected(
        name="diverged",
        classification="diverged",
        relationship="diverged",
        is_dirty=False,
        ahead=1,
        behind=1,
        note="Both sides moved. Never mutated without explicit user action.",
    ),
    Expected(
        name="detached",
        classification="detached",
        relationship="unknown",
        is_dirty=False,
        current_branch=None,
        detached=True,
        has_upstream=False,
        ahead=None,
        behind=None,
        note="HEAD points at a commit, not a branch.",
    ),
    Expected(
        name="no-upstream",
        classification="no-upstream",
        relationship="unknown",
        is_dirty=False,
        current_branch="local-only",
        has_upstream=False,
        ahead=None,
        behind=None,
        note="Branch exists locally with no tracking configuration.",
    ),
    Expected(
        name="ambiguous-default",
        classification="default-branch-unknown",
        relationship="current",
        is_dirty=False,
        current_branch="trunk",
        default_branch=None,
        inference_source="ambiguous",
        note="No origin/HEAD and no main, master, or dev branch to fall back on.",
    ),
    Expected(
        name="unreachable-remote",
        classification="remote-unavailable",
        relationship="unknown",
        is_dirty=False,
        ahead=None,
        behind=None,
        note="Remote path does not exist. Must never be reported as up to date.",
    ),
    Expected(
        name="rebase-in-progress",
        classification="in-progress",
        relationship="unknown",
        is_dirty=True,
        in_progress="rebase",
        current_branch=None,
        detached=True,
        has_upstream=False,
        ahead=None,
        behind=None,
        note="Conflicted rebase. Never mutated, under any flag combination.",
    ),
    Expected(
        name="stash-ok",
        classification="dirty",
        relationship="behind",
        is_dirty=True,
        behind=1,
        note="Local edit to a file upstream did not touch. Stash restores cleanly.",
    ),
    Expected(
        name="stash-conflict",
        classification="dirty",
        relationship="behind",
        is_dirty=True,
        behind=1,
        note="Local edit to the same lines upstream changed. Stash restore conflicts.",
    ),
)

MATRIX_BY_NAME = {e.name: e for e in MATRIX}


# --------------------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------------------


class WorkspaceBuilder:
    """Builds bare remotes and clones for the fixture matrix.

    Layout:
        root/
          remotes/<name>.git    bare remotes, outside the workspace
          seeds/<name>          scratch clones used to advance a remote
          workspace/<name>      the repositories under test
    """

    def __init__(self, root: Path):
        self.root = root
        self.remotes = root / "remotes"
        self.seeds = root / "seeds"
        self.workspace = root / "workspace"
        for d in (self.remotes, self.seeds, self.workspace):
            d.mkdir(parents=True, exist_ok=True)

    # -- primitives ---------------------------------------------------------------

    def make_remote(self, name: str, default_branch: str = "main") -> Path:
        bare = self.remotes / f"{name}.git"
        git(self.remotes, "init", "--bare", "-q", "-b", default_branch, str(bare))
        return bare

    def seed(self, name: str, bare: Path, branch: str = "main") -> Path:
        """Create the initial commit on `bare` and return a scratch clone of it."""
        seed = self.seeds / name
        git(self.seeds, "clone", "-q", str(bare), str(seed))
        commit_file(seed, "README.md", "# fixture\n", "Initial commit")
        commit_file(
            seed,
            "src/app.py",
            "VERSION = 1\nSHARED = 'original'\n",
            "Add application module",
        )
        commit_file(seed, "docs/notes.md", "original notes\n", "Add notes")
        git(seed, "push", "-q", "origin", branch)
        return seed

    def clone(self, name: str, bare: Path, branch: str = "main") -> Path:
        target = self.workspace / name
        git(self.workspace, "clone", "-q", "--branch", branch, str(bare), str(target))
        return target

    def advance_remote(self, seed: Path, branch: str = "main", count: int = 1) -> None:
        """Push `count` new commits so any existing clone falls behind."""
        for i in range(count):
            commit_file(
                seed,
                f"upstream-{i}.txt",
                f"upstream change {i}\n",
                f"Upstream commit {i}",
            )
        git(seed, "push", "-q", "origin", branch)

    def standard(self, name: str) -> tuple[Path, Path]:
        """A seeded remote plus a fresh clone, the starting point for most states."""
        bare = self.make_remote(name)
        seed = self.seed(name, bare)
        repo = self.clone(name, bare)
        return repo, seed

    # -- the twelve states --------------------------------------------------------

    def build_clean_current(self) -> Path:
        repo, _ = self.standard("clean-current")
        return repo

    def build_clean_behind(self) -> Path:
        repo, seed = self.standard("clean-behind")
        self.advance_remote(seed, count=2)
        git(repo, "fetch", "-q", "origin")
        return repo

    def build_dirty(self) -> Path:
        repo, seed = self.standard("dirty")
        self.advance_remote(seed, count=1)
        git(repo, "fetch", "-q", "origin")
        # One staged, one unstaged, one untracked.
        write(repo / "staged.txt", "staged content\n")
        git(repo, "add", "--", "staged.txt")
        write(repo / "docs/notes.md", "modified notes\n")
        write(repo / "untracked.txt", "untracked content\n")
        return repo

    def build_ahead(self) -> Path:
        repo, _ = self.standard("ahead")
        commit_file(repo, "local.txt", "local work\n", "Local commit")
        return repo

    def build_diverged(self) -> Path:
        repo, seed = self.standard("diverged")
        self.advance_remote(seed, count=1)
        git(repo, "fetch", "-q", "origin")
        commit_file(repo, "local.txt", "local work\n", "Local commit")
        return repo

    def build_detached(self) -> Path:
        repo, _ = self.standard("detached")
        first = git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
        git(repo, "checkout", "-q", first)
        return repo

    def build_no_upstream(self) -> Path:
        repo, _ = self.standard("no-upstream")
        git(repo, "switch", "-q", "-c", "local-only")
        return repo

    def build_ambiguous_default(self) -> Path:
        """No origin/HEAD, and no main, master, or dev to fall back on."""
        name = "ambiguous-default"
        bare = self.make_remote(name, default_branch="trunk")
        seed = self.seeds / name
        git(self.seeds, "clone", "-q", str(bare), str(seed))
        git(seed, "switch", "-q", "-c", "trunk")
        commit_file(seed, "README.md", "# fixture\n", "Initial commit")
        git(seed, "push", "-q", "origin", "trunk")
        git(seed, "switch", "-q", "-c", "release")
        commit_file(seed, "release.txt", "release\n", "Release branch commit")
        git(seed, "push", "-q", "origin", "release")

        repo = self.clone(name, bare, branch="trunk")
        # Strip the remote HEAD symref so inference has no proven answer.
        git(repo, "remote", "set-head", "origin", "--delete", check=False)
        return repo

    def build_unreachable_remote(self) -> Path:
        repo, _ = self.standard("unreachable-remote")
        missing = self.root / "does-not-exist" / "gone.git"
        git(repo, "remote", "set-url", "origin", str(missing))
        return repo

    def build_rebase_in_progress(self) -> Path:
        repo, _ = self.standard("rebase-in-progress")
        # Two branches editing the same line, then a rebase that must stop.
        git(repo, "switch", "-q", "-c", "feature")
        commit_file(
            repo, "src/app.py", "VERSION = 1\nSHARED = 'feature'\n", "Feature edit"
        )
        git(repo, "switch", "-q", "main")
        commit_file(
            repo, "src/app.py", "VERSION = 1\nSHARED = 'mainline'\n", "Mainline edit"
        )
        git(repo, "switch", "-q", "feature")
        proc = git(repo, "rebase", "main", check=False)
        if proc.returncode == 0:
            raise RuntimeError("rebase fixture did not conflict as expected")
        return repo

    def build_stash_ok(self) -> Path:
        """Local edit to a file the upstream commit does not touch."""
        name = "stash-ok"
        repo, seed = self.standard(name)
        commit_file(seed, "src/app.py", "VERSION = 2\nSHARED = 'original'\n", "Bump")
        git(seed, "push", "-q", "origin", "main")
        git(repo, "fetch", "-q", "origin")
        write(repo / "docs/notes.md", "my local notes\n")
        return repo

    def build_stash_conflict(self) -> Path:
        """Local edit to the same line the upstream commit changed."""
        name = "stash-conflict"
        repo, seed = self.standard(name)
        commit_file(
            seed, "src/app.py", "VERSION = 1\nSHARED = 'upstream'\n", "Upstream edit"
        )
        git(seed, "push", "-q", "origin", "main")
        git(repo, "fetch", "-q", "origin")
        write(repo / "src/app.py", "VERSION = 1\nSHARED = 'mine'\n")
        return repo

    def build_all(self) -> dict[str, Path]:
        return {
            "clean-current": self.build_clean_current(),
            "clean-behind": self.build_clean_behind(),
            "dirty": self.build_dirty(),
            "ahead": self.build_ahead(),
            "diverged": self.build_diverged(),
            "detached": self.build_detached(),
            "no-upstream": self.build_no_upstream(),
            "ambiguous-default": self.build_ambiguous_default(),
            "unreachable-remote": self.build_unreachable_remote(),
            "rebase-in-progress": self.build_rebase_in_progress(),
            "stash-ok": self.build_stash_ok(),
            "stash-conflict": self.build_stash_conflict(),
        }

    # -- extra shapes used by discovery and in-progress tests ---------------------

    def build_merge_in_progress(self) -> Path:
        repo, _ = self.standard("merge-in-progress")
        git(repo, "switch", "-q", "-c", "feature")
        commit_file(
            repo, "src/app.py", "VERSION = 1\nSHARED = 'feature'\n", "Feature edit"
        )
        git(repo, "switch", "-q", "main")
        commit_file(
            repo, "src/app.py", "VERSION = 1\nSHARED = 'mainline'\n", "Mainline edit"
        )
        proc = git(repo, "merge", "--no-ff", "feature", check=False)
        if proc.returncode == 0:
            raise RuntimeError("merge fixture did not conflict as expected")
        return repo

    def add_linked_worktree(self, repo: Path, name: str = "linked-wt") -> Path:
        """Create a worktree whose `.git` is a file, not a directory."""
        target = self.workspace / name
        git(repo, "worktree", "add", "-q", "--detach", str(target))
        return target

    def add_noise(self) -> None:
        """Directories discovery must not treat as selectable repositories."""
        # Excluded by default: a real repo buried in node_modules.
        vendored = self.workspace / "node_modules" / "vendored-pkg"
        vendored.mkdir(parents=True, exist_ok=True)
        git(vendored, "init", "-q", "-b", "main")
        commit_file(vendored, "index.js", "// vendored\n", "Vendored commit")

        # Excluded by default: a virtualenv containing a repo.
        venv_repo = self.workspace / ".venv" / "src" / "editable-dep"
        venv_repo.mkdir(parents=True, exist_ok=True)
        git(venv_repo, "init", "-q", "-b", "main")
        commit_file(venv_repo, "dep.py", "# dep\n", "Dep commit")

        # Not a repository at all.
        (self.workspace / "plain-dir" / "nested").mkdir(parents=True, exist_ok=True)
        write(self.workspace / "plain-dir" / "nested" / "file.txt", "no git here\n")

        # A real repository nested two levels down, which must be found.
        nested = self.workspace / "services" / "payments-api"
        bare = self.make_remote("payments-api", default_branch="dev")
        seed = self.seeds / "payments-api"
        git(self.seeds, "clone", "-q", str(bare), str(seed))
        git(seed, "switch", "-q", "-c", "dev")
        commit_file(seed, "api.py", "# api\n", "API commit")
        git(seed, "push", "-q", "origin", "dev")
        nested.parent.mkdir(parents=True, exist_ok=True)
        git(nested.parent, "clone", "-q", "--branch", "dev", str(bare), str(nested))

        # A repository deeper than the default max_depth of 4.
        deep = self.workspace / "a" / "b" / "c" / "d" / "e" / "too-deep"
        deep.mkdir(parents=True, exist_ok=True)
        git(deep, "init", "-q", "-b", "main")
        commit_file(deep, "deep.txt", "deep\n", "Deep commit")


# --------------------------------------------------------------------------------------
# Pytest fixtures
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def git_available() -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")


@pytest.fixture
def builder(tmp_path: Path, git_available: None) -> WorkspaceBuilder:
    return WorkspaceBuilder(tmp_path)


@pytest.fixture
def matrix(builder: WorkspaceBuilder) -> tuple[WorkspaceBuilder, dict[str, Path]]:
    """All twelve states, freshly built. Function-scoped so tests may mutate."""
    return builder, builder.build_all()


@pytest.fixture(scope="session")
def matrix_snapshots(
    tmp_path_factory: pytest.TempPathFactory, git_available: None
) -> dict:
    """All twelve states built once and snapshotted with a real fetch.

    Session-scoped because building the matrix is expensive (dozens of clones and
    commits) and the snapshot suite only reads. Parametrized assertions share this
    single result instead of rebuilding per case. Tests that mutate repositories
    must use the function-scoped `builder`/`matrix` fixtures instead.
    """
    from repo_manager.repository_service import RepoSpec, RepositoryService

    root = tmp_path_factory.mktemp("matrix")
    builder = WorkspaceBuilder(root)
    repos = builder.build_all()
    service = RepositoryService(jobs=4)
    specs = [
        RepoSpec(
            name=name,
            absolute_path=path,
            relative_path=name,
            groups=[],
            remote="origin",
        )
        for name, path in repos.items()
    ]
    results = service.snapshot_all(specs, fetch=True, fetch_timeout=15.0)
    return {snap.name: snap for snap in results}


@pytest.fixture
def discovery_workspace(builder: WorkspaceBuilder) -> WorkspaceBuilder:
    """A workspace with real repositories plus directories discovery must ignore."""
    builder.build_clean_current()
    builder.add_noise()
    root = builder.build_ahead()
    builder.add_linked_worktree(root)
    return builder
