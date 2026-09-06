"""Deterministic scan counts and parsing contracts for ADO path markers."""

import ast
import inspect
import textwrap
from collections.abc import Iterable
from typing import overload
from urllib.parse import urlparse

import pytest

from apm_cli.models.dependency import reference
from apm_cli.models.dependency.reference import DependencyReference

pytestmark = pytest.mark.unit


class _CountedParts(list[str]):
    """Observe marker searches, slice copies and indexed deletions."""

    def __init__(self, values: Iterable[str]) -> None:
        super().__init__(values)
        self.marker_contains = 0
        self.marker_indexes = 0
        self.marker_index: int | None = None
        self.marker_removal_slices = 0
        self.deletion_indexes: list[int | slice] = []

    def __contains__(self, value: object) -> bool:
        if value == "_git":
            self.marker_contains += 1
        return super().__contains__(value)

    def index(self, value: str, start: int = 0, stop: int | None = None) -> int:
        if value == "_git":
            self.marker_indexes += 1
        index = super().index(value, start, len(self) if stop is None else stop)
        if value == "_git":
            self.marker_index = index
        return index

    @overload
    def __getitem__(self, index: int) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> list[str]: ...

    def __getitem__(self, index: int | slice) -> str | list[str]:
        if isinstance(index, slice) and self.marker_index is not None and not self.deletion_indexes:
            self.marker_removal_slices += 1
        return super().__getitem__(index)

    def __delitem__(self, index: int | slice) -> None:
        super().__delitem__(index)
        self.deletion_indexes.append(index)


class _CountedPath(str):
    """Expose the real resolver's split lists to the test."""

    def __init__(self, value: str) -> None:
        self.parts: list[_CountedParts] = []

    def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
        parts = _CountedParts(super().split(sep, maxsplit))
        self.parts.append(parts)
        return parts


@pytest.mark.parametrize("marker", ["_git/", ""])
@pytest.mark.parametrize("virtual", [False, True])
def test_shorthand_resolvers_scan_marker_once(marker: str, virtual: bool) -> None:
    """Both fallback resolvers scan once, including when no marker exists."""
    path = _CountedPath(f"org/project/{marker}repo" + ("/skills/demo" if virtual else ""))
    if virtual:
        host, repo = DependencyReference._resolve_virtual_shorthand_repo(
            path, "dev.azure.com", "skills/demo"
        )
        assert repo == "org/project/repo"
        assert host is not None
    else:
        parsed, host, port = DependencyReference._resolve_shorthand_to_parsed_url(
            path, "dev.azure.com"
        )
        assert (parsed.hostname, parsed.path, host, port) == (
            "dev.azure.com",
            "/org/project/repo",
            "dev.azure.com",
            None,
        )
    assert sum(parts.marker_contains for parts in path.parts) == 0
    assert sum(parts.marker_indexes for parts in path.parts) == 1
    assert sum(parts.marker_removal_slices for parts in path.parts) == 0
    assert [index for parts in path.parts for index in parts.deletion_indexes] == (
        [2] if marker else []
    )


@pytest.mark.parametrize(
    ("host", "path", "expected_repo", "expected_virtual", "marker_index"),
    [
        ("dev.azure.com", "org/project/_git/repo", "org/project/repo", None, 2),
        ("dev.azure.com", "org/project/repo", "org/project/repo", None, None),
        ("dev.azure.com", "org/My%20Project/_git/repo.git", "org/My Project/repo", None, 2),
        (
            "org.visualstudio.com",
            "project/_git/repo/skills/demo",
            "org/project/repo",
            "skills/demo",
            1,
        ),
        ("org.visualstudio.com", "project/repo", "org/project/repo", None, None),
        (
            "org.visualstudio.com",
            "org/project/repo/skills/demo",
            "org/project/repo",
            "skills/demo",
            None,
        ),
        ("github.com", "owner/repo", "owner/repo", None, None),
        ("github.com", "owner/%5Fgit/My%20Repo", "owner/My%20Repo", None, 1),
        (
            "dev.azure.com",
            "org/project/_git/repo/skills/_git/demo",
            "org/project/repo",
            "skills/_git/demo",
            2,
        ),
    ],
)
def test_url_validator_scans_marker_once(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    path: str,
    expected_repo: str,
    expected_virtual: str | None,
    marker_index: int | None,
) -> None:
    """One lookup deletes matching decoded/raw indexes without slice copies."""
    lists: list[_CountedParts] = []

    def counted_list(values: Iterable[str]) -> _CountedParts:
        parts = _CountedParts(values)
        lists.append(parts)
        return parts

    monkeypatch.setattr(reference, "list", counted_list, raising=False)
    assert DependencyReference._validate_url_repo_path(urlparse(f"https://{host}/{path}")) == (
        expected_repo,
        expected_virtual,
    )
    assert sum(parts.marker_contains for parts in lists) == 0
    assert sum(parts.marker_indexes for parts in lists) == 1
    assert sum(parts.marker_removal_slices for parts in lists) == 0
    assert len(lists) == 2
    expected_deletions = [] if marker_index is None else [marker_index]
    assert lists[0].deletion_indexes == lists[1].deletion_indexes == expected_deletions
    assert lists[0] is not lists[1]


