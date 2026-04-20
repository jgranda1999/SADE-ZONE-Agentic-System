"""Filesystem roots for runtime output (results/) and tests."""

from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    """
    Return the repository root (directory containing ``pyproject.toml``).

    Used so ``results/`` and similar paths stay at the project root when code
    lives under ``src/sade/``.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return here.parents[2]
