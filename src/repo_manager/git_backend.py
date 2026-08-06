"""The only component that spawns git subprocesses.

Design rules enforced here:
- Never change the process working directory; every call takes a repo path.
- Every network call has a hard timeout and suppresses interactive prompts, so an
  SSH passphrase or 2FA wall fails cleanly instead of hanging the whole run.
- Parse only script-oriented plumbing (`--porcelain=v2 -z`, `for-each-ref`), never
  terminal-oriented output.
- Redact credentials from any remote URL before it leaves this module.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Config neutralized on every call so a developer's global settings, hooks, or
# credential helpers cannot change behavior or introduce a hang.
_BASE_FLAGS = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "credential.helper=",
    "-c", "credential.interactive=never",
    "-c", "advice.detachedHead=false",
    "-c", "protocol.file.allow=always",
]

_NETWORK_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    # BatchMode makes SSH fail instead of prompting for a passphrase/host key.
    "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new",
}

_CRED_URL = re.compile(r"(://)([^/@:\s]+)(:[^/@\s]+)?@")


def redact_url(url: str | None) -> str | None:
    """Strip any `user:token@` credentials from a remote URL."""
    if not url:
        return url
    return _CRED_URL.sub(r"\1***@", url)


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class GitError(RuntimeError):
    def __init__(self, args: list[str], result: GitResult):
        self.args_run = args
        self.result = result
        super().__init__(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


class GitBackend:
    def __init__(self, git_executable: str = "git"):
        self.git = git_executable

    # -- core runner --------------------------------------------------------------

    def run(
        self,
        repo: Path | None,
        *args: str,
        check: bool = False,
        timeout: float | None = None,
        network: bool = False,
    ) -> GitResult:
        cmd = [self.git, *_BASE_FLAGS]
        if repo is not None:
            cmd += ["-C", str(repo)]
        cmd += list(args)

        env = None
        if network:
            import os

            env = dict(os.environ)
            env.update(_NETWORK_ENV)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            partial = ""
            if exc.stderr:
                partial = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
            result = GitResult(returncode=124, stdout="", stderr=partial, timed_out=True)
            if check:
                raise GitError(list(args), result) from exc
            return result

        result = GitResult(proc.returncode, proc.stdout, proc.stderr)
        if check and not result.ok:
            raise GitError(list(args), result)
        return result

    # -- discovery ----------------------------------------------------------------

    def is_worktree(self, path: Path) -> bool:
        """True if `path` is the top level of a working tree (repo or linked)."""
        if not (path / ".git").exists():
            return False
        res = self.run(path, "rev-parse", "--is-inside-work-tree")
        return res.ok and res.stdout.strip() == "true"

    def git_common_dir(self, repo: Path) -> Path | None:
        res = self.run(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if not res.ok:
            return None
        return Path(res.stdout.strip())

    def git_dir(self, repo: Path) -> Path | None:
        res = self.run(repo, "rev-parse", "--absolute-git-dir")
        if not res.ok:
            return None
        return Path(res.stdout.strip())

    def is_linked_worktree(self, repo: Path) -> bool:
        """A linked worktree has a `.git` file and a git-dir under the main repo."""
        dot_git = repo / ".git"
        return dot_git.is_file()

    # -- reads --------------------------------------------------------------------

    def head_sha(self, repo: Path) -> str | None:
        res = self.run(repo, "rev-parse", "HEAD")
        return res.stdout.strip() if res.ok else None

    def current_branch(self, repo: Path) -> str | None:
        """Branch name, or None when HEAD is detached."""
        res = self.run(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        return res.stdout.strip() if res.ok and res.stdout.strip() else None

    def upstream(self, repo: Path, branch: str | None = None) -> str | None:
        ref = f"{branch}@{{upstream}}" if branch else "@{upstream}"
        res = self.run(
            repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", ref
        )
        return res.stdout.strip() if res.ok and res.stdout.strip() else None

    def remotes(self, repo: Path) -> list[str]:
        res = self.run(repo, "remote")
        return [l for l in res.stdout.splitlines() if l.strip()] if res.ok else []

    def remote_url(self, repo: Path, remote: str) -> str | None:
        res = self.run(repo, "remote", "get-url", remote)
        return redact_url(res.stdout.strip()) if res.ok else None

    def ahead_behind(
        self, repo: Path, upstream: str
    ) -> tuple[int, int] | None:
        """(ahead, behind) relative to `upstream`, or None if not computable."""
        res = self.run(
            repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}"
        )
        if not res.ok:
            return None
        parts = res.stdout.split()
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    def ref_exists(self, repo: Path, ref: str) -> bool:
        return self.run(repo, "rev-parse", "--verify", "--quiet", ref).ok

    def remote_head_branch(self, repo: Path, remote: str) -> str | None:
        """The branch `refs/remotes/<remote>/HEAD` points at, if the symref exists."""
        res = self.run(repo, "symbolic-ref", f"refs/remotes/{remote}/HEAD")
        if not res.ok or not res.stdout.strip():
            return None
        # e.g. refs/remotes/origin/main -> main
        ref = res.stdout.strip()
        prefix = f"refs/remotes/{remote}/"
        return ref[len(prefix):] if ref.startswith(prefix) else None

    def local_branches(self, repo: Path) -> list[str]:
        res = self.run(
            repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/"
        )
        return [l for l in res.stdout.splitlines() if l.strip()] if res.ok else []

    def remote_branches(self, repo: Path, remote: str) -> list[str]:
        res = self.run(
            repo,
            "for-each-ref",
            "--format=%(refname:short)",
            f"refs/remotes/{remote}/",
        )
        out = []
        prefix = f"{remote}/"
        for line in res.stdout.splitlines() if res.ok else []:
            line = line.strip()
            if not line or line == f"{remote}/HEAD":
                continue
            out.append(line[len(prefix):] if line.startswith(prefix) else line)
        return out

    def in_progress_operation(self, repo: Path) -> str:
        """One of: none, rebase, merge, cherry-pick, revert, bisect."""
        gd = self.git_dir(repo)
        if gd is None:
            return "none"
        checks = [
            ("rebase-merge", "rebase"),
            ("rebase-apply", "rebase"),
            ("MERGE_HEAD", "merge"),
            ("CHERRY_PICK_HEAD", "cherry-pick"),
            ("REVERT_HEAD", "revert"),
            ("BISECT_LOG", "bisect"),
        ]
        for marker, label in checks:
            if (gd / marker).exists():
                return label
        return "none"

    def status_counts(self, repo: Path) -> tuple[int, int, int, int]:
        """(staged, unstaged, untracked, unmerged) from porcelain v2."""
        res = self.run(
            repo,
            "status",
            "--porcelain=v2",
            "--untracked-files=all",
            "-z",
        )
        staged = unstaged = untracked = unmerged = 0
        if not res.ok:
            return (0, 0, 0, 0)
        for entry in res.stdout.split("\0"):
            if not entry:
                continue
            tag = entry[0]
            if tag == "?":
                untracked += 1
            elif tag == "u":
                unmerged += 1
            elif tag in ("1", "2"):
                # Format: "1 XY ..." where XY are staged/unstaged status codes.
                xy = entry[2:4]
                if len(xy) == 2:
                    if xy[0] != ".":
                        staged += 1
                    if xy[1] != ".":
                        unstaged += 1
        return (staged, unstaged, untracked, unmerged)

    def last_commit(self, repo: Path) -> tuple[str, str, str, str, str] | None:
        """(sha, short_sha, subject, author_name, committer_date_iso)."""
        res = self.run(
            repo,
            "log",
            "-1",
            "--no-color",
            "--pretty=format:%H%x00%h%x00%s%x00%an%x00%cI",
        )
        if not res.ok or not res.stdout.strip():
            return None
        parts = res.stdout.split("\0")
        if len(parts) != 5:
            return None
        return (parts[0], parts[1], parts[2], parts[3], parts[4])

    def branch_exists_local(self, repo: Path, branch: str) -> bool:
        return self.ref_exists(repo, f"refs/heads/{branch}")

    def branch_exists_remote(self, repo: Path, remote: str, branch: str) -> bool:
        return self.ref_exists(repo, f"refs/remotes/{remote}/{branch}")

    # -- network ------------------------------------------------------------------

    def fetch(
        self, repo: Path, remote: str, prune: bool = True, timeout: float = 30.0
    ) -> GitResult:
        args = ["fetch", "--quiet"]
        if prune:
            args.append("--prune")
        args.append(remote)
        return self.run(repo, *args, timeout=timeout, network=True)

    # -- local mutations ----------------------------------------------------------
    #
    # These never touch the network. Update/switch-default fetch first (in
    # parallel), then fast-forward locally against the already-updated ref, so a
    # mutation can never block on a remote.

    def merge_ff_only(self, repo: Path, ref: str) -> GitResult:
        """Fast-forward the current branch to `ref`. Fails if not a fast-forward."""
        return self.run(repo, "merge", "--ff-only", ref)

    def switch(self, repo: Path, branch: str) -> GitResult:
        """Check out an existing local branch. Git refuses on overwrite risk."""
        return self.run(repo, "switch", branch)

    def switch_create_tracking(
        self, repo: Path, branch: str, start_point: str
    ) -> GitResult:
        """Create `branch` tracking `start_point` (e.g. origin/dev) and switch to it."""
        return self.run(repo, "switch", "-c", branch, "--track", start_point)
