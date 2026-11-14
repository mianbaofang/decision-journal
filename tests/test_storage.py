"""Storage regression tests use only isolated/synthetic databases."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import db
import pytest
import storage

ROOT = Path(__file__).resolve().parents[1]


def load_repo_script(name):
    """Resolve repository tools by file, regardless of global scripts packages."""
    spec = importlib.util.spec_from_file_location("storage_test_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_db(path, key="old", question="synthetic", *, setting=None, wal=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
    db._ensure_schema(conn)
    if key:
        conn.execute(
            "INSERT INTO decisions (id,question,mode,result,created_at,journal) VALUES(?,?,?,?,?,?)",
            (
                key,
                question,
                "rational",
                '{"source":"host-agent"}',
                "2026-11-14",
                '{"expectedOutcome":"learn","actualOutcome":"learned"}',
            ),
        )
    if setting:
        conn.execute("INSERT INTO config VALUES (?,?)", ("preference", json.dumps(setting)))
    conn.commit()
    return conn


def rows(path):
    with closing(sqlite3.connect(path)) as conn:
        return storage._contents(conn)


def test_paths_are_stable_and_explicit_overrides_win(tmp_path):
    home = tmp_path / "home"
    assert storage.resolve_db_path({}, home) == home / ".decision-journal" / "decision_journal.db"
    assert (
        storage.resolve_db_path({"DECISION_JOURNAL_DATA_DIR": str(tmp_path / "data")}, home)
        == tmp_path / "data" / "decision_journal.db"
    )
    assert (
        storage.resolve_db_path(
            {
                "DECISION_JOURNAL_DATA_DIR": str(tmp_path / "data"),
                "DECISION_JOURNAL_DB_PATH": str(tmp_path / "chosen.db"),
            },
            home,
        )
        == tmp_path / "chosen.db"
    )
    assert not home.exists()


def test_status_missing_is_read_only(tmp_path):
    target = tmp_path / "new" / "data.db"
    assert storage.storage_status(target)["exists"] is False
    assert not target.parent.exists()


def test_default_startup_imports_package_wal_journal_settings_once(tmp_path, monkeypatch):
    target, package = tmp_path / "user" / "data.db", tmp_path / "package"
    for name in ("DECISION_JOURNAL_DB_PATH", "DECISION_JOURNAL_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(storage, "resolve_db_path", lambda: target)
    old = package / "decision_journal.db"
    with closing(make_db(old, setting="old-setting", wal=True)):
        assert Path(str(old) + "-wal").exists()
        storage.prepare_database(target, package)
        data = rows(target)
        assert len(data["decisions"]) == 1
        assert json.loads(data["decisions"][0][-1])["actualOutcome"] == "learned"
        assert data["config"] == [("preference", '"old-setting"')]
        assert old.exists()
        with closing(sqlite3.connect(target)) as conn:
            conn.execute("DELETE FROM decisions")
            conn.commit()
        storage.prepare_database(target, package)
        assert rows(target)["decisions"] == []
        assert len(rows(old)["decisions"]) == 1


@pytest.mark.parametrize("env", ["DECISION_JOURNAL_DB_PATH", "DECISION_JOURNAL_DATA_DIR"])
def test_override_never_adopts_package_data(tmp_path, monkeypatch, env):
    target, package = tmp_path / "target.db", tmp_path / "package"
    make_db(package / "decision_journal.db").close()
    monkeypatch.setenv(env, str(tmp_path / "override"))
    monkeypatch.setattr(storage, "resolve_db_path", lambda: target)
    storage.prepare_database(target, package)
    assert not target.exists()


def test_populated_default_warns_and_keeps_both_databases(tmp_path, monkeypatch):
    target, package = tmp_path / "user.db", tmp_path / "package"
    make_db(target, "new").close()
    make_db(package / "decision_journal.db", "old").close()
    monkeypatch.delenv("DECISION_JOURNAL_DB_PATH", raising=False)
    monkeypatch.delenv("DECISION_JOURNAL_DATA_DIR", raising=False)
    monkeypatch.setattr(storage, "resolve_db_path", lambda: target)
    with pytest.warns(RuntimeWarning, match="left untouched"):
        storage.prepare_database(target, package)
    assert [r[0] for r in rows(target)["decisions"]] == ["new"]
    assert len(storage.storage_status(target, package)["pending_package_databases"]) == 1


def test_ambiguous_package_databases_block_automatic_migration(tmp_path, monkeypatch):
    target, package = tmp_path / "user.db", tmp_path / "package"
    for name in ("choice.db", "decision_journal.db"):
        make_db(package / name).close()
    monkeypatch.delenv("DECISION_JOURNAL_DB_PATH", raising=False)
    monkeypatch.delenv("DECISION_JOURNAL_DATA_DIR", raising=False)
    monkeypatch.setattr(storage, "resolve_db_path", lambda: target)
    with pytest.raises(storage.StorageError, match="Multiple legacy"):
        storage.prepare_database(target, package)
    assert not target.exists()


def test_explicit_merge_preserves_target_settings_and_is_idempotent(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    make_db(source, "old", setting="old").close()
    make_db(target, "new", setting="new").close()
    result = storage.import_database(target, source, merge=True)
    assert result["settings_kept"] == 1
    assert result["decisions_imported"] == 1
    assert [r[0] for r in rows(target)["decisions"]] == ["new", "old"]
    assert rows(target)["config"] == [("preference", '"new"')]
    assert [r[0] for r in rows(Path(result["safety_backup"]))["decisions"]] == ["new"]
    assert storage.import_database(target, source, merge=True)["status"] == "already_imported"
    assert len(rows(source)["decisions"]) == 1


def test_merge_conflict_is_atomic_and_never_silently_discards(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    make_db(source, "same", "different").close()
    make_db(target, "same", "keep").close()
    before = rows(target)
    with pytest.raises(storage.StorageError, match="Conflicting decision IDs"):
        storage.import_database(target, source, merge=True)
    assert rows(target) == before
    assert not (tmp_path / "backups").exists()


def test_existing_target_requires_explicit_merge(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    make_db(source).close()
    make_db(target, None, setting="keep").close()
    with pytest.raises(storage.StorageError, match="--merge"):
        storage.import_database(target, source)
    assert rows(target)["config"] == [("preference", '"keep"')]


def test_backup_includes_uncheckpointed_wal_and_refuses_overwrite(tmp_path):
    source, backup = tmp_path / "source.db", tmp_path / "copy.sqlite3"
    with closing(make_db(source, setting="fake-local-setting", wal=True)):
        result = storage.backup_database(source, backup)
        assert result["decisions"] == 1
        assert rows(source)["decisions"] == rows(backup)["decisions"]
        assert rows(source)["config"] == rows(backup)["config"]
        with pytest.raises(storage.StorageError, match="already exists"):
            storage.backup_database(source, backup)
    assert not list(tmp_path.glob("*.partial-*"))


def test_restore_requires_explicit_replace_and_creates_rollback_backup(tmp_path):
    source, target = tmp_path / "snapshot.db", tmp_path / "target.db"
    make_db(source, "restore", setting="previous").close()
    make_db(target, "current", setting="keep-before-replace").close()
    before = rows(target)
    with pytest.raises(storage.StorageError, match="--replace"):
        storage.import_database(target, source, restore=True)
    assert rows(target) == before
    result = storage.import_database(target, source, restore=True, replace=True)
    assert rows(target)["decisions"] == rows(source)["decisions"]
    assert rows(target)["config"] == rows(source)["config"]
    rollback = Path(result["safety_backup"])
    assert rows(rollback)["decisions"] == before["decisions"]
    assert rows(rollback)["config"] == before["config"]
    storage.import_database(target, rollback, restore=True, replace=True)
    assert rows(target)["decisions"] == before["decisions"]


def test_restore_empty_target_retains_migration_history(tmp_path):
    source, target, restored = tmp_path / "old.db", tmp_path / "target.db", tmp_path / "restored.db"
    make_db(source).close()
    storage.import_database(target, source)
    backup = Path(storage.backup_database(target)["backup"])
    storage.import_database(restored, backup, restore=True)
    assert rows(restored)["markers"] == rows(target)["markers"]


@pytest.mark.parametrize("bad", ["missing", "corrupt", "unrelated", "json", "future", "duplicates"])
def test_invalid_source_leaves_target_absent(tmp_path, bad):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    if bad == "corrupt":
        source.write_text("not sqlite", encoding="utf-8")
    elif bad == "unrelated":
        with closing(sqlite3.connect(source)) as conn:
            conn.execute("CREATE TABLE something_else (value TEXT)")
    elif bad not in {"missing"}:
        with closing(make_db(source)) as conn:
            if bad == "json":
                conn.execute("UPDATE decisions SET journal='broken json'")
            elif bad == "future":
                conn.execute("ALTER TABLE decisions ADD COLUMN future_field TEXT")
            elif bad == "duplicates":
                conn.execute("ALTER TABLE decisions RENAME TO original")
                conn.execute("CREATE TABLE decisions AS SELECT * FROM original")
                conn.execute("INSERT INTO decisions SELECT * FROM original")
            conn.commit()
    with pytest.raises((storage.StorageError, sqlite3.Error)):
        storage.import_database(target, source, restore=True)
    assert not target.exists()


def test_restore_failure_rolls_back_and_retains_safety_backup(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    make_db(source, "incoming").close()
    with closing(make_db(target, "keep")) as conn:
        conn.execute(
            "CREATE TRIGGER reject_restore BEFORE INSERT ON decisions WHEN NEW.id='incoming' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
        conn.commit()
    before = rows(target)
    with pytest.raises(sqlite3.IntegrityError):
        storage.import_database(target, source, restore=True, replace=True)
    assert rows(target) == before
    assert len(list((tmp_path / "backups").glob("before-restore-*.sqlite3"))) == 1


@pytest.mark.parametrize("operation", ["backup", "restore", "migrate"])
def test_never_operate_with_source_as_destination(tmp_path, operation):
    source = tmp_path / "source.db"
    make_db(source).close()
    with pytest.raises(storage.StorageError, match="different files"):
        if operation == "backup":
            storage.backup_database(source, source)
        else:
            storage.import_database(source, source, restore=operation == "restore", replace=True)


def test_unrelated_target_is_not_modified(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    make_db(source).close()
    with closing(sqlite3.connect(target)) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    before = target.read_bytes()
    with pytest.raises(storage.StorageError):
        storage.import_database(target, source, restore=True, replace=True)
    assert target.read_bytes() == before


def test_stdlib_only_cli_status_and_help_do_not_start_backend(tmp_path):
    env = dict(os.environ, DECISION_JOURNAL_DB_PATH=str(tmp_path / "never-created" / "db.sqlite3"))
    for arguments in (["data", "status"], ["data", "--help"], ["data", "restore", "--help"]):
        process = subprocess.run(
            [sys.executable, "-S", str(ROOT / "scripts" / "decision_journal.py"), *arguments],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert process.returncode == 0, process.stderr
        if arguments[-1] == "status":
            assert json.loads(process.stdout)["exists"] is False
    assert not (tmp_path / "never-created").exists()


def test_legacy_choice_import_retains_journal(tmp_path, monkeypatch):
    make_db(tmp_path / "choice.db").close()
    target = tmp_path / "new.db"
    monkeypatch.setattr(db, "DB_PATH", target)
    db.init_db()
    assert db.get_decision("old")["journal"]["actualOutcome"] == "learned"


def test_upgrade_relocation_keeps_user_data(tmp_path):
    import shutil

    first = tmp_path / "install-v1" / "backend"
    second = tmp_path / "install-v2" / "backend"
    for destination in (first, second):
        shutil.copytree(
            ROOT / "backend",
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.db", "*.db-*", "*.db.migrated"),
        )
    make_db(first / "decision_journal.db", "kept-after-upgrade", setting="synthetic-setting").close()
    env = dict(os.environ)
    for key in ("DECISION_JOURNAL_DB_PATH", "DECISION_JOURNAL_DATA_DIR"):
        env.pop(key, None)
    user_home = tmp_path / "isolated-home"
    env.update(USERPROFILE=str(user_home), HOME=str(user_home))
    program = (
        "import sys,json;"
        "sys.path.insert(0,sys.argv[1]);"
        "import db;"
        "db.init_db();"
        "print(json.dumps({'path':str(db.DB_PATH),'records':db.list_decisions(),'setting':db.get_config_value('preference')}))"
    )

    def run(backend):
        result = subprocess.run(
            [sys.executable, "-S", "-c", program, str(backend)], env=env, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    before = run(first)
    # The previous installation is no longer at its original path.
    first.parent.rename(tmp_path / "retired-installation")
    after = run(second)
    assert before == after
    assert after["records"][0]["id"] == "kept-after-upgrade"
    assert after["records"][0]["journal"]["actualOutcome"] == "learned"
    assert after["setting"] == "synthetic-setting"
    assert Path(after["path"]) == user_home / ".decision-journal" / "decision_journal.db"


def test_restore_serializes_a_real_second_process_writer(tmp_path, monkeypatch):
    source, target = tmp_path / "snapshot.db", tmp_path / "target.db"
    make_db(source, "from-backup").close()
    make_db(target, "before-restore").close()
    child_ready = tmp_path / "writer-ready"
    child_acquired = tmp_path / "writer-acquired"
    original = storage._backup_connection
    process = None

    def interleave(connection, destination):
        nonlocal process
        program = (
            "import sqlite3,sys,pathlib;"
            "pathlib.Path(sys.argv[2]).write_text('ready');"
            "c=sqlite3.connect(sys.argv[1],timeout=10);"
            "c.execute('BEGIN IMMEDIATE');"
            "pathlib.Path(sys.argv[3]).write_text('acquired');"
            'c.execute("INSERT INTO decisions(id,question,mode,result,created_at) '
            "VALUES ('concurrent','synthetic','rational','{}','2026-11-14')\");"
            "c.commit();"
            "c.close()"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program, str(target), str(child_ready), str(child_acquired)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        import time

        deadline = time.monotonic() + 8
        while not child_ready.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(process.communicate())
            time.sleep(0.01)
        assert child_ready.exists()
        assert not child_acquired.exists(), "restore must already own the writer reservation"
        return original(connection, destination)

    monkeypatch.setattr(storage, "_backup_connection", interleave)
    try:
        result = storage.import_database(target, source, restore=True, replace=True)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
    assert {row[0] for row in rows(target)["decisions"]} == {"from-backup", "concurrent"}
    assert {row[0] for row in rows(Path(result["safety_backup"]))["decisions"]} == {"before-restore"}


def test_backup_publication_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    target, backup = tmp_path / "target.db", tmp_path / "backup.sqlite3"
    make_db(target).close()

    def fail(*_args, **_kwargs):
        raise OSError("synthetic publish failure")

    monkeypatch.setattr(os, "rename" if os.name == "nt" else "link", fail)
    with pytest.raises(OSError):
        storage.backup_database(target, backup)
    assert not backup.exists()
    assert not list(tmp_path.glob("*.partial-*"))
    assert len(rows(target)["decisions"]) == 1


def test_release_filter_excludes_all_local_sqlite_artifacts():
    excluded = load_repo_script("package_skill").excluded
    exclude_suffixes = load_repo_script("sync_skill_package").EXCLUDE_SUFFIXES

    names = (
        "copy.sqlite3",
        "choice.db.migrated",
        "decision_journal.db-wal",
        "decision_journal.db-shm",
        "copy.sqlite3-wal",
        "copy.sqlite3-shm",
    )
    for name in names:
        assert excluded(Path("backend") / name)
        assert name.endswith(exclude_suffixes)
    assert excluded(Path("backups/private-data.bin"))
    assert not excluded(Path("backend/storage.py"))


def test_explicit_choice_database_is_not_renamed_or_emptied(tmp_path, monkeypatch):
    target = tmp_path / "choice.db"
    make_db(target, None, setting="keep-explicit-config").close()
    monkeypatch.setenv("DECISION_JOURNAL_DB_PATH", str(target))
    monkeypatch.setattr(db, "DB_PATH", target)
    db.init_db()
    assert target.exists()
    assert not target.with_name("choice.db.migrated").exists()
    assert db.get_config_value("preference") == "keep-explicit-config"


def test_developer_sync_refuses_to_erase_legacy_package_data(tmp_path, monkeypatch):
    sync = load_repo_script("sync_skill_package")

    source = tmp_path / "repository"
    package = source / "skills" / "decision-journal"
    (source / "backend").mkdir(parents=True)
    (source / "frontend").mkdir()
    old = package / "backend" / "decision_journal.db"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"synthetic legacy data must survive")
    monkeypatch.setattr(sync, "ROOT", source)
    monkeypatch.setattr(sync, "PACKAGE", package)
    with pytest.raises(SystemExit, match="containing local data"):
        sync.sync()
    assert old.read_bytes() == b"synthetic legacy data must survive"
