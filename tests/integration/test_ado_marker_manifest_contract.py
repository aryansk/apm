"""ADO marker parsing through real manifest and lockfile consumers."""

from pathlib import Path
from urllib.parse import urlparse

import pytest

from apm_cli.deps.lockfile import LockedDependency
from apm_cli.models.apm_package import APMPackage
from apm_cli.utils.yaml_io import dump_yaml

pytestmark = pytest.mark.component


@pytest.mark.parametrize("host", ["dev.azure.com", "org.visualstudio.com"])
@pytest.mark.parametrize("marker", ["_git/", ""])
def test_manifest_marker_coordinates_survive_lock_replay(
    tmp_path: Path, host: str, marker: str
) -> None:
    """Manifest consumers preserve ADO identity and install paths across replay."""
    path = (
        f"org/My%20Project/{marker}Repo"
        if host == "dev.azure.com"
        else f"My%20Project/{marker}Repo"
    )
    manifest = tmp_path / "apm.yml"
    dump_yaml(
        {
            "name": "marker-consumer",
            "version": "1.0.0",
            "dependencies": {
                "apm": [{"git": f"https://{host}/{path}", "path": "skills/demo", "ref": "v1.0.0"}]
            },
        },
        manifest,
    )
    dependencies = APMPackage.from_apm_yml(manifest).get_apm_dependencies()
    assert len(dependencies) == 1
    dep = dependencies[0]
    restored = LockedDependency.from_dict(
        LockedDependency.from_dependency_ref(
            dep, resolved_commit="a" * 40, depth=1, resolved_by=None
        ).to_dict()
    ).to_dependency_ref()

    for parsed in (dep, restored):
        assert (parsed.ado_organization, parsed.ado_project, parsed.ado_repo) == (
            "org",
            "My Project",
            "Repo",
        )
        assert parsed.virtual_path == "skills/demo"
        assert parsed.reference == "v1.0.0"
        clone_url = urlparse(parsed.to_github_url())
        assert (clone_url.scheme, clone_url.hostname, clone_url.path) == (
            "https",
            host,
            "/org/My%20Project/_git/Repo" if host == "dev.azure.com" else "/My%20Project/_git/Repo",
        )
    assert restored.get_unique_key() == dep.get_unique_key()
    assert restored.get_install_path(tmp_path / "apm_modules") == dep.get_install_path(
        tmp_path / "apm_modules"
    )
