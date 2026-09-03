from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from apm_cli.cli import cli
from apm_cli.commands.install import _prepare_dry_run_manifest_path
from apm_cli.core.command_logger import _ValidationOutcome


def test_global_dry_run_command_leaves_absent_home_state_uncreated(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_manifest = fake_home / ".apm" / "apm.yml"
    captured: dict[str, Any] = {}
    outcome = _ValidationOutcome(valid=[("test/pkg", False)], invalid=[])

    def fake_validate(
        packages: tuple[str, ...],
        dry_run: bool,
        *,
        manifest_path: Path,
        **_kwargs: object,
    ) -> tuple[list[str], _ValidationOutcome]:
        captured["validation_manifest_path"] = manifest_path
        assert packages == ("test/pkg",)
        assert dry_run is True
        return ["test/pkg"], outcome

    def fake_install(
        ctx: Any, validation_outcome: _ValidationOutcome
    ) -> tuple[int, int, int, None]:
        captured["install_manifest_path"] = ctx.manifest_path
        captured["install_manifest_display"] = ctx.manifest_display
        assert validation_outcome is outcome
        return 0, 0, 0, None

    with (
        patch.object(Path, "home", return_value=fake_home),
        patch("apm_cli.commands.install._validate_and_add_packages_to_apm_yml", fake_validate),
        patch("apm_cli.commands.install._install_apm_packages", fake_install),
    ):
        result = CliRunner().invoke(
            cli,
            ["install", "--dry-run", "-g", "test/pkg"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    assert captured["validation_manifest_path"] != user_manifest
    assert captured["install_manifest_path"] != user_manifest
    assert captured["install_manifest_display"] == str(user_manifest)
    assert not captured["validation_manifest_path"].parent.exists()
    assert not (fake_home / ".apm").exists()


def test_global_dry_run_redirects_absent_manifest_without_creating_user_state(
    tmp_path: Path,
) -> None:
    user_manifest = tmp_path / "home" / ".apm" / "apm.yml"

    preview_manifest, temp_dir = _prepare_dry_run_manifest_path(
        user_manifest,
        dry_run=True,
        user_scope=True,
        has_packages=True,
    )
    try:
        assert preview_manifest != user_manifest
        assert preview_manifest.name == "apm.yml"
        assert not user_manifest.parent.exists()
    finally:
        temp_dir.cleanup()


def test_existing_user_manifest_is_read_in_place(tmp_path: Path) -> None:
    user_manifest = tmp_path / ".apm" / "apm.yml"
    user_manifest.parent.mkdir(parents=True)
    user_manifest.write_text("name: test\n", encoding="utf-8")

    preview_manifest, temp_dir = _prepare_dry_run_manifest_path(
        user_manifest,
        dry_run=True,
        user_scope=True,
        has_packages=True,
    )

    assert preview_manifest == user_manifest
    assert temp_dir is None
