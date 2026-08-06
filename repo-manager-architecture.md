# Repo Manager CLI: Architecture and Implementation Plan

## Status

Proposed design. No CLI implementation exists yet.

## Purpose

`repo-manager` is a globally installed command-line utility for working safely with a project directory that contains multiple independent Git repositories. It is intended for umbrella workspaces, service fleets, microservice checkouts, and any directory tree where related repositories must be inspected, updated, switched between branches, and summarized together.

The utility treats every discovered repository as an independent Git worktree. It does not require Git submodules, a monorepo, a particular Git host, or a prescribed directory layout.

The primary user outcome is a reliable answer to these questions:

- Which repositories are present, which branch is each one on, and which have local work?
- Which repositories can be updated safely right now?
- How can selected repositories return to their default branches and receive the latest changes?
- What changed across the workspace since the last time it was reviewed?

## Scope

### In scope

- Discovering Git repositories below a configured root directory.
- Interactive and non-interactive status, update, sync, checkout, and summary operations.
- Project profiles that select repositories, define groups, and override inferred defaults.
- Default-branch inference and user-configured overrides.
- Safe handling of clean, dirty, detached, ahead, behind, and divergent worktrees.
- Optional temporary stashing and intentionally destructive reset behavior.
- Incremental commit summaries, optional GitHub pull-request enrichment, and optional LLM explanations.
- Global installation and management of multiple unrelated workspace profiles.

### Out of scope for the first release

- Creating, deleting, merging, or pushing branches.
- Resolving merge or stash conflicts automatically.
- Managing non-Git version control systems.
- Performing a GitHub write such as creating pull requests or changing issues.
- Replacing Git itself or hiding Git commands from the operation log.
- Making an LLM integration mandatory for summaries.

## Design principles

1. **Safety before convenience.** A clean, tracking worktree that can fast-forward is the only default mutation case.
2. **Discover broadly, operate narrowly.** Discovery can be recursive; mutation always uses explicit selected repositories.
3. **Git remains the source of truth.** The CLI stores preferences and summary cursors, not a duplicate representation of Git state.
4. **Interactive and automatable.** Every menu action has an equivalent non-interactive command and structured output.
5. **Explain skipped work.** A skipped repository is a valid result only when the reason and next action are clear.
6. **Optional integrations degrade cleanly.** GitHub and LLM features enhance summaries but never block local Git workflows.
7. **Keep private source under user control.** LLM summaries default to metadata-only input. Sending patches or source excerpts needs explicit opt-in.

## Terminology

| Term | Meaning |
| --- | --- |
| Workspace | A directory tree containing related repositories. |
| Project profile | Saved configuration for one workspace. |
| Repository | One Git worktree, discovered or configured below a workspace root. |
| Default branch | The branch designated as the normal update target for one repository. |
| Current branch | The branch currently checked out in a repository. |
| Upstream | The configured remote tracking branch for the current branch. |
| Sync | Switch a repository to its default branch and fast-forward it to the configured remote. |
| Watermark | Last reviewed commit stored for incremental summaries. |
| Safe update | A fast-forward-only pull that does not change branch or discard work. |

## Example workspace

The current `services-workspace` checkout is an example, not a special case. It contains an environment repository at the root, an `platform` checkout, four domain-agent checkouts, and four MCP checkouts. They are independent repositories rather than Git submodules.

An initialized profile could express that layout as:

```toml
[project]
name = "services"
root = "/Users/alex/work/services"

[discovery]
max_depth = 4
exclude = [".git", ".venv", "node_modules", "outputs", ".pytest_cache"]

[[repositories]]
path = "."
name = "local-environment"
groups = ["environment"]
default_branch = "main"

[[repositories]]
path = "platform"
groups = ["platform"]
default_branch = "main"

[[repositories]]
path = "services/payments-api"
groups = ["services", "payments"]
default_branch = "dev"
```

Other repositories can be discovered and selected through `repo-manager init`; they do not need to appear in a static template.

## High-level architecture

