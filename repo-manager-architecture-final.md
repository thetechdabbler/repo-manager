# Repo Manager CLI: Finalized Architecture and Phase Plan

## Status

Finalized design, approved for phased implementation. Supersedes `repo-manager-architecture.md`.

No implementation exists yet. Phase 1 is the next action.

## Locked decisions

| # | Decision | Rationale |
| --- | --- | --- |
| 1 | Scope trimmed to four phases | Core value is status plus safe update. Enrichment features deferred until the core is in daily use. |
| 2 | `--discard` cut from v1 | Only unrecoverable operation in the design, hardest to test safely, and `--stash-and-update` covers the realistic need. |
| 3 | Summaries are date-based only | Removes watermark persistence and the entire commit-reachability problem. `--since 7d` delivers most of the value with no state. |
| 4 | Safety skips return exit code 3 | Skips must be impossible to ignore. `--ignore-skips` provides the scripting escape hatch. |
| 5 | Build rather than adopt `gita` | Owning the data model is a prerequisite for the planned AI functionality. Evaluation gate removed. |
| 6 | Reports are serializable structures first | A later LLM layer consumes the structured report rather than parsing rendered output. |

## Purpose

`repo-manager` is a globally installed command-line utility for working safely with a directory tree containing multiple independent Git repositories. It targets umbrella workspaces, service fleets, and microservice checkouts. It requires no submodules, no monorepo, no particular Git host, and no prescribed layout.

It answers three questions and performs two mutations:

- Which repositories are present, on what branch, in what state, and which have local work?
- Which repositories can be updated safely right now, and why can the rest not?
- What changed across the workspace in the last N days?
- Update the safe repositories on their current branch.
- Return the safe repositories to their default branch.

Anything outside that list is deferred.

## Scope

### In scope for v1

- Discovering Git worktrees below a configured root.
- Interactive and non-interactive status, update, switch-default, checkout, and summary operations.
- Project profiles selecting repositories, defining groups, and overriding inferred defaults.
- Default-branch inference with recorded evidence, plus user overrides.
- Safe handling of clean, dirty, detached, ahead, behind, divergent, and in-progress worktrees.
- Opt-in temporary stashing with conflict-safe restore reporting.
- Date-ranged commit summaries in terminal, JSON, and Markdown.
- Global installation with multiple unrelated workspace profiles.

### Explicitly out of scope for v1

- `--discard` or any operation that destroys uncommitted work.
- Review watermarks and `--since last`.
- GitHub pull-request enrichment.
- LLM explanation modes.
- Creating, deleting, merging, or pushing branches.
- Automatic conflict resolution.
- Non-Git version control.
- Multiple workspace roots in one profile.
- A Codex or Claude skill wrapper.

## Design principles

1. **Safety before convenience.** A clean, tracking worktree that can fast-forward is the only default mutation case.
2. **Discover broadly, operate narrowly.** Discovery may recurse; mutation always uses explicit selection.
3. **Git remains the source of truth.** The tool stores preferences, not a shadow copy of Git state.
4. **Structure first, rendering second.** Every command produces a serializable report; the terminal table is one renderer among several. This is what keeps a future AI layer a consumer rather than a rewrite.
5. **Explain skipped work.** A skip is only valid when the reason and the next action are both stated.
6. **Interactive and automatable.** Every menu action has a non-interactive equivalent with structured output.
7. **Never hang.** Every network operation has a timeout and prompt suppression.

## Terminology

| Term | Meaning |
| --- | --- |
| Workspace | A directory tree containing related repositories. |
| Project profile | Saved configuration for one workspace. |
| Repository | One Git worktree discovered or configured below a workspace root. |
| Default branch | The branch designated as the normal update target for one repository. |
| Upstream | The configured remote tracking branch for the current branch. |
| Switch-default | Switch a repository to its default branch and fast-forward it. |
| Safe update | A fast-forward-only pull that changes neither branch nor local work. |

## High-level architecture

