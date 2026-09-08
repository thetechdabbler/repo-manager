# Updating

Two commands change branch state. Both fetch in parallel first, then mutate serially
and locally, so they never block on the network mid-operation. Both are safe by
default: only eligible repositories are touched, and everything else is skipped with
a reason.

## `update` — fast-forward the current branch

`update` fast-forwards the branch each repository is already on. It never switches
branches.

```bash
repo <project> update --dry-run     # preview the plan, change nothing
repo <project> update --yes         # execute
repo <project> update --group libraries --yes
```

Only `ready` repositories (clean, tracking, behind) are updated. See the
[policy table](../concepts/safety-model.md#policy-by-operation) for what happens in
every other state.

## `default` — return to the default branch

`default` switches each repository to its configured default branch and
fast-forwards it. Useful for returning a whole workspace to a clean baseline after
feature work.

```bash
repo <project> default --dry-run
repo <project> default --select clean --yes
```

If the default branch itself has diverged from its upstream, the repository is
reported as failed rather than silently merged.

## Previewing and confirming

- `--dry-run` runs no mutation command. It prints exactly what would happen.
- Interactively, a plan table is shown and a single per-batch confirmation is asked.
- Non-interactively, you must pass `--yes` (or `--dry-run`). The tool refuses to
  mutate silently in a script without explicit consent.

## Selecting repositories

`--group`, `--repo`, and `--select` all apply, exactly as in
[status](daily-status.md#narrowing-the-view).

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Everything completed or was already current |
| 1 | One or more repositories failed |
| 3 | One or more repositories were safety-skipped |

Exit 3 is intentional: a routine dirty-repo skip makes the whole run non-zero so it
cannot be ignored in automation. Pass `--ignore-skips` to downgrade a skip-only run
to 0 for shell prompts and non-blocking scripts.
