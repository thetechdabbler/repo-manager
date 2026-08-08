# Commands

`repo-manager` has a small, explicit command set. Read-only commands never change a
repository. Mutating commands change only the repositories you select, and only when
they are safe to touch.

| Command | What it does | Changes repos? |
| --- | --- | --- |
| [`init`](init.md) | Scan a workspace and save a profile | No |
| [`status`](status.md) | Report the state of every repository | No |
| [`update`](update.md) | Fast-forward the current branch | Yes |
| [`switch-default`](switch-default.md) | Switch to the default branch and fast-forward | Yes |
| [`checkout`](checkout.md) | Check out a branch across repositories | Yes |
| [`summary`](summary.md) | Summarize recent commits | No |
| [`forget`](forget.md) | Remove a saved profile (config only) | No |
| [Utility](utility.md) | `profiles`, `version`, `help` | No |

## Selecting repositories

Every command that operates on repositories accepts the same selectors:

- `--repo <name>` limits to a single repository.
- `--group <name>` limits to a configured group.
- `--select <expr>` filters by a comma-separated union of tokens: `all`, `clean`, a
  state name (`ready`, `dirty`, `ahead`, ...), `group:NAME`, `search:TEXT`,
  `name:NAME`, or a bare name or path.

With no selector, a command targets every repository in the active profile.

## Exit codes

Mutating commands return the highest-severity applicable code, and every per-repository
result stays in the output regardless.

--8<-- "reference/_generated/exit-codes.md"