@pytest.mark.parametrize(
    "method",
    [
        "_detect_virtual_package",
        "_resolve_virtual_shorthand_repo",
        "_resolve_shorthand_to_parsed_url",
        "_validate_url_repo_path",
    ],
)
def test_marker_stripping_sites_do_not_pre_scan(method: str) -> None:
    """Include the legacy detection branch now bypassed by host parsing."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(DependencyReference, method))))
    marker_memberships = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Constant)
        and node.left.value == "_git"
        and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
    ]
    assert marker_memberships == []
    marker_deletions = [
        target.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Delete)
        for target in node.targets
        if isinstance(target, ast.Subscript)
        and isinstance(target.value, ast.Name)
        and isinstance(target.slice, ast.Name)
        and target.slice.id == "git_idx"
    ]
    expected_lists = {
        "_detect_virtual_package": ["path_segments"],
        "_resolve_virtual_shorthand_repo": ["parts"],
        "_resolve_shorthand_to_parsed_url": ["parts"],
        "_validate_url_repo_path": ["path_parts", "presentation_path_parts"],
    }
    assert marker_deletions == expected_lists[method]


@pytest.mark.parametrize(
    ("source", "repo", "virtual"),
    [
        ("dev.azure.com/org/project/_git/repo", "org/project/repo", None),
        ("dev.azure.com/org/project/repo", "org/project/repo", None),
        ("https://dev.azure.com/org/My%20Project/_git/repo", "org/My Project/repo", None),
        ("org.visualstudio.com/project/_git/repo/skills/demo", "org/project/repo", "skills/demo"),
        (
            "https://org.visualstudio.com/project/_git/repo/skills/demo",
            "org/project/repo",
            "skills/demo",
        ),
        ("github.com/owner/repo/skills/_git/demo", "owner/repo", "skills/_git/demo"),
        ("owner/repo/skills/_git/demo", "owner/repo", "skills/_git/demo"),
        ("gitlab.com/group/subgroup/repo", "group/subgroup/repo", None),
        ("https://bitbucket.org/owner/repo.git", "owner/repo", None),
    ],
)
def test_public_parser_preserves_marker_coordinates(
    source: str, repo: str, virtual: str | None
) -> None:
    """Real parse consumers retain coordinates and non-ADO virtual markers."""
    dep = DependencyReference.parse(source)
    assert (dep.repo_url, dep.virtual_path, dep.is_virtual) == (repo, virtual, virtual is not None)
    if dep.is_azure_devops():
        assert (dep.ado_organization, dep.ado_project, dep.ado_repo) == tuple(repo.split("/"))


@pytest.mark.parametrize(
    "source",
    [
        "https://dev.azure.com/",
        "https://dev.azure.com/org/project",
        "https://dev.azure.com/org/project/_git",
        "https://org.visualstudio.com/project",
        "https://org.visualstudio.com/project/_git",
        "https://org.visualstudio.com/org/project/repo/skills/demo",
        "https://dev.azure.com/org/project/_git/repo/%2e%2e",
        "https://dev.azure.com/org/project/_git/repo/invalid.txt",
    ],
)
def test_public_parser_keeps_invalid_marker_paths_invalid(source: str) -> None:
    """A missing marker must not hide format or path-security failures."""
    with pytest.raises(ValueError):
        DependencyReference.parse(source)
