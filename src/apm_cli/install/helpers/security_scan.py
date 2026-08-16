"""Pre-deploy security scan that runs before any file is written to the project tree.

Wraps the :class:`~apm_cli.security.gate.SecurityGate` scanner used by the
install pipeline. The scan detects hidden characters (zero-width joiners,
bidirectional overrides, etc.) that could be used to smuggle malicious payloads
into prompts, skills, or agent definitions.
"""

from pathlib import Path

from apm_cli.utils.diagnostics import DiagnosticCollector


_DEPLOYABLE_DIRS = frozenset(
    {
        ".apm",
        ".github",
        ".claude-plugin",
        "agents",
        "commands",
        "context",
        "contexts",
        "hooks",
        "instructions",
        "memory",
        "prompts",
        "skills",
    }
)
_DEPLOYABLE_NAMES = frozenset(
    {"SKILL.md", "plugin.json", "hooks.json", ".mcp.json", "lsp.json"}
)
_DEPLOYABLE_SUFFIXES = (
    ".agent.md",
    ".instructions.md",
    ".context.md",
    ".memory.md",
    ".prompt.md",
)


def _is_deployable_source_path(relative_path: str) -> bool:
    """Return whether a fetched source file belongs to deployable package content."""
    parts = tuple(part for part in relative_path.replace("\\", "/").split("/") if part)
    if not parts:
        return False
    if parts[-1] in _DEPLOYABLE_NAMES or parts[-1].endswith(_DEPLOYABLE_SUFFIXES):
        return True
    return any(part in _DEPLOYABLE_DIRS for part in parts[:-1])


def _pre_deploy_security_scan(
    install_path: Path,
    diagnostics: DiagnosticCollector,
    package_name: str = "",
    force: bool = False,
    logger=None,
) -> bool:
    """Scan package source files for hidden characters BEFORE deployment.

    Delegates to :class:`SecurityGate` for the scan->classify->decide pipeline.
    Inline CLI feedback (error/info lines) is kept here because it is
    install-specific formatting.

    Returns:
        True if deployment should proceed, False to block.
    """
    from apm_cli.security.gate import BLOCK_POLICY, SecurityGate

    verdict = SecurityGate.scan_files(
        install_path,
        policy=BLOCK_POLICY,
        force=force,
        path_filter=_is_deployable_source_path,
    )
    if not verdict.has_findings:
        return True

    # Record into diagnostics (consistent messaging via gate)
    SecurityGate.report(verdict, diagnostics, package=package_name, force=force)

    if verdict.should_block:
        if logger:
            logger.error(
                f"  Blocked: {package_name or 'package'} contains critical hidden character(s)"
            )
            logger.tree_item(f"  |-- Source checkout: {install_path}")
            logger.tree_item(
                "  |-- Note: a failed install may remove this checkout during transaction cleanup"
            )
            logger.tree_item("  |-- Use --force to deploy anyway")
        return False

    return True
