"""Keep the installable Skill package identical to the repository sources."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skills" / "decision-journal"

MIRROR_DIRS = ("backend", "frontend")
COPY_FILES = ("scripts/__init__.py", "scripts/decision_journal.py")
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "backups"}
EXCLUDE_SUFFIXES = (".pyc", ".db", ".db-wal", ".db-shm", ".db.migrated", ".sqlite", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")
EXCLUDE_NAMES = {("backend", "README.md")}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(base: Path, relative: str) -> dict[str, str]:
    root = base / relative
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.name.endswith(EXCLUDE_SUFFIXES):
            continue
        item = path.relative_to(root).as_posix()
        if (relative, item) in EXCLUDE_NAMES:
            continue
        result[item] = _digest(path)
    return result


def test_installable_package_matches_sources() -> None:
    problems: list[str] = []
    for relative in MIRROR_DIRS:
        source = _files(ROOT, relative)
        package = _files(PACKAGE, relative)
        only_source = sorted(set(source) - set(package))
        only_package = sorted(set(package) - set(source))
        changed = sorted(name for name in set(source) & set(package) if source[name] != package[name])
        for name in only_source:
            problems.append(f"{relative}/{name}: missing from Skill package")
        for name in only_package:
            problems.append(f"{relative}/{name}: only exists in Skill package")
        for name in changed:
            problems.append(f"{relative}/{name}: differs from source")
    for relative in COPY_FILES:
        source = ROOT / relative
        package = PACKAGE / relative
        if not package.is_file():
            problems.append(f"{relative}: missing from Skill package")
        elif _digest(source) != _digest(package):
            problems.append(f"{relative}: differs from source")
    assert not problems, "Run scripts/sync_skill_package.py:\n" + "\n".join(problems)
