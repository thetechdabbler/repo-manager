# status

Report the state of every selected repository. Read-only unless `--fetch` is passed.

## What it does

`status` builds a snapshot of each selected repository: current branch (or detached
HEAD), upstream, staged / unstaged / untracked counts, ahead and behind counts, and
any in-progress operation. It reduces each to one [state](../concepts/safety-model.md)
and renders a table.

Without `--fetch` it does not contact any remote, and the ahead/behind column reads
`no-fetch` rather than showing possibly-stale numbers. With `--fetch`, remotes are
fetched in parallel first (each with a timeout), so an unreachable or credential-gated
remote becomes `remote-unavailable` instead of hanging the run. Stale remote-tracking
refs are never presented as if they were current.

```mermaid
flowchart TD
  A["repo-manager status"] --> B["Resolve profile + select repos"]
  B --> C{"--fetch ?"}
  C -- "yes" --> D["git fetch --prune<br/>parallel, with timeout"]
  C -- "no" --> E["Use local refs<br/>ahead/behind = no-fetch"]
  D --> F["Snapshot each repo<br/>branch, dirty, ahead/behind, in-progress"]
  E --> F
  F --> G["Classify into one state"]
  G --> H["Render table or JSON"]
```

## How the options change it

- **`--fetch`** updates remote-tracking refs first so ahead/behind counts are real.
  This is the only thing that makes `status` touch the network.
- **`--group` / `--repo` / `--select`** narrow which repositories are reported. See
  [selecting repositories](index.md#selecting-repositories).
- **`--json`** emits the full, versioned `StatusReport` structure instead of a table.
  This is the contract for scripts.
- **`--jobs`** sets how many repositories are read and fetched in parallel.

## Reading the table

| Column | Meaning |
| --- | --- |
| Branch | Current branch, or `detached@<sha>` |
| State | One of the [repository states](../concepts/safety-model.md) |
| ↑ / ↓ | Commits ahead / behind upstream, or `no-fetch` / `unreachable` |
| Changes | `Ns` staged, `Nm` modified, `N?` untracked, `Nu` unmerged |
| Last commit | Short SHA and subject |

## Options

--8<-- "reference/_generated/status.md"

## Examples

```bash
repo-manager status                    # local-only, fast
repo-manager status --fetch            # real ahead/behind
repo-manager status --group backend    # one group
repo-manager status --select dirty     # only repos with local work
repo-manager status --json             # machine-readable
```
