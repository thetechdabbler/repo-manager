# Configuration

A profile describes one workspace: where it is, which repositories belong to it, how
they group, and the policy for operating on them. Profiles are TOML, written with
comment-preserving formatting so you can annotate them by hand.

## Creating a profile

`init` discovers repositories and writes the profile for you. It performs no git
mutation.

```bash
repo init ~/work/services --name services
```

Interactively it shows a selectable table with each repository's branch and inferred
default. Non-interactively, `--yes` selects everything discovered.

## Profile schema

```toml
schema_version = 1

[project]
name = "services"
root = "/Users/alex/work/services"
default_remote = "origin"

[discovery]
max_depth = 4
include_root_repository = true
exclude = [".git", ".venv", "node_modules", "outputs", ".pytest_cache"]
include_linked_worktrees = false
descend_into_repositories = false

[policy]
pull_mode = "ff-only"
skip_dirty = true
fetch_before_update = true
fetch_before_status = false
fetch_timeout_seconds = 30
jobs = 8

[[repositories]]
path = "."
name = "workspace-root"
default_branch = "main"
groups = ["root"]

[[repositories]]
path = "services/payments-api"
default_branch = "dev"
groups = ["services", "payments"]
remote = "origin"
```

### Repositories

Each `[[repositories]]` entry has a `path` relative to `root` (paths outside the root
are rejected), an optional `name`, an optional `groups` list, an optional
`default_branch` override, and an optional `remote` override.

Groups are defined exactly one way: the per-repository `groups` array. Any command
that accepts `--group` matches against it.

### Discovery

`max_depth` bounds how deep the scan walks; `exclude` lists directory names to skip
(package, vendor, and build directories by default). By default discovery stops at the
first repository it finds and does not look inside it.

Set `descend_into_repositories = true` (or pass `--include-nested` to `init`) when the
workspace root is itself a Git repository that contains independent clones, an
*umbrella* layout. Discovery then walks past a found repository to surface the clones
inside it, while still skipping submodules, linked worktrees, and excluded
directories. See [`init`](../reference/init.md#umbrella-repositories-include-nested).

## Default-branch inference

When you do not set `default_branch`, it is inferred in this order, and the source is
recorded so you can tell a proven default from a heuristic:

1. A profile override.
2. `refs/remotes/<remote>/HEAD`.
3. The current branch's upstream, if it is a common default name.
4. An existing `main`, then `master`, then `dev`.
5. Otherwise `ambiguous`, which shows as `default-branch-unknown` and requires you to
   set the value.

## Removing a profile

```bash
repo remove <name>
```

`remove` deletes only the profile's config file. It never touches a repository or the
workspace on disk. It names the exact file it will remove and asks for confirmation
(`--yes` to skip), and clears the active-project pointer if it referred to that
profile. There is no command that deletes repositories; that is out of scope by
design.

## Storage and overrides

Projects live under `~/.config/repo/projects/`. On first use, `repo` moves an existing
`~/.config/repo-manager/` directory to `~/.config/repo/`. If both directories exist,
the command stops and asks for manual resolution instead of merging files. Set
`REPO_HOME` to use a different new configuration directory.
