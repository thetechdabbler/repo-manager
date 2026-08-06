# repo-manager

A command-line utility for working safely with a directory tree that contains many
independent Git repositories.

It answers three questions and performs two mutations:

- Which repositories are present, on what branch, in what state, and which have local work?
- Which repositories can be updated safely right now, and why can the rest not?
- What changed across the workspace in the last N days?
- Update the safe repositories on their current branch.
- Return the safe repositories to their default branch.

See `repo-manager-architecture-final.md` for the full design and phase plan.

## Status

Phase 1 (See) in progress.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```
