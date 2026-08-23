"""Policy matrix for Git subprocess credentials."""

from __future__ import annotations

import pytest

from apm_cli.core.auth import AuthContext, AuthResolver, HostInfo

_PLATFORM_TOKENS = {
    "ADO_APM_PAT": "ado-sentinel",
    "GH_TOKEN": "gh-sentinel",
    "GITHUB_APM_PAT": "github-sentinel",
    "GITHUB_TOKEN": "actions-sentinel",
    "GIT_TOKEN": "git-sentinel",
    "GIT_HTTP_EXTRAHEADER": "Authorization: sentinel",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "http.extraheader",
    "GIT_CONFIG_VALUE_0": "Authorization: sentinel",
}


class _TokenManager:
    """Provide a deterministic hardened base without resolving credentials."""

    def setup_environment(self) -> dict[str, str]:
        return {
            **_PLATFORM_TOKENS,
            "GIT_ASKPASS": "echo",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_SSH_COMMAND": "ssh -o ConnectTimeout=30 -o BatchMode=yes",
        }


class _RecordingTokenManager(_TokenManager):
    """Record native helper lookups without exposing fixture credentials."""

    def __init__(self) -> None:
        self.credential_envs: list[dict[str, str]] = []

    def get_token_for_purpose(self, _purpose: str) -> None:
        return None

    def resolve_credential_from_gh_cli(self, _host: str) -> None:
        return None

    def resolve_credential_from_git(
        self,
        _host: str,
        port: int | None = None,
        path: str | None = None,
        *,
        env: dict[str, str] | None = None,
    ) -> None:
        assert port is None
        assert path is None
        assert env is not None
        self.credential_envs.append(env)


def _context(kind: str) -> AuthContext:
    return AuthContext(
        token=None,
        source="none",
        token_type="unknown",
        host_info=HostInfo(
            host=f"{kind}.example.test",
            kind=kind,
            has_public_repos=True,
            api_base="https://example.test/api",
        ),
        git_env={},
    )


@pytest.mark.parametrize(
    ("kind", "remote_url", "expects_helper", "expects_isolation"),
    [
        ("generic", "https://gitea.example.test/org/repo.git", True, False),
        ("generic", "http://gitea.example.test/org/repo.git", False, True),
        ("generic", "git@gitea.example.test:org/repo.git", True, False),
        ("github", "https://github.com/org/repo.git", False, True),
        ("gitlab", "https://gitlab.com/org/repo.git", False, True),
        ("ado", "https://dev.azure.com/org/project/_git/repo", False, True),
    ],
)
def test_git_transport_policy_matrix(
    kind: str,
    remote_url: str,
    expects_helper: bool,
    expects_isolation: bool,
) -> None:
    """Only generic HTTPS and SSH retain native Git credential configuration."""
    env = AuthResolver(token_manager=_TokenManager()).git_env_for_remote(_context(kind), remote_url)

    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_TOKEN" not in env
    assert "GIT_HTTP_EXTRAHEADER" not in env
    assert "GITHUB_TOKEN" not in env
    assert "GITHUB_APM_PAT" not in env
    assert "GH_TOKEN" not in env
    assert "ADO_APM_PAT" not in env
    assert "GIT_CONFIG_PARAMETERS" not in env

    if expects_helper:
        assert "GIT_ASKPASS" not in env
        assert "GIT_CONFIG_GLOBAL" not in env
        assert "GIT_CONFIG_NOSYSTEM" not in env
    else:
        assert env["GIT_ASKPASS"] == "echo"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"

    if kind == "generic" and expects_isolation:
        assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
        assert env["GIT_CONFIG_VALUE_0"] == ""
    if remote_url.startswith("git@"):
        assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
        assert "ConnectTimeout=30" in env["GIT_SSH_COMMAND"]


@pytest.mark.parametrize(
    ("remote_url", "expected_lookups"),
    [
        ("https://gitea.example.test/org/repo.git", 0),
        ("http://gitea.example.test/org/repo.git", 0),
        ("git@gitea.example.test:org/repo.git", 0),
    ],
)
def test_generic_remote_policy_controls_native_helper_lookup(
    remote_url: str, expected_lookups: int
) -> None:
    """Remote resolution leaves native helper invocation to Git itself."""
    token_manager = _RecordingTokenManager()
    resolver = AuthResolver(token_manager=token_manager)

    resolver.resolve_for_remote("gitea.example.test", remote_url)

    assert len(token_manager.credential_envs) == expected_lookups
    if token_manager.credential_envs:
        lookup_env = token_manager.credential_envs[0]
        assert set(_PLATFORM_TOKENS).isdisjoint(lookup_env)
        assert lookup_env["GIT_TERMINAL_PROMPT"] == "0"
