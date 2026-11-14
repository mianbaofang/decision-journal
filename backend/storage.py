"""Local SQLite maintenance. No HTTP, model calls, or optional dependencies.

Never replace a live database file: imports/restores modify application rows in
one SQLite write transaction; backups use SQLite's snapshot API (including WAL).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
import warnings
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


class StorageError(ValueError):
    """Actionable maintenance failure; messages never contain record/key values."""


class TargetNotEmpty(StorageError):
    """Explicit merge/replacement is needed to modify existing data."""


COLUMNS = (
    "id",
    "question",
    "mode",
    "result",
    "brief",
    "created_at",
    "executed",
    "regret",
    "dialogue_history",
    "journal",
)
REQUIRED = {"id", "question", "mode", "result", "created_at"}
MIGRATIONS = "_decision_journal_migrations"


def resolve_db_path(environ=None, home=None) -> Path:
    env = os.environ if environ is None else environ
    root = Path.home() if home is None else Path(home)
    if env.get("DECISION_JOURNAL_DB_PATH"):
        return Path(env["DECISION_JOURNAL_DB_PATH"]).expanduser().resolve()
    directory = (
        Path(env["DECISION_JOURNAL_DATA_DIR"]).expanduser()
        if env.get("DECISION_JOURNAL_DATA_DIR")
        else root / ".decision-journal"
    )
    return (directory / "decision_journal.db").resolve()


def _readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise StorageError(f"Database file does not exist: {path}")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)


def _same_file(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and os.path.samefile(a, b))


def _contents(conn: sqlite3.Connection) -> dict:
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise StorageError("SQLite integrity check failed; source was left untouched.")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
    if not REQUIRED.issubset(columns):
        raise StorageError("Not a supported Decision Journal database (missing decision columns).")
    if columns - set(COLUMNS):
        raise StorageError("Database has newer/unknown decision fields; use a compatible app before importing.")
    defaults = {"executed": "0", "regret": "0"}
    selection = ", ".join(name if name in columns else defaults.get(name, "NULL") for name in COLUMNS)
    decisions = conn.execute("SELECT " + selection + " FROM decisions ORDER BY id").fetchall()
    if len({row[0] for row in decisions}) != len(decisions):
        raise StorageError("Duplicate source decision IDs; no rows were imported.")
    for row in decisions:
        if any(
            not isinstance(row[COLUMNS.index(name)], str) or not row[COLUMNS.index(name)].strip() for name in REQUIRED
        ):
            raise StorageError("A source record is incomplete; no rows were imported.")
        for index in (3, 4, 8, 9):
            if row[index] is not None:
                try:
                    parsed = json.loads(row[index])
                    if not isinstance(parsed, list if index == 8 else dict):
                        raise ValueError("Unexpected JSON type")
                except (ValueError, TypeError):
                    raise StorageError("A source record has invalid JSON; no rows were imported.") from None
    config_columns = {row[1] for row in conn.execute("PRAGMA table_info(config)")}
    if config_columns and config_columns != {"key", "value"}:
        raise StorageError("Unsupported settings table; source was left untouched.")
    config = conn.execute("SELECT key, value FROM config ORDER BY key").fetchall() if config_columns else []
    if len({row[0] for row in config}) != len(config):
        raise StorageError("Duplicate source setting keys; no rows were imported.")
    if any(key is None or value is None for key, value in config):
        raise StorageError("A source setting is incomplete; no rows were imported.")
    marker_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({MIGRATIONS})")}
    if marker_columns and marker_columns != {"source", "imported_at"}:
        raise StorageError("Unsupported migration history; use a compatible app before importing.")
    markers = (
        conn.execute(f"SELECT source, imported_at FROM {MIGRATIONS}").fetchall()
        if marker_columns == {"source", "imported_at"}
        else []
    )
    return {"decisions": decisions, "config": config, "markers": markers}


def _ensure_markers(conn: sqlite3.Connection) -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {MIGRATIONS} (source TEXT PRIMARY KEY, imported_at TEXT NOT NULL)")


def ensure_database_file(target: Path) -> None:
    """Create only new directories/files privately; retain existing permissions."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        return
    os.close(fd)