```text
                         +----------------------------+
                         |        CLI interface       |
                         | command flags / menus      |
                         +-------------+--------------+
                                       |
             +-------------------------+-------------------------+
             |                                                   |
 +-----------v------------+                         +------------v-----------+
 | Project configuration  |                         | Operation coordinator  |
 | profiles and cursors   |                         | policy and selection   |
 +-----------+------------+                         +------------+-----------+
             |                                                   |
             +-------------------------+-------------------------+
                                       |
                         +-------------v--------------+
                         | Repository service          |
                         | discovery and state model   |
                         +-------------+--------------+
                                       |
                         +-------------v--------------+
                         | Git backend                 |
                         | explicit git subprocesses   |
                         +-------------+--------------+
                                       |
                         +-------------v--------------+
                         | Local Git repositories      |
                         +----------------------------+

 Optional summary adapters:
 GitHub CLI or API -> pull request metadata
 LLM adapter       -> explanation from selected facts
```

### Components

| Component | Responsibilities |
| --- | --- |
| CLI interface | Parses commands and flags, runs menus, renders tables, returns exit codes. |
| Configuration service | Loads profiles, validates paths, resolves repository groups, saves user choices. |
| Discovery service | Finds Git worktrees and applies include, exclude, and selection rules. |
| Git backend | Runs small, explicit Git commands and converts output into typed results. |
| Repository service | Calculates branch, remote, cleanliness, upstream, ahead/behind, and default-branch state. |
| Policy engine | Decides whether a requested operation may proceed, skip, require confirmation, or fail. |
| Operation coordinator | Executes fetch, update, sync, checkout, stash, and reset workflows repository by repository. |
| Summary service | Builds deterministic commit/file summaries and maintains review watermarks. |
| Optional adapters | Enrich summaries with pull requests or LLM explanations. |

## Installation and profile storage

The tool should be distributed as a Python package and installed globally with `pipx`:

```bash
pipx install repo-manager
```

The global application directory should follow the operating system's configuration conventions. On macOS and Linux, the preferred location is:

```text
~/.config/repo-manager/
├── config.toml                 # global settings and active project
├── projects/
│   └── services.toml
└── state/
    └── services.toml
```

The profile stores user-maintained workspace selection and policy. The state file stores mutable review watermarks and should not normally be committed. A project-local `.repo-manager.toml` may optionally be loaded as a shareable overlay, but must not contain personal access tokens, credentials, or mutable state.

## Initialization flow

```bash
repo-manager init /path/to/workspace --name my-project
```

`init` performs no Git mutation. It should:

1. Canonicalize and validate the workspace root.
2. Discover nested Git worktrees within the configured maximum depth.
3. Read each repository's path, origin URL, current branch, and candidate default branch.
4. Show an interactive selectable table.
5. Let the user include or exclude repositories.
6. Let the user name repository groups and apply default-branch overrides.
7. Preview the generated profile.
8. Save the profile only after confirmation.

Default-branch inference should use this ordered evidence:

1. A profile override already supplied by the user.
2. `refs/remotes/<remote>/HEAD` if available.
3. The current branch's remote tracking branch when it is a common default candidate.
4. Existing local or remote `main`, then `master`, then `dev` branches.
5. An `ambiguous` result that requires user selection.

The CLI must show the inference source so users can distinguish a proven default branch from a heuristic.

## Command contract

### Status

```bash
repo-manager status
repo-manager status --project my-project
repo-manager status --group services
repo-manager status --fetch
repo-manager status --json
```

`status` is read-only unless `--fetch` is passed. The default report includes:

- Repository name and relative path.
- Current branch or detached HEAD state.
- Configured and inferred default branch.
- HEAD commit and last commit subject/date.
- Origin URL with credentials redacted.
- Upstream tracking branch.
- Dirty state, including staged, unstaged, and untracked counts.
- Ahead/behind counts when remote references are available.
- A status classification such as `ready`, `dirty`, `detached`, `no-upstream`, `diverged`, or `unknown-remote-state`.

`--fetch` runs `git fetch --prune <remote>` per selected repository before calculating remote state. Fetch changes remote-tracking references but does not change the worktree.

### Update

