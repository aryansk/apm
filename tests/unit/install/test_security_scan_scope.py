from apm_cli.install.helpers.security_scan import _is_deployable_source_path


def test_deployable_primitives_are_scanned():
    deployable = [
        "SKILL.md",
        "plugin.json",
        ".apm/agents/reviewer.agent.md",
        ".github/instructions/security.instructions.md",
        "skills/review/SKILL.md",
        "hooks/hooks.json",
    ]

    assert all(_is_deployable_source_path(path) for path in deployable)


def test_source_only_files_are_not_scanned_by_install_gate():
    source_only = [
        "src/DotnetInspector.HostileNameFixtures/HostileLiterals.cs",
        "tests/fixtures/hostile-name.txt",
        "docs/hostile-metadata.md",
        "src/Program.cs",
    ]

    assert not any(_is_deployable_source_path(path) for path in source_only)


def test_windows_paths_are_normalized():
    assert _is_deployable_source_path(r".github\agents\reviewer.agent.md")
