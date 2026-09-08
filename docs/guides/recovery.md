# Recovering local work

By default, a dirty repository is skipped by `update` and `default` so your
local work is never touched. When you do want to update on top of local changes,
`--stash` is the opt-in, conflict-safe path.

```bash
repo <project> update --stash --group backend
repo <project> default --stash --repo api
```

For each dirty repository that is otherwise fast-forwardable, it:

1. Creates a uniquely named stash, including untracked files.
2. Fast-forwards using the normal safe path (`default` also switches branch first).
3. Restores the stash.
4. Reports whether the restore was clean or conflicted.

## When the restore is clean

The stash is applied and dropped, the repository shows as `updated`, and your local
changes sit on top of the freshly fast-forwarded branch. Nothing more to do.

## When the restore conflicts

The stash is **never dropped** on a conflict. The repository is reported as `failed`,
and the result carries the exact recovery commands:

```text
FAILED  services/payments-api
Error:  the operation succeeded but restoring your local changes conflicted
Next:   your changes are safe in a stash. Inspect with
        `git -C <path> stash list` (entry: 'repo-manager auto-stash: ...');
        re-apply after resolving with `git -C <path> stash pop stash@{0}`
```

The fast-forward still happened; your uncommitted work is preserved in the named stash
and can be recovered at any time. In `--json` output the same information is available
under each result's `stash` object (`stashed`, `reference`, `restore`).

## What is never stashed

Stashing only applies to a `dirty` repository whose branch can fast-forward. Anything
that stashing cannot make updatable is still skipped with a reason:

- A repository mid rebase, merge, cherry-pick, revert, or bisect (`in-progress`) is
  never touched, even with this flag.
- A `diverged` or `ahead` branch, where a fast-forward is impossible regardless of the
  working tree.