```bash
repo-manager update
repo-manager update --repo payments-api
repo-manager update --group libraries
repo-manager update --dry-run
repo-manager update --stash-and-update
repo-manager update --discard --repo payments-api --yes
```

`update` fetches every repository in the selected profile before showing the interactive selection. It updates the current branch only and never switches branches.

Normal update sequence for an eligible repository:

```text
git fetch --prune <remote>
git pull --ff-only
```

Eligibility for the default update path:

- Worktree is clean.
- A non-detached branch is checked out.
- The branch has an upstream.
- The local and upstream histories are not divergent.
- Fast-forward is possible or the branch is already current.

Repositories that do not meet these conditions are skipped and reported. The command continues with other selected repositories and returns a nonzero exit code if any requested operation failed or was safety-skipped.

### Stash and update

```bash
repo-manager update --stash-and-update --group services
```

This mode is opt-in. For a dirty selected repository, it should:

1. Create a uniquely identifiable stash including untracked files.
2. Update using the normal fast-forward-only path.
3. Restore the stash.
4. Report whether the restore was clean, conflicted, or failed.

The tool must not delete the stash if restoring it produces conflicts. It should print the stash reference and the Git commands needed to inspect or recover it.

### Discard and update

```bash
repo-manager update --discard --repo payments-api --yes
```

This is intentionally destructive. It discards local tracked and untracked work only for explicitly selected repositories and aligns the current branch to its configured upstream. It must require:

- At least one explicit repository or group selection, never an implicit all-repository target.
- `--yes` in non-interactive mode.
- An interactive typed confirmation that repeats the selected repository paths in menu mode.
- A displayed preview of affected paths and the remote branch before execution.

The exact reset and cleanup behavior must be documented in the CLI help before this command ships. The first version should keep `--discard` narrow: reset tracked changes and remove untracked files only when `--include-untracked` is separately specified.

### Sync default branches

```bash
repo-manager sync
repo-manager sync --group services
repo-manager sync --repo platform --dry-run
repo-manager sync --stash-and-update
```

`sync` makes the default branch the active branch, then fast-forwards it to the configured remote. It is useful after feature work when the user wants a current, consistent workspace baseline.

Normal sync sequence:

```text
git fetch --prune <remote>
git switch <default-branch>
git pull --ff-only
```

By default, a dirty worktree, missing default branch, untracked default branch, divergent default branch, or checkout conflict causes a skip. `--stash-and-update` may be used only when the user accepts the stash lifecycle described above.

### Checkout

```bash
repo-manager checkout
repo-manager checkout --repo payments-api --branch feature/example
repo-manager checkout --group libraries --branch dev
repo-manager checkout --branch default
```

Interactive checkout should fetch branch metadata, select repositories, then offer local branches and remote branches. If only a remote branch exists, it creates a local tracking branch. `--branch default` resolves to each selected repository's configured default branch.

The default policy refuses checkout when local changes would be overwritten. It must not create a branch merely because the same string exists in another repository.

### Summaries

```bash
repo-manager summary --since last
repo-manager summary --since 7d
repo-manager summary --group services --markdown report.md
repo-manager summary --since last --prs
repo-manager summary --since last --explain=metadata
```

Summaries are read-only. `--since last` resolves each repository's individual review watermark. `--since <duration>` and `--since <date>` select commits by author/commit date using an explicitly documented date rule.

## Interactive mode

```bash
repo-manager
repo-manager interactive
```

The initial menu should be intentionally small:

```text
Project: services

1. Show repository status
2. Update selected repositories
3. Sync selected repositories to default branches
4. Checkout a branch
5. Summarize recent changes
6. Configure project
7. Exit
```

Repository selection should support individual choices and expressions:

```text
all
clean
dirty
group:services
group:libraries
search:payments
```

Before a mutation, interactive mode must show a plan table with operation, repository, branch, safety classification, and expected outcome. The final confirmation is per batch, not one prompt per repository, unless a destructive mode is selected.

## Repository state model

The domain model should separate raw Git observations from policy decisions.

