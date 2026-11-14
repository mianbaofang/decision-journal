#!/usr/bin/env python3
"""Sync the installable Skill package from the repository sources.

The public repository keeps the runnable app at ``backend/`` and ``frontend/``;
the installable Skill at ``skills/decision-journal/`` ships a copy.  This script
mirrors the source trees into the package so the two cannot drift silently, and
``tests/test_package_sync.py`` verifies the result.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills" / "decision-journal"

MIRROR_DIRS = ("backend", "frontend")
COPY_FILES = ("scripts/__init__.py", "scripts/decision_journal.py")

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "backups"}
EXCLUDE_SUFFIXES = (".pyc", ".db", ".db-wal", ".db-shm", ".db.migrated", ".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")
EXCLUDE_NAMES = {("backend", "README.md")}


def _ignore(directory: str, names: list[str]) -> set[str]:
    """Filter generated files while copying."""
    ignored: set[str] = set()
    for name in names:
        if name in EXCLUDE_DIRS or name.endswith(EXCLUDE_SUFFIXES):
            ignored.add(name)
            continue
        relative = Path(directory).resolve().relative_to(ROOT)
        if (relative.as_posix(), name) in EXCLUDE_NAMES:
            ignored.add(name)
    return ignored


def sync() -> list[str]:
    changed: list[str] = []
    # Preflight every destination before deleting anything. Older installations
    # may still have a database here; a developer sync must not erase it.
    data_suffixes = tuple(suffix for suffix in EXCLUDE_SUFFIXES if suffix != ".pyc")
    for relative in MIRROR_DIRS:
        destination = PACKAGE / relative
        if destination.is_dir():
            for item in destination.rglob("*"):
                if item.is_file() and (item.name.endswith(data_suffixes) or "backups" in item.parts):
                    raise SystemExit("Refusing to replace a package directory containing local data; "
                                     "migrate or back it up outside the package first.")
    for relative in MIRROR_DIRS:
        source = ROOT / relative
        destination = PACKAGE / relative
        if not source.is_dir():
            raise SystemExit(f"Missing source directory: {source}")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=_ignore)
        changed.append(relative)
    for relative in COPY_FILES:
        source = ROOT / relative
        destination = PACKAGE / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        changed.append(relative)
    return changed


if __name__ == "__main__":
    for item in sync():
        print(f"synced {item}")
