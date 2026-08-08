# Contributing

## Development setup

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## The fixture matrix

The test suite is built fixture-first. `tests/conftest.py` constructs a workspace of
independent repositories, each in one specific state (clean-behind, dirty, detached,
diverged, mid-rebase, unreachable remote, and so on) from local bare remotes, with a
neutralized git environment so a contributor's own git config cannot change the
outcome.

The `MATRIX` in that file is the single source of truth for expected state. The state
model, the policy engine, and the CLI are all asserted against it. When you add a
state or change a classification, update the matrix first; it is the specification.

## Generated docs

Some docs are generated from the code and must not be edited by hand:

- `docs/reference/_generated/*.md`, the per-command synopsis and option tables,
  embedded into the command pages via snippets
- `docs/concepts/safety-model.md`, from the state enum and the policy functions

The hand-written command pages under `docs/reference/` (narrative, diagrams, examples)
are authored; only the embedded `_generated` partials come from the CLI.

Regenerate and validate them with:

```bash
make docs          # regenerate
make docs-check    # fail if committed copies are stale (also run in CI)
```

The policy table in the safety model is produced by running the real decision
functions over a representative snapshot per state, so the documentation cannot drift
from the tested behavior.

## Before opening a pull request

```bash
make test
make docs-check
```

## Design

The architecture and phase plan live in `repo-manager-architecture-final.md` at the
repository root. It records the rationale behind the safety model, the concurrency
choices, and what is deliberately deferred.