```text
RepositorySnapshot
├── identity
│   ├── name
│   ├── absolute_path
│   ├── relative_path
│   ├── groups
│   └── origin_url_redacted
├── checkout
│   ├── head_sha
│   ├── current_branch | detached_sha
│   ├── upstream_branch
│   └── default_branch and inference_source
├── worktree
│   ├── staged_count
│   ├── unstaged_count
│   ├── untracked_count
│   └── is_dirty
├── remote
│   ├── fetch_attempted
│   ├── fetch_result
│   ├── ahead_count
│   ├── behind_count
│   └── relationship
└── classification
    ├── ready
    ├── current
    ├── dirty
    ├── detached
    ├── no_upstream
    ├── default_branch_unknown
    ├── diverged
    └── remote_unavailable
```

`relationship` is one of `current`, `ahead`, `behind`, `diverged`, or `unknown`. `unknown` is required when no current remote reference can be obtained; the tool must not substitute an old remote state as if it were current.

## Safety policy

| Repository condition | Status | Default update | Sync | Checkout | Discard |
| --- | --- | --- | --- | --- | --- |
| Clean, tracking, behind only | ready | fast-forward | fast-forward default | allowed | not needed |
| Clean, current | current | no-op | no-op after branch switch | allowed | not needed |
| Dirty | dirty | skip | skip | refuse if overwrite risk | explicit only |
| Detached HEAD | detached | skip | skip | allowed only explicitly | explicit only |
| No upstream | no-upstream | skip | skip | allowed | not implied |
| Ahead only | ahead | skip | skip default only if default is clean and not divergent | allowed | explicit only |
| Diverged | diverged | skip | skip | allowed | explicit only |
| Remote unavailable | remote-unavailable | skip | skip | local-only branch operations allowed | never implied |

The policy layer must produce a human-readable reason and a suggested action. For example:

```text
SKIPPED  services/payments-api
Reason: 4 local changes would be affected by sync to dev.
Next: inspect changes, use --stash-and-update, or explicitly use --discard.
```

## Git backend contract

The Git backend is the only component that runs subprocesses. It should expose typed methods, use a repository path as an argument rather than changing the process working directory globally, and record redacted command results.

Representative read methods:

```text
discover_worktrees(root, max_depth)
get_head(repo)
get_current_branch(repo)
get_status_porcelain(repo)
get_upstream(repo, branch)
get_default_branch(repo, remote)
get_ahead_behind(repo, upstream)
get_last_commit(repo)
list_local_branches(repo)
list_remote_branches(repo, remote)
```

Representative mutation methods:

```text
fetch(repo, remote, prune=true)
pull_fast_forward_only(repo)
switch(repo, branch)
create_tracking_branch(repo, branch, remote_branch)
stash_push(repo, include_untracked=true, message)
stash_pop(repo, stash_ref)
reset_to_upstream(repo, include_untracked=false)
```

The first implementation should prefer stable Git plumbing or porcelain output designed for scripts, such as `git status --porcelain=v2 -z`, and avoid parsing terminal-oriented output.

## Configuration schema

Project configuration is TOML. TOML is readable, supports comments, ships in Python's standard library for parsing, and avoids a mandatory YAML dependency.

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

[policy]
pull_mode = "ff-only"
skip_dirty = true
fetch_before_update = true
fetch_before_status = false
allow_discard_untracked = false

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

