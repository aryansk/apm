"""Deployable source-plan contracts for install-time security scanning."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from apm_cli.install.deployable_source_plan import DeployableSourcePlan
from apm_cli.install.helpers.security_scan import _pre_deploy_security_scan
from apm_cli.security.gate import SecurityGate
from apm_cli.utils.diagnostics import DiagnosticCollector

pytestmark = pytest.mark.component


def _package(root: Path) -> SimpleNamespace:
    return SimpleNamespace(install_path=root)


def _skill_target() -> SimpleNamespace:
    return SimpleNamespace(primitives={"skills": object()})


def test_source_only_hidden_character_is_not_in_authorized_scan(tmp_path: Path) -> None:
    """A nested clean skill remains installable when source-only fixtures are hostile."""
    skill = tmp_path / "skills" / "clean"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("clean skill\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "fixture.txt").write_text("source \u202e fixture\n", encoding="utf-8")

    plan = DeployableSourcePlan.create(
        _package(tmp_path),
        [_skill_target()],
        skill_subset=None,
        hooks_approved=False,
        canvas_approved=False,
        skip_bin=True,
    )
    verdict = SecurityGate.scan_files(tmp_path, path_filter=plan.includes)

    assert verdict.should_block is False
    assert verdict.scanned_files == frozenset({"skills/clean/SKILL.md"})
    assert _pre_deploy_security_scan(plan, DiagnosticCollector(), package_name="clean") is True


def test_nested_deployable_hidden_character_blocks_without_force(tmp_path: Path) -> None:
    """A hostile selected skill is fail-closed while source-only files stay excluded."""
    skill = tmp_path / "skills" / "hostile"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("hostile \u202e skill\n", encoding="utf-8")

    plan = DeployableSourcePlan.create(
        _package(tmp_path),
        [_skill_target()],
        skill_subset=("hostile",),
        hooks_approved=False,
        canvas_approved=False,
        skip_bin=True,
    )
    verdict = SecurityGate.scan_files(tmp_path, path_filter=plan.includes)

    assert verdict.should_block is True
    assert verdict.scanned_files == frozenset({"skills/hostile/SKILL.md"})
    assert _pre_deploy_security_scan(plan, DiagnosticCollector(), package_name="hostile") is False