```text
                       +----------------------------+
                       |        CLI interface        |
                       |  commands, flags, menus     |
                       +-------------+---------------+
                                     |
           +-------------------------+-------------------------+
           |                                                   |
+----------v-----------+                          +------------v-----------+
| Configuration service|                          | Operation coordinator  |
| profiles, validation |                          | plan, confirm, execute |
+----------+-----------+                          +------------+-----------+
           |                                                   |
           |                                      +------------v-----------+
           |                                      |     Policy engine       |
           |                                      | eligibility + reasons   |
           |                                      +------------+-----------+
           |                                                   |
           +-------------------------+-------------------------+
                                     |
                       +-------------v---------------+
                       |    Repository service       |
                       | snapshots, parallel fetch   |
                       +-------------+---------------+
                                     |
                       +-------------v---------------+
                       |        Git backend          |
                       | explicit git subprocesses   |
                       +-------------+---------------+
                                     |
                       +-------------v---------------+
                       |   Local Git repositories    |
                       +-----------------------------+

           All commands emit a serializable report consumed by
           the output layer: table | json | markdown | quiet
```

### Components

| Component | Responsibilities |
| --- | --- |
| CLI interface | Parses commands and flags, runs menus, resolves selection expressions, returns exit codes. |
| Configuration service | Loads and validates profiles, resolves groups, writes profiles preserving comments. |
| Discovery service | Finds Git worktrees, applies exclusions, detects linked worktrees. |
| Git backend | The only component that spawns subprocesses. Typed results, no global cwd changes. |
| Repository service | Assembles snapshots, runs parallel fetch with timeouts. |
| Policy engine | Pure functions mapping snapshot plus requested operation to allow, skip with reason, or require confirmation. |
| Operation coordinator | Builds the plan, gets confirmation, executes serially, collects results. |
| Output layer | Renders one report structure as table, JSON, Markdown, or nothing. |
| Summary service | Builds date-ranged commit reports (Phase 4). |

## Concurrency model

Decided up front because retrofitting it touches the coordinator and the output layer.

- Read operations and fetches run in a `ThreadPoolExecutor`, default 8 workers, overridable with `--jobs`.
- Mutations run strictly serially. Parallel mutation makes failure recovery and log interleaving unmanageable for no meaningful gain.
- The output layer buffers per-repository results and renders in stable configuration order regardless of completion order.
- Progress during parallel reads is a single aggregate indicator, not interleaved per-repo lines.

### Network safety

Every `git fetch` runs with:

- A hard timeout, default 30 seconds, configurable per profile.
- `GIT_TERMINAL_PROMPT=0` in the environment.
- `-c core.askPass=` and `-c credential.interactive=never`.
- SSH batch mode where an SSH remote is detected.

An SSH passphrase prompt, a 2FA wall, or an unreachable host must resolve to `remote-unavailable`, never a hung process.

## Repository state model

Raw Git observation is kept separate from policy interpretation. All structures are JSON-serializable dataclasses with stable, versioned field names.

```text
RepositorySnapshot
├── schema_version
├── identity
│   ├── name
│   ├── absolute_path
│   ├── relative_path
│   ├── groups
│   ├── remote_name
│   └── origin_url_redacted
├── checkout
│   ├── head_sha
│   ├── current_branch | detached_sha
│   ├── upstream_branch
│   ├── default_branch
│   └── default_branch_inference_source
├── worktree
│   ├── staged_count
│   ├── unstaged_count
│   ├── untracked_count
│   ├── is_dirty
│   └── in_progress_operation      # rebase | merge | cherry-pick | bisect | none
├── remote
│   ├── fetch_attempted
│   ├── fetch_result               # ok | timeout | auth-required | unreachable | skipped
│   ├── ahead_count
│   ├── behind_count
│   └── relationship               # current | ahead | behind | diverged | unknown
└── classification
```

`classification` is exactly one of:

`ready`, `current`, `dirty`, `in-progress`, `detached`, `no-upstream`, `default-branch-unknown`, `ambiguous-remote`, `diverged`, `remote-unavailable`.