[groups]
all-agents = ["services"]
all-services = ["platform", "services", "libraries"]
```

Configuration validation must reject repository paths outside the workspace root unless the user intentionally initializes a profile with multiple roots in a future version.

## Summary architecture

### Deterministic summary

The deterministic summary is always available and must remain useful without GitHub access or an LLM. For each repository and commit range, collect:

- Start and end revisions.
- Commit SHA, subject, author, and timestamp.
- Files changed per commit and across the range.
- Insertions and deletions.
- Top-level directories affected.
- Conventional commit type, issue key, or ticket reference if parseable.

The formatter should produce terminal, JSON, and Markdown outputs from the same structured report.

```text
SummaryReport
├── generated_at
├── scope
├── repositories[]
│   ├── repository identity
│   ├── requested range and resolved range
│   ├── commits[]
│   ├── file_statistics
│   ├── detected_references
│   └── warnings
└── cross_repository_totals
```

### Incremental watermarks

Each project state file stores a watermark by canonical repository path and branch:

```toml
[watermarks."services/payments-api".dev]
last_summary_head = "abc123..."
last_summary_at = "2026-08-06T10:00:00Z"
```

The watermark advances only after a summary completes successfully and the user accepts it as reviewed. A `--no-advance-watermark` flag supports previews and automation.

If a stored commit is no longer reachable because of a rebase or history rewrite, the tool should report that the baseline is unavailable, use a configured date fallback only with user confirmation, and avoid silently omitting changes.

### Pull-request enrichment

`--prs` is an optional adapter. It should use authenticated GitHub CLI or GitHub API access if present and otherwise produce the deterministic summary with an explicit warning.

Pull-request records may include:

- Number, title, URL, author, state, and merged date.
- Base and head branches.
- Merge commit or included commit IDs.
- Labels and linked issue references.
- Body text only when explicitly requested for output or LLM explanation.

Commit-to-PR association is inherently imperfect for squash merges, rebases, cherry-picks, and cross-repository releases. The report must label unmatched commits and avoid claiming a complete PR mapping where the host cannot provide one.

### LLM explanation mode

```bash
repo-manager summary --since last --explain=metadata
repo-manager summary --since last --explain=diffs
```

`--explain=metadata` sends only selected structured facts such as repository paths, branch names, commit subjects, PR titles, changed-file paths, and diff statistics. This is the initial recommended mode.

`--explain=diffs` may include selected patches or source excerpts and must require a separate explicit acknowledgement that private code can leave the local environment.

The prompt must instruct the model to:

- Distinguish facts supplied in the report from interpretation.
- Avoid inventing implementation details not supported by commits, PRs, or diffs.
- Identify uncertainty and cross-repository dependencies.
- Produce a concise context-preserving summary, risks, and suggested follow-up questions.

LLM results are advisory and must include an input-mode label. A failed or unavailable LLM request cannot invalidate the deterministic summary.

## Output formats and exit codes

### Output formats

- Default terminal table and grouped operation log.
- `--json` for automation and external tooling.
- `--markdown <path>` for review notes and handoff documents.
- `--quiet` for scripts that only need exit status.

All output must avoid exposing credentials from remote URLs or environment variables.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | All requested operations completed or were already current. |
| 1 | One or more requested repositories failed. |
| 2 | Invalid command, configuration, or user selection. |
| 3 | One or more requested operations were skipped for a safety condition. |
| 4 | Required optional integration was requested but unavailable. |

When failures and skips both occur, the highest-severity applicable exit code should be returned and every per-repository result must remain in the output.

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
│   ├── selection.py
│   ├── summaries.py
│   ├── watermarks.py
│   ├── output.py
│   └── integrations/
│       ├── github.py
│       └── llm.py
└── tests/
    ├── fixtures/
    ├── test_discovery.py
    ├── test_policy.py
    ├── test_operations.py
    ├── test_summaries.py
    └── test_cli.py
```

Suggested dependencies:

- Python 3.11 or later.
- Typer for command parsing.
- Rich for interactive menus and terminal tables.
- `tomllib` for reading TOML and a small TOML writer dependency for writes.
- Standard-library `subprocess` for Git execution.
- Optional adapters for GitHub CLI and an OpenAI-compatible LLM endpoint.

## Validation strategy

The CLI must be validated independently from Codex and independently from real user repositories.

### Fixture repository matrix

Create temporary local bare remotes and cloned fixture worktrees representing:

- Clean repository already current.
- Clean repository behind upstream.
- Repository with staged, unstaged, and untracked changes.
- Repository ahead of upstream.
- Repository divergent from upstream.
- Detached HEAD.
- Repository with no upstream branch.
- Repository with ambiguous default branch.
- Failed fetch because the remote is unavailable.
- Stash restoration that succeeds.
- Stash restoration that conflicts.

### Required tests

