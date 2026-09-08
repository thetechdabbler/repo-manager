# repo

A command-line utility for working safely with a directory tree that contains many
independent Git repositories.

It answers three questions and performs three mutations:

- Which repositories are present, on what branch, in what state, and which have local work?
- Which repositories can be updated safely right now, and why can the rest not?
- What changed across the workspace in the last N days?
- Update the safe repositories on their current branch.
- Sync the remote default branch into the current branch.
- Return the safe repositories to their default branch.

See the [documentation site](https://thetechdabbler.github.io/repo-manager/) for
installation, command reference, and guides.

## Install

Installed from this repository with [pipx](https://pipx.pypa.io/):

```bash
pipx install "git+https://github.com/thetechdabbler/repo-manager.git"
```

Not published to PyPI: `pipx install repo-manager` would fetch an unrelated,
abandoned project of the same name. Always install from the Git URL above.

Requires Python 3.11+ and Git on your `PATH`.

The installed command is `repo`:

```bash
repo status
repo services status
repo services sync --rebase
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```
