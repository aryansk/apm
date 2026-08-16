import json

import pytest

from apm_cli.adapters.client.intellij import _strip_jsonc_comments
from apm_cli.commands.uninstall.engine import _remove_stale_mcp_from_recorded_targets
from apm_cli.integration.mcp_integrator import MCPIntegrator


class _Lockfile:
    mcp_target_servers = {
        "claude": ["server-a"],
        "cursor": ["server-b"],
    }


def test_jsonc_cleanup_accepts_comments_without_corrupting_urls():
    raw = """{
      // managed servers
      "servers": {
        "docs": {"url": "https://example.com/mcp"} /* keep URL intact */
      }
    }"""

    data = json.loads(_strip_jsonc_comments(raw))

    assert data["servers"]["docs"]["url"] == "https://example.com/mcp"


def test_cleanup_only_touches_recorded_runtime_owners(monkeypatch):
    calls = []

    def fake_remove(stale, **kwargs):
        calls.append((set(stale), kwargs.get("runtime")))

    monkeypatch.setattr(MCPIntegrator, "remove_stale", fake_remove)

    _remove_stale_mcp_from_recorded_targets(
        {"server-a"},
        _Lockfile(),
        project_root=None,
        user_scope=False,
        scope=None,
    )

    assert calls == [({"server-a"}, "claude")]


def test_cleanup_continues_other_targets_before_reporting_failure(monkeypatch):
    calls = []

    def fake_remove(stale, **kwargs):
        runtime = kwargs.get("runtime")
        calls.append(runtime)
        if runtime == "claude":
            raise OSError("broken config")

    monkeypatch.setattr(MCPIntegrator, "remove_stale", fake_remove)

    with pytest.raises(RuntimeError, match="claude"):
        _remove_stale_mcp_from_recorded_targets(
            {"server-a", "server-b"},
            _Lockfile(),
            project_root=None,
            user_scope=False,
            scope=None,
        )

    assert calls == ["claude", "cursor"]
