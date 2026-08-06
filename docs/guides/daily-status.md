# Daily status

`status` is the command you run most. It is read-only.

```bash
repo-manager status
```

Without a network call it reports each repository's branch, cleanliness, and last
commit. The ahead/behind column shows `no-fetch`, because the tool refuses to present
stale remote-tracking references as if they were current.

## Getting real ahead/behind numbers

Pass `--fetch` to update remote-tracking refs first:

```bash
repo-manager status --fetch
```

Fetches run in parallel (see `--jobs`) and each has a timeout, so an unreachable or
credential-gated remote reports `remote-unavailable` rather than hanging the run.

## Narrowing the view

```bash
repo-manager status --group backend        # one group
repo-manager status --repo api             # one repository
repo-manager status --select dirty         # only repos with local work
```

`--select` accepts a comma-separated union of tokens: `all`, `clean`, any state name
(`dirty`, `ready`, `ahead`, ...), `group:NAME`, `search:TEXT`, and `name:NAME`.

## Machine-readable output

```bash
repo-manager status --json
```

The JSON is a stable, versioned structure (`schema_version`). It is the contract for
scripts and for any tooling built on top of the CLI; the terminal table is just one
rendering of the same report.

## Reading the columns

| Column | Meaning |
| --- | --- |
| Branch | Current branch, or `detached@<sha>` |
| State | One of the [repository states](../concepts/safety-model.md) |
| ↑/↓ | Commits ahead / behind upstream, or `no-fetch` / `unreachable` |
| Changes | `Ns` staged, `Nm` modified, `N?` untracked, `Nu` unmerged |
| Last commit | Short SHA and subject |
