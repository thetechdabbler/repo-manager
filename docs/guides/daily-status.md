# Daily status

`status` is the command you run most. It is read-only.

There are two status views:

```bash
# All saved projects, local-only counts, no fetch.
repo status

# Detailed child-repository status for one project.
repo core status
```

The global view reports the project root, total child repositories, clean count, dirty
count, in-progress count, and missing-worktree count. It does not fetch. The detailed
view reports each saved repository name, relative path, current branch, state, local
changes, and commit information.

```bash
repo <project> status
```

Without a network call it reports each repository's branch, cleanliness, and last
commit. The ahead/behind column shows `no-fetch`, because the tool refuses to present
stale remote-tracking references as if they were current.

## Getting real ahead/behind numbers

Pass `--fetch` to update remote-tracking refs first:

```bash
repo <project> status --fetch
```

Fetches run in parallel (see `--jobs`) and each has a timeout, so an unreachable or
credential-gated remote reports `remote-unavailable` rather than hanging the run.

## Narrowing the view

```bash
repo <project> status --group backend        # one group
repo <project> status --repo api             # one repository
repo <project> status --select dirty         # only repos with local work
```

`--select` accepts a comma-separated union of tokens: `all`, `clean`, any state name
(`dirty`, `ready`, `ahead`, ...), `group:NAME`, `search:TEXT`, and `name:NAME`.

## Machine-readable output

```bash
repo <project> status --json
```

The JSON is a stable, versioned structure (`schema_version`). It is the contract for
scripts and for any tooling built on top of the CLI; the terminal table is just one
rendering of the same report.

For example, a project with one root repository and two child repositories may show:

```text
Project  Root                         Repos  Clean  Dirty  In progress  Missing
core     /Users/mchoudhary/docker-env      3      2      1            0        0
```

The detailed view then makes the paths explicit:

```text
Name                  Path                    Branch  State
root                  .                       main    dirty
Entrata               Entrata                 dev     current
LeaseManagement       LeaseManagement         main    current
```

## Reading the columns

| Column | Meaning |
| --- | --- |
| Branch | Current branch, or `detached@<sha>` |
| State | One of the [repository states](../concepts/safety-model.md) |
| ↑/↓ | Commits ahead / behind upstream, or `no-fetch` / `unreachable` |
| Changes | `Ns` staged, `Nm` modified, `N?` untracked, `Nu` unmerged |
| Last commit | Short SHA and subject |