`relationship` must be `unknown` when no current remote reference can be obtained. A stale remote-tracking ref must never be presented as current state.

### In-progress detection

Before any other classification, check for `.git/rebase-merge`, `.git/rebase-apply`, `.git/MERGE_HEAD`, `.git/CHERRY_PICK_HEAD`, `.git/BISECT_LOG`. A repository mid-rebase reports as dirty under naive inspection, which is technically true and practically useless. `in-progress` takes precedence over `dirty` and names the specific operation in every skip message.

### Linked worktrees

A `.git` file rather than a directory indicates a linked worktree. Discovery detects these, excludes them by default, and reports them explicitly rather than silently skipping or misclassifying them.

### Remote resolution

`origin` is the default. If absent and exactly one other remote exists, use it and state the substitution. Multiple remotes with no `origin` classifies as `ambiguous-remote` and requires profile configuration. The remote is overridable per repository.

## Safety policy

| Condition | Classification | `update` | `switch-default` | `checkout` |
| --- | --- | --- | --- | --- |
| Clean, tracking, behind | ready | fast-forward | switch then fast-forward | allowed |
| Clean, current | current | no-op | switch if needed | allowed |
| Dirty | dirty | skip, or stash mode | skip, or stash mode | refuse on overwrite risk |
| Rebase/merge/cherry-pick in progress | in-progress | skip, always | skip, always | skip, always |
| Detached HEAD | detached | skip | skip | allowed explicitly |
| No upstream | no-upstream | skip | skip if default also untracked | allowed |
| Ahead only | ahead | skip | allowed if default is clean and not divergent | allowed |
| Diverged | diverged | skip | skip | allowed |
| Remote unavailable | remote-unavailable | skip | skip | local branches only |
| Default branch unknown | default-branch-unknown | n/a | skip | allowed |

`in-progress` is never overridable, including in stash mode. Stashing on top of a conflicted rebase is a data-loss path.

Every skip produces a reason and a next action:

```text
SKIPPED  services/payments-api
Reason:  4 local changes would be affected by switching to dev.
Next:    inspect with `git -C services/payments-api status`,
         or re-run with --stash-and-update.
```

## Command contract

### status

```bash
repo-manager status
repo-manager status --group services
repo-manager status --fetch --jobs 8
repo-manager status --json
```

Read-only unless `--fetch` is passed. Reports name, relative path, current branch or detached state, default branch with inference source, HEAD and last commit subject/date, redacted origin URL, upstream, dirty counts, ahead/behind, and classification.

### update

```bash
repo-manager update --dry-run
repo-manager update --group libraries
repo-manager update --stash-and-update --repo platform
```

Fetches selected repositories in parallel, then updates the current branch only. Never switches branches.

```text
git fetch --prune <remote>      # parallel
git pull --ff-only              # serial, eligible repos only
```

### switch-default

```bash
repo-manager switch-default --dry-run
repo-manager switch-default --group services
```

`sync` is retained as a deprecated alias. Makes the default branch active, then fast-forwards it.

```text
git fetch --prune <remote>
git switch <default-branch>
git pull --ff-only
```

### checkout

```bash
repo-manager checkout --repo payments-api --branch feature/example
repo-manager checkout --group libraries --branch dev
repo-manager checkout --branch default
```

Creates a local tracking branch when only a remote branch exists. Refuses when local changes would be overwritten. Never creates a branch merely because the same name exists in another repository.

### summary (Phase 4)

```bash
repo-manager summary --since 7d
repo-manager summary --since 2026-07-01 --group services
repo-manager summary --since 7d --markdown report.md
```

Read-only. Date rule documented explicitly in help: commits selected by committer date on the current branch, first-parent by default.

### Stash mode

Opt-in via `--stash-and-update`. For an eligible dirty repository:

1. Create a uniquely named stash including untracked files.
2. Update via the fast-forward-only path.
3. Restore the stash.
4. Report clean restore, conflict, or failure.

The stash is never dropped when restore conflicts. The stash ref and the exact recovery commands are printed.

