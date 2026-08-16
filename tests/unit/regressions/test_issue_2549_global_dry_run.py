from apm_cli.commands.install import _prepare_dry_run_manifest_path


def test_global_dry_run_redirects_absent_manifest_without_creating_user_state(tmp_path):
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


def test_existing_user_manifest_is_read_in_place(tmp_path):
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