1. Discovery finds only intended worktrees and honors exclusions.
2. Init saves a valid profile and records default-branch inference evidence.
3. Status identifies every fixture condition accurately.
4. Normal update mutates only eligible clean, behind repositories.
5. Sync switches only eligible repositories to configured defaults and fast-forwards them.
6. Dry-run runs no mutation command.
7. Stash mode preserves local changes or reports an actionable conflict.
8. Discard requires explicit scope and confirmation flags.
9. Summary ranges include expected commits and watermark behavior is correct.
10. GitHub and LLM adapter failures leave deterministic output intact.
11. JSON output has stable, documented fields for automation.

### Manual acceptance checks

Before global installation, use a disposable multi-repository test workspace and verify:

```bash
repo-manager init /tmp/repo-manager-fixture --name fixture
repo-manager status --project fixture --json
repo-manager update --project fixture --dry-run
repo-manager sync --project fixture --dry-run
repo-manager summary --project fixture --since last --markdown /tmp/summary.md
```

No test should run `--discard` against a real workspace.

## Delivery phases

### Phase 1: Foundation

- Package scaffold and global installation path.
- Profile initialization and discovery.
- Repository state model and status output.
- Default-branch inference with user overrides.
- Fixture-based test harness.

### Phase 2: Safe Git operations

- Fetch, default update, sync, checkout, dry-run, and operation logs.
- Interactive repository selection.
- Safety classification and clear skip reporting.

### Phase 3: Recovery modes

- Stash-and-update behavior and conflict reporting.
- Narrow, explicit discard behavior.
- Strong confirmation UX and tests for destructive paths.

### Phase 4: Change intelligence

- Commit-level summaries and review watermarks.
- Markdown and JSON reports.
- Optional GitHub PR enrichment.
- Optional metadata-only LLM explanations.

### Phase 5: Codex skill

After the CLI has independent safety and behavior validation, create a thin Codex skill that invokes `repo-manager`, interprets results, and helps write review summaries. The skill must not reimplement Git policy or bypass CLI confirmations.

## Trade-offs and decisions

| Decision | Chosen approach | Reason |
| --- | --- | --- |
| Installation | Global Python package via `pipx` | Reusable across projects without coupling to one workspace. |
| Configuration | Global TOML profiles with optional local overlay | User-friendly and Python-native parsing. |
| Discovery | Recursive with exclusions, then user-selected inclusion | Works generically while avoiding accidental scope. |
| Normal update | Fetch then `pull --ff-only` on current branch | Preserves history and avoids implicit merge/rebase decisions. |
| Default branch | Infer during init, persist override | Convenient setup with predictable later behavior. |
| Dirty worktrees | Skip by default | Local work is valuable context and must not be changed implicitly. |
| Stash | Explicit opt-in | Useful convenience with visible recovery behavior. |
| Discard | Explicit scoped, confirmed operation | Destructive behavior requires narrow authority. |
| Summaries | Deterministic first, optional enrichments | Preserves usefulness and accuracy without external dependencies. |
| LLM input | Metadata-only by default | Provides context while reducing private-source exposure. |
| Codex integration | After CLI validation | Keeps core behavior portable and independently testable. |

## Future considerations

- Multiple workspace roots in a single profile.
- Read-only support for Git worktrees managed through a shared `.git` directory.
- GitLab, Bitbucket, and other pull-request providers.
- Release and deployment metadata correlations.
- Scheduled read-only daily or weekly change briefs.
- Team-shareable profiles with local policy overrides.
- Optional TUI dashboard after command-line workflows prove stable.

## Implementation readiness checklist

The design is ready for implementation when the following are confirmed:

- [x] The tool is globally installed and supports multiple project profiles.
- [x] Initialization discovers repositories and lets users choose configuration.
- [x] Default behavior skips unsafe worktrees.
- [x] `--stash-and-update`, `--discard`, and `sync` are explicit modes.
- [x] Update fetches selected profile repositories and supports interactive selection.
- [x] Default branches are inferred during initialization and stored in config.
- [x] Change summaries support commit-level incremental reporting and optional PR/LLM enrichment.
- [x] The CLI is validated independently before any Codex skill is introduced.
- [ ] Exact discard semantics and confirmation wording are finalized during implementation planning.
- [ ] LLM provider configuration and source-data consent language are finalized before enabling diff-based explanations.