## Interactive mode

```text
Project: services

1. Show repository status
2. Update selected repositories
3. Switch selected repositories to default branches
4. Checkout a branch
5. Summarize recent changes
6. Configure project
7. Exit
```

Selection expressions: `all`, `clean`, `dirty`, `group:<name>`, `search:<text>`.

Before any mutation, a plan table shows operation, repository, branch, classification, and expected outcome. Confirmation is per batch.

## Configuration

TOML, read with `tomllib`, written with `tomlkit` to preserve user comments and formatting.

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

[policy]
pull_mode = "ff-only"
skip_dirty = true
fetch_before_update = true
fetch_before_status = false
fetch_timeout_seconds = 30
jobs = 8

[[repositories]]
path = "."
name = "local-environment"
default_branch = "main"
groups = ["environment"]

[[repositories]]
path = "platform"
default_branch = "main"
groups = ["platform"]

[[repositories]]
path = "services/payments-api"
default_branch = "dev"
groups = ["services", "payments"]
remote = "origin"
```

Groups are defined exactly one way: the per-repository `groups` array. There is no separate group aliasing table.

Repository paths outside the workspace root are rejected.

### Storage

```text
~/.config/repo-manager/
├── config.toml                 # global settings, active project
└── projects/
    └── services.toml
```

No state directory in v1. Date-based summaries need no persistence.

A project-local `.repo-manager.toml` may be loaded as a shareable overlay and must never contain credentials.

### Default-branch inference

Ordered evidence, with the source recorded and displayed:

1. Profile override.
2. `refs/remotes/<remote>/HEAD`.
3. Current branch's upstream when it is a common default candidate.
4. Existing `main`, then `master`, then `dev`.
5. `ambiguous`, requiring user selection.

## Git backend contract

The only component that spawns subprocesses. Takes a repository path as an argument, never changes the process working directory, and records redacted command results.

Reads:

```text
discover_worktrees(root, max_depth, exclude)
get_head(repo)
get_current_branch(repo)
get_status_porcelain_v2(repo)
get_in_progress_operation(repo)
get_upstream(repo, branch)
get_default_branch(repo, remote)
get_ahead_behind(repo, upstream)
get_last_commit(repo)
list_local_branches(repo)
list_remote_branches(repo, remote)
list_remotes(repo)
log_range(repo, since, until)
```

Mutations:

```text
fetch(repo, remote, prune=True, timeout=30)
pull_fast_forward_only(repo)
switch(repo, branch)
create_tracking_branch(repo, branch, remote_branch)
stash_push(repo, include_untracked=True, message)
stash_pop(repo, stash_ref)
```

Use `git status --porcelain=v2 -z` and other script-oriented plumbing. Never parse terminal-oriented output.

## Output and exit codes

Formats: default terminal table, `--json`, `--markdown <path>`, `--quiet`.

Credentials from remote URLs and environment variables must never appear in any output.

| Code | Meaning |
| --- | --- |
| 0 | All requested operations completed or were already current. |
| 1 | One or more repositories failed. |
| 2 | Invalid command, configuration, or selection. |
| 3 | One or more operations were skipped for a safety condition. |

A safety skip returns 3 by default. This is intentional: skips must be impossible to ignore. `--ignore-skips` downgrades skip-only runs to 0 for scripting and shell-prompt use. The behavior is documented prominently in `--help`.

When failures and skips both occur, return the highest-severity code. Every per-repository result stays in the output regardless.

## Implementation structure

```text
repo-manager/
├── pyproject.toml
├── src/repo_manager/
│   ├── cli.py
│   ├── models.py
│   ├── config.py
│   ├── discovery.py
│   ├── git_backend.py
│   ├── repository_service.py
│   ├── policy.py
│   ├── operations.py
│   ├── output.py
│   └── summaries.py
└── tests/
    ├── conftest.py            # fixture repository matrix
    ├── test_discovery.py
    ├── test_snapshot.py
    ├── test_policy.py
    ├── test_operations.py
    ├── test_summaries.py
    └── test_cli.py