def _open_target(target: Path) -> sqlite3.Connection:
    # Import lazily: db.init_db calls this module after db has finished loading.
    from db import _ensure_schema

    if target.exists():
        with closing(_readonly(target)) as existing:
            tables = existing.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                _contents(existing)
    ensure_database_file(target)
    conn = sqlite3.connect(str(target), timeout=15)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(conn)
        _ensure_markers(conn)
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def _snapshot(path: Path) -> sqlite3.Connection:
    snapshot = sqlite3.connect(":memory:")
    try:
        with closing(_readonly(path)) as source:
            source.backup(snapshot)
        _contents(snapshot)
        return snapshot
    except Exception:
        snapshot.close()
        raise


def _backup_connection(source: sqlite3.Connection, destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise StorageError(f"Backup destination already exists; choose a new filename: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    partial = destination.with_name(destination.name + ".partial-" + uuid.uuid4().hex)
    fd = os.open(str(partial), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(str(partial))) as output:
            source.backup(output)
            output.execute("PRAGMA journal_mode=DELETE")
            _contents(output)
        # Link publishes only a completed file and refuses to overwrite even if
        # another process creates destination after our initial existence check.
        if os.name == "nt":
            os.rename(str(partial), str(destination))  # Windows rename refuses an existing destination.
        else:
            os.link(str(partial), str(destination))
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _backup_name(target: Path, prefix="decision-journal") -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return target.parent / "backups" / f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"


def backup_database(target: Path, destination: Path | None = None) -> dict:
    target = Path(target).expanduser().resolve()
    destination = Path(destination) if destination is not None else _backup_name(target)
    if _same_file(target, destination):
        raise StorageError("Backup source and destination must be different files.")
    with closing(_snapshot(target)) as snapshot:
        contents = _contents(snapshot)
        saved = _backup_connection(snapshot, destination)
    return {
        "backup": str(saved),
        "decisions": len(contents["decisions"]),
        "settings": len(contents["config"]),
        "may_contain_credentials": bool(contents["config"]),
        "scope": "SQLite records, journal, SQLite settings and migration history; config.json and environment excluded",
    }


def import_database(target: Path, source: Path, *, replace=False, merge=False, restore=False) -> dict:
    target, source = Path(target).expanduser().resolve(), Path(source).expanduser().resolve()
    if _same_file(target, source):
        raise StorageError("Source and active database must be different files.")
    # Validate the complete source before creating or changing the target.
    with closing(_snapshot(source)) as snapshot:
        incoming = _contents(snapshot)
        with closing(_open_target(target)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = _contents(conn)
                marker = str(source)
                if not restore and any(row[0] == marker for row in current["markers"]):
                    conn.rollback()
                    return {"status": "already_imported", "source": marker, "source_preserved": True}
                populated = bool(current["decisions"] or current["config"])
                if populated and not (replace if restore else merge):
                    flag = "--replace" if restore else "--merge"
                    raise TargetNotEmpty(
                        f"Target already contains data/settings. Review it first; explicit {flag} is required."
                    )
                if not restore:
                    existing = {row[0]: row for row in current["decisions"]}
                    if any(
                        row[0] in existing and tuple(row) != tuple(existing[row[0]]) for row in incoming["decisions"]
                    ):
                        raise StorageError(
                            "Conflicting decision IDs: nothing imported; originals preserved. "
                            "Review the two databases separately."
                        )
                safety_backup = None
                if populated:
                    # BEGIN IMMEDIATE reserves the writer. A separate read
                    # connection captures exactly the pre-change committed rows.
                    with closing(_readonly(target)) as before:
                        safety_backup = _backup_connection(
                            before, _backup_name(target, "before-restore" if restore else "before-merge")
                        )
                if restore:
                    conn.execute("DELETE FROM decisions")
                    conn.execute("DELETE FROM config")
                before_ids = set() if restore else {row[0] for row in current["decisions"]}
                current_keys = set() if restore else {row[0] for row in current["config"]}
                kept_settings = sum(key in current_keys for key, _ in incoming["config"])
                conn.executemany(
                    "INSERT INTO decisions (" + ",".join(COLUMNS) + ") VALUES (" + ",".join("?" for _ in COLUMNS) + ")",
                    [row for row in incoming["decisions"] if row[0] not in before_ids],
                )
                conn.executemany(
                    "INSERT INTO config (key,value) VALUES (?,?)",
                    [row for row in incoming["config"] if row[0] not in current_keys],
                )
                conn.executemany(
                    f"INSERT OR IGNORE INTO {MIGRATIONS} (source,imported_at) VALUES (?,?)", incoming["markers"]
                )
                if not restore:
                    conn.execute(
                        f"INSERT INTO {MIGRATIONS} (source,imported_at) VALUES (?,?)",
                        (marker, datetime.now(timezone.utc).isoformat()),
                    )
                expected_decisions = {} if restore else {row[0]: row for row in current["decisions"]}
                expected_decisions.update({row[0]: row for row in incoming["decisions"]})
                expected_config = {row[0]: row for row in incoming["config"]}
                if not restore:
                    expected_config.update({row[0]: row for row in current["config"]})
                applied = _contents(conn)
                if applied["decisions"] != sorted(expected_decisions.values(), key=lambda row: row[0]) or applied[
                    "config"
                ] != sorted(expected_config.values(), key=lambda row: row[0]):
                    raise StorageError("Import verification failed; the transaction was rolled back.")
                conn.commit()
                return {
                    "status": "restored" if restore else "imported",
                    "database": str(target),
                    "source": str(source),
                    "source_preserved": True,
                    "decisions_imported": sum(row[0] not in before_ids for row in incoming["decisions"]),
                    "settings_kept": kept_settings,
                    "safety_backup": str(safety_backup) if safety_backup else None,
                }
            except Exception:
                conn.rollback()
                raise


def package_sources(target: Path, package_dir: Path) -> list[Path]:
    return [
        path
        for path in (package_dir / "decision_journal.db", package_dir / "choice.db")
        if path.is_file() and not _same_file(path, target)
    ]


def prepare_database(target: Path, package_dir: Path | None = None) -> None:
    """Automatic adoption only for the default path and an empty destination."""
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (
        os.environ.get("DECISION_JOURNAL_DB_PATH")
        or os.environ.get("DECISION_JOURNAL_DATA_DIR")
        or target.resolve() != resolve_db_path()
    ):
        return
    sources = package_sources(target, package_dir or Path(__file__).parent)
    if not sources:
        return
    # A durable marker prevents re-importing deleted/changed records on restart.
    marked = set()
    if target.exists():
        with closing(_readonly(target)) as conn:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({MIGRATIONS})")}
            if cols == {"source", "imported_at"}:
                marked = {row[0] for row in conn.execute(f"SELECT source FROM {MIGRATIONS}")}
    sources = [p for p in sources if str(p.resolve()) not in marked]
    if not sources:
        return
    if len(sources) > 1:
        raise StorageError(
            "Multiple legacy databases found. Use 'data migrate --from PATH' to select explicitly; originals preserved."
        )
    try:
        import_database(target, sources[0])
    except TargetNotEmpty:
        warnings.warn(
            "A package database was left untouched because the user database is populated. "
            "Inspect 'data status', then use 'data migrate --from PATH --merge' if intended.",
            RuntimeWarning,
            stacklevel=2,
        )


def storage_status(target: Path, package_dir: Path | None = None) -> dict:
    target = Path(target).expanduser().resolve()
    result = {
        "database": str(target),
        "exists": target.is_file(),
        "decisions": 0,
        "settings": 0,
        "backup_directory": str(target.parent / "backups"),
        "path_source": "DECISION_JOURNAL_DB_PATH"
        if os.environ.get("DECISION_JOURNAL_DB_PATH")
        else "DECISION_JOURNAL_DATA_DIR"
        if os.environ.get("DECISION_JOURNAL_DATA_DIR")
        else "user_home",
    }
    marked = set()
    if result["exists"]:
        with closing(_readonly(target)) as conn:
            contents = _contents(conn)
        result.update(decisions=len(contents["decisions"]), settings=len(contents["config"]))
        marked = {row[0] for row in contents["markers"]}
    result["pending_package_databases"] = [
        str(p.resolve())
        for p in package_sources(target, package_dir or Path(__file__).parent)
        if str(p.resolve()) not in marked
    ]
    return result
