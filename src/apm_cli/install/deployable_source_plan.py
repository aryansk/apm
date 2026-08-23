"""Authorized source paths for one package deployment.

The plan is built only after target, subset, and executable authorization.
It is the single source of truth for both the pre-deploy security scan and
skill materialization, so a source-only fixture cannot become deployable
because it happens to be scanned.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apm_cli.models.dependency.subsets import skill_subset_filter_tokens
from apm_cli.utils.paths import portable_relpath


@dataclass(frozen=True)
class DeployableSourcePlan:
    """Concrete, authorized source files for one package deployment."""

    source_root: Path
    paths: frozenset[str]

    @classmethod
    def create(
        cls,
        package_info: Any,
        targets: list[Any],
        *,
        skill_subset: tuple[str, ...] | None,
        hooks_approved: bool,
        canvas_approved: bool,
        skip_bin: bool,
    ) -> DeployableSourcePlan:
        """Build the authorized deploy set after all deployment gates resolve."""
        source_root = Path(package_info.install_path)
        paths: set[str] = set()
        target_primitives = {primitive for target in targets for primitive in target.primitives}

        def add_file(path: Path) -> None:
            if path.is_file() and not path.is_symlink():
                paths.add(portable_relpath(path, source_root))

        def add_tree(root: Path) -> None:
            if not root.is_dir():
                return
            for path in root.rglob("*"):
                add_file(path)

        if "prompts" in target_primitives or "commands" in target_primitives:
            for path in source_root.glob("*.prompt.md"):
                add_file(path)
            for path in (source_root / ".apm" / "prompts").rglob("*.prompt.md"):
                add_file(path)

        if "agents" in target_primitives:
            for path in source_root.glob("*.agent.md"):
                add_file(path)
            for path in (source_root / ".apm" / "agents").rglob("*.md"):
                add_file(path)

        if "instructions" in target_primitives:
            for path in (source_root / ".apm" / "instructions").rglob("*.instructions.md"):
                add_file(path)

        if hooks_approved and "hooks" in target_primitives:
            for root in (source_root / ".apm" / "hooks", source_root / "hooks"):
                for path in root.glob("*.json"):
                    add_file(path)

        if canvas_approved and "canvas" in target_primitives:
            add_tree(source_root / ".apm" / "extensions")

        if "skills" in target_primitives:
            source_skill = source_root / "SKILL.md"
            if source_skill.is_file():
                add_file(source_skill)
                for root in ("assets", "references", "scripts"):
                    add_tree(source_root / root)
                if not skip_bin:
                    add_tree(source_root / "bin")

            selected = skill_subset_filter_tokens(skill_subset)
            for skills_root in (source_root / "skills", source_root / ".apm" / "skills"):
                if not skills_root.is_dir():
                    continue
                for skill_dir in skills_root.iterdir():
                    if (
                        skill_dir.is_dir()
                        and (skill_dir / "SKILL.md").is_file()
                        and (selected is None or skill_dir.name in selected)
                    ):
                        add_tree(skill_dir)

        return cls(source_root=source_root, paths=frozenset(paths))

    def includes(self, relative_path: str) -> bool:
        """Return whether a portable source-relative path is authorized."""
        return relative_path.replace("\\", "/") in self.paths

    def copy_ignore(self, directory: str, contents: list[str]) -> list[str]:
        """Return source entries excluded from a skill copy by this plan."""
        current = Path(directory)
        ignored: list[str] = []
        for name in contents:
            candidate = current / name
            relative = portable_relpath(candidate, self.source_root)
            if self.includes(relative) or any(
                path.startswith(f"{relative}/") for path in self.paths
            ):
                continue
            ignored.append(name)
        return ignored