```

Dependencies: Python 3.11+, Typer, Rich, tomlkit, stdlib subprocess and concurrent.futures.

## Testing strategy

### Fixture matrix

A pytest fixture builds local bare remotes and clones producing all twelve states. These fixtures are written **before** the state model, because they are the specification for it.

1. Clean and current.
2. Clean and behind.
3. Staged, unstaged, and untracked changes.
4. Ahead only.
5. Diverged.
6. Detached HEAD.
7. No upstream.
8. Ambiguous default branch.
9. Unreachable remote.
10. Rebase in progress.
11. Stash restore that succeeds.
12. Stash restore that conflicts.

### Required tests

1. Discovery finds only intended worktrees, honors exclusions, and flags linked worktrees.
2. `init` saves a valid profile recording default-branch inference evidence.
3. Snapshot classification is correct for all twelve fixture states.
4. Policy allows mutation only for eligible states, per the safety table.
5. `update` mutates only clean, behind, tracking repositories.
6. `switch-default` switches only eligible repositories.
7. `--dry-run` executes zero mutation commands.
8. Stash mode preserves changes or reports an actionable conflict without dropping the stash.
9. `in-progress` is never mutated under any flag combination.
10. Fetch timeout produces `remote-unavailable`, not a hang.
11. Summary date ranges include exactly the expected commits.
12. JSON output fields are stable and documented.
13. No output path leaks credentials from remote URLs.

### Manual acceptance

Against a disposable multi-repository workspace, never a real one:

```bash
repo-manager init /tmp/repo-manager-fixture --name fixture
repo-manager status --project fixture --json
repo-manager update --project fixture --dry-run
repo-manager switch-default --project fixture --dry-run
repo-manager summary --project fixture --since 7d --markdown /tmp/summary.md
```

## Phases

Each phase ships something used in real work before the next begins.

### Phase 1: See

Fixture harness first, then package scaffold, `init` with interactive selection and displayed inference evidence, discovery, `RepositorySnapshot`, `status`, `--json`, parallel fetch with timeouts, in-progress and linked-worktree detection.

**Ship criterion:** `repo-manager status` replaces the daily shell loop, and it correctly classifies every one of the twelve fixture states.

### Phase 2: Move

`update`, `switch-default`, `checkout`, `--dry-run`, the policy engine with reason plus next action, selection expressions, the pre-mutation plan table, per-batch confirmation, exit codes.

**Ship criterion:** manual cross-repo `git pull` stops, and every skip states what to do about it.

### Phase 3: Recover

`--stash-and-update` with unique stash naming, conflict-safe restore, and recovery instructions. Full in-progress enforcement across all paths.

**Ship criterion:** a deliberately triggered stash-restore conflict in the fixture matrix loses nothing and prints working recovery commands.

### Phase 4: Summarize

`summary --since <duration|date>`, structured `SummaryReport`, terminal, JSON, and Markdown renderers, commit metadata, file statistics, top-level directories touched, parsed ticket references.

**Ship criterion:** the Markdown output is used for a real standup or handoff.

## Deferred

Revisit only once Phases 1 through 4 are in regular use.

- AI and LLM explanation layers, consuming `SummaryReport` and `RepositorySnapshot` directly. Metadata-only by default; any mode sending source or patches requires separate explicit acknowledgement, and organizational review before use on sensitive code.
- Review watermarks and `--since last`.
- GitHub pull-request enrichment, with unmatched commits explicitly labeled.
- `--discard`, if it is ever justified, with its own design review.
- Multiple workspace roots.
- Read-only linked-worktree support.
- Non-GitHub PR providers.
- Scheduled read-only change briefs.
- TUI dashboard.

## Open items

- [ ] Confirm terminal renderer library choice survives first contact. Rich is assumed; drop to plain formatting if it fights the parallel progress display.
- [ ] Fix the exact `--since` date rule wording in help text during Phase 4.
- [ ] Decide whether `init` should offer to write a project-local overlay for team sharing, or defer entirely.
