"""Guards on the git invocation flags and network environment.

These lock in the fetch-auth fix: the credential helper must stay enabled so HTTPS
remotes can authenticate non-interactively, while interactive prompts stay disabled so
a fetch can never hang.
"""

from __future__ import annotations

from repo_manager.git_backend import _BASE_FLAGS, _NETWORK_ENV, redact_url


def _flag_pairs(flags):
    return [flags[i + 1] for i, f in enumerate(flags) if f == "-c"]


def test_credential_helper_is_not_disabled():
    settings = _flag_pairs(_BASE_FLAGS)
    # The over-reach that broke HTTPS fetches must be gone.
    assert "credential.helper=" not in settings
    # But interactive credential prompting stays off (no hang).
    assert "credential.interactive=never" in settings


def test_network_env_prevents_hangs_without_breaking_auth():
    # No interactive terminal prompt, and SSH runs in batch mode.
    assert _NETWORK_ENV["GIT_TERMINAL_PROMPT"] == "0"
    assert "BatchMode=yes" in _NETWORK_ENV["GIT_SSH_COMMAND"]
    # Empty askpass overrides (which can themselves break auth) must not be set.
    assert "GIT_ASKPASS" not in _NETWORK_ENV
    assert "SSH_ASKPASS" not in _NETWORK_ENV


def test_redact_url_strips_credentials():
    assert redact_url("https://user:token@github.com/x/y.git") == (
        "https://***@github.com/x/y.git"
    )
    assert redact_url("git@github.com:x/y.git") == "git@github.com:x/y.git"
    assert redact_url(None) is None
