"""数据库层（db.py）测试。

覆盖：
  - init_db 后 decisions / config 两表存在
  - save/get/list/update/delete/count 决策记录
  - config 表 CRUD
"""

import sqlite3

import db


def _make_decision(question: str = "今天吃什么", mode: str = "random", **overrides) -> dict:
    """构造一条决策记录 dict（便于多测试复用）。"""
    payload = {
        "question": question,
        "mode": mode,
        "result": {"type": mode, "options": ["A", "B"]},
        "executed": False,
        "regret": False,
    }
    payload.update(overrides)
    return payload


def test_init_db_creates_two_tables():
    """init_db 后 decisions 和 config 两张表必须存在。"""
    # conftest.py 已经调过 init_db，这里直接验证
    with db.get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "decisions" in tables
    assert "config" in tables


def test_save_and_get_decision():
    """save 后能按 id 查到，字段一致。"""
    saved = db.save_decision(_make_decision(question="要不要换工作", mode="rational"))
    assert saved["id"]
    assert saved["createdAt"]

    fetched = db.get_decision(saved["id"])
    assert fetched is not None
    assert fetched["question"] == "要不要换工作"
    assert fetched["mode"] == "rational"
    assert fetched["result"] == {"type": "rational", "options": ["A", "B"]}
    assert fetched["executed"] is False
    assert fetched["regret"] is False


def test_get_decision_returns_none_for_missing_id():
    """查不到的 id 返回 None。"""
    assert db.get_decision("not_exists_id_123") is None


def test_list_decisions_sorted_by_created_at_desc():
    """保存多条后按 createdAt 降序返回（最新在前）。"""
    import time

    # 顺序插入三条，并人工指定 createdAt 以保证顺序
    older = db.save_decision(_make_decision(question="旧决策", createdAt="2024-01-01T00:00:00"))
    middle = db.save_decision(_make_decision(question="中决策", createdAt="2024-06-01T00:00:00"))
    newer = db.save_decision(_make_decision(question="新决策", createdAt="2025-01-01T00:00:00"))

    items = db.list_decisions(limit=100, offset=0)
    # 最新在前
    assert items[0]["id"] == newer["id"]
    assert items[1]["id"] == middle["id"]
    assert items[2]["id"] == older["id"]


def test_update_decision_patches_allowed_fields():
    """PATCH 仅更新 executed/regret/dialogueHistory；其它字段忽略。"""
    saved = db.save_decision(_make_decision(question="要不要换城市", mode="rational"))
    updated = db.update_decision(
        saved["id"],
        {
            "executed": True,
            "regret": True,
            "dialogueHistory": [{"role": "user", "text": "我决定换"}],
            # 试图改 question，应被忽略
            "question": "已被篡改",
        },
    )
    assert updated is not None
    assert updated["executed"] is True
    assert updated["regret"] is True
    assert updated["dialogueHistory"] == [{"role": "user", "text": "我决定换"}]
    # question 未被修改
    assert updated["question"] == "要不要换城市"


def test_update_decision_returns_none_for_missing_id():
    """不存在的 id 更新返回 None。"""
    assert db.update_decision("missing_id_xxx", {"executed": True}) is None


def test_delete_decision():
    """删除后 get 返回 None，重复删除返回 False。"""
    saved = db.save_decision(_make_decision(question="删除测试"))
    assert db.delete_decision(saved["id"]) is True
    assert db.get_decision(saved["id"]) is None
    # 再次删除应返回 False
    assert db.delete_decision(saved["id"]) is False


def test_count_decisions():
    """计数随保存而增加。"""
    assert db.count_decisions() == 0
    db.save_decision(_make_decision(question="计数1"))
    assert db.count_decisions() == 1
    db.save_decision(_make_decision(question="计数2"))
    db.save_decision(_make_decision(question="计数3"))
    assert db.count_decisions() == 3


def test_config_crud():
    """config 表的 set/get/get_all/delete 闭环。"""
    # 初始 get 返回默认值
    assert db.get_config_value("llm_api_key", default="") == ""

    # set 后能 get 到
    db.set_config_value("llm_api_key", "sk-test-xxx")
    assert db.get_config_value("llm_api_key") == "sk-test-xxx"

    # 支持 dict 等结构化值
    db.set_config_value("preferences", {"language": "zh-CN", "theme": "dark"})
    assert db.get_config_value("preferences") == {"language": "zh-CN", "theme": "dark"}

    # get_all 能拿到
    all_cfg = db.get_all_config()
    assert all_cfg["llm_api_key"] == "sk-test-xxx"
    assert all_cfg["preferences"]["language"] == "zh-CN"

    # delete 后再 get 返回默认
    db.delete_config_value("llm_api_key")
    assert db.get_config_value("llm_api_key", default="<deleted>") == "<deleted>"


# ─── 旧库迁移 _migrate_legacy_db（v0.10.0 更名）─────────────────────────────

import json  # noqa: E402
from pathlib import Path  # noqa: E402


def _make_legacy_db(
    path: Path,
    decisions: int = 2,
    with_config: bool = False,
    config=None,
) -> None:
    """构造与旧版 choice.db 同构的库：decisions 若干行 + 可选 config。"""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE decisions (
            id TEXT PRIMARY KEY, question TEXT NOT NULL, mode TEXT NOT NULL,
            result TEXT NOT NULL, brief TEXT, created_at TEXT NOT NULL,
            executed INTEGER DEFAULT 0, regret INTEGER DEFAULT 0, dialogue_history TEXT)"""
    )
    conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for i in range(decisions):
        conn.execute(
            "INSERT INTO decisions VALUES (?, ?, 'rational', ?, NULL, ?, 0, 0, NULL)",
            (f"legacy_{i}", f"旧库问题{i}", json.dumps({"type": "rational"}), f"2024-01-0{i + 1}T00:00:00"),
        )
    values = dict(config or {})
    if with_config:
        values.setdefault("llm_api_key", "sk-legacy")
    for key, value in values.items():
        conn.execute("INSERT INTO config VALUES (?, ?)", (key, json.dumps(value)))
    conn.commit()
    conn.close()


def _legacy_path() -> Path:
    """旧库路径与 _migrate_legacy_db 的探测逻辑一致：DB_PATH 同目录 choice.db。"""
    return db.DB_PATH.parent / "choice.db"


def test_migration_adopts_legacy_when_new_db_missing(tmp_path, monkeypatch):
    """场景a：新库文件不存在 → 整体接管旧库（含 config），旧库标记为 migrated。"""
    target = tmp_path / "decision_journal.db"
    monkeypatch.setattr(db, "DB_PATH", target)
    _make_legacy_db(_legacy_path(), decisions=2, with_config=True)

    db._migrate_legacy_db()

    assert db.count_decisions() == 2
    assert db.get_config_value("llm_api_key") == "sk-legacy"
    assert not _legacy_path().exists()
    assert (tmp_path / "choice.db.migrated").exists()


def test_migration_skips_when_new_db_has_decisions(tmp_path, monkeypatch):
    """场景b：新库已有决策 → 不覆盖、不动旧库、不打标记。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "decision_journal.db")
    db.init_db()
    mine = db.save_decision(_make_decision(question="新库自己的决策"))
    _make_legacy_db(_legacy_path(), decisions=2)

    db._migrate_legacy_db()

    assert db.count_decisions() == 1
    assert db.get_decision(mine["id"])["question"] == "新库自己的决策"
    assert _legacy_path().exists()
    assert not (tmp_path / "choice.db.migrated").exists()


def test_migration_merges_when_only_config_has_data(tmp_path, monkeypatch):
    """场景c：新库 decisions 为空但 config 已有 Key → 补旧决策，且不覆盖新 Key。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "decision_journal.db")
    db.init_db()
    db.set_config_value("llm_api_key", "sk-keep-me")
    db.set_config_value("preferences", {"language": "en"})
    _make_legacy_db(
        _legacy_path(),
        decisions=2,
        config={"llm_api_key": "sk-legacy", "weather_key": "amap-legacy"},
    )

    db._migrate_legacy_db()

    assert db.count_decisions() == 2
    # 新库已有值优先；旧库缺失的键补进来
    assert db.get_config_value("llm_api_key") == "sk-keep-me"
    assert db.get_config_value("weather_key") == "amap-legacy"
    assert db.get_config_value("preferences") == {"language": "en"}
    assert not _legacy_path().exists()
    assert (tmp_path / "choice.db.migrated").exists()


def test_migration_noop_without_legacy(tmp_path, monkeypatch):
    """场景d：没有旧库 → 什么都不发生，新库照常可用。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "decision_journal.db")
    db.init_db()
    assert not _legacy_path().exists()

    db._migrate_legacy_db()

    assert db.count_decisions() == 0
    assert not (tmp_path / "choice.db.migrated").exists()
    db.save_decision(_make_decision(question="迁移后照常写入"))
    assert db.count_decisions() == 1


def test_migration_does_not_repeat_after_marker(tmp_path, monkeypatch):
    """场景e：迁移成功后旧库已改名，重复调用不再有副作用。"""
    target = tmp_path / "decision_journal.db"
    monkeypatch.setattr(db, "DB_PATH", target)
    _make_legacy_db(_legacy_path(), decisions=1)

    db._migrate_legacy_db()
    db._migrate_legacy_db()  # 第二次：旧库已改名，应直接跳过

    assert db.count_decisions() == 1
    assert not _legacy_path().exists()


def test_migration_reads_uncheckpointed_wal_when_new_db_exists(tmp_path, monkeypatch):
    """场景f：旧库 WAL 尚未 checkpoint 时，已存在的新库也能读到已提交数据。"""
    target = tmp_path / "decision_journal.db"
    monkeypatch.setattr(db, "DB_PATH", target)
    db.init_db()
    legacy = _legacy_path()
    conn = sqlite3.connect(str(legacy))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute(
        """CREATE TABLE decisions (
            id TEXT PRIMARY KEY, question TEXT NOT NULL, mode TEXT NOT NULL,
            result TEXT NOT NULL, brief TEXT, created_at TEXT NOT NULL,
            executed INTEGER DEFAULT 0, regret INTEGER DEFAULT 0, dialogue_history TEXT)"""
    )
    conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO decisions VALUES ('wal_1', 'WAL 问题', 'rational', '{}', NULL, '2024-01-01T00:00:00', 0, 0, NULL)"
    )
    conn.commit()  # 不关闭连接：数据此时仍在 -wal 中

    db._migrate_legacy_db()
    conn.close()

    assert db.count_decisions() == 1
    assert db.get_decision("wal_1")["question"] == "WAL 问题"


def test_migration_survives_corrupt_legacy(tmp_path, monkeypatch):
    """场景g：旧库是损坏文件 → 不崩溃、不标记、新库仍可正常初始化。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "decision_journal.db")
    _legacy_path().write_bytes(b"this is not a sqlite database")

    db._migrate_legacy_db()
    db.init_db()

    assert db.count_decisions() == 0
    assert _legacy_path().exists()
    assert not (tmp_path / "choice.db.migrated").exists()
    db.save_decision(_make_decision(question="损坏旧库之后仍可写入"))
    assert db.count_decisions() == 1


def test_migration_ignores_unrelated_sqlite_file(tmp_path, monkeypatch):
    """场景h：同名 choice.db 但没有 decisions 表 → 不当成本项目数据接管。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "decision_journal.db")
    conn = sqlite3.connect(str(_legacy_path()))
    conn.execute("CREATE TABLE notes (body TEXT)")
    conn.execute("INSERT INTO notes VALUES ('not ours')")
    conn.commit()
    conn.close()

    db._migrate_legacy_db()
    db.init_db()

    assert _legacy_path().exists()
    assert not (tmp_path / "choice.db.migrated").exists()
    assert db.count_decisions() == 0


def test_migration_rolls_back_when_backup_fails(tmp_path, monkeypatch):
    """场景i：backup 失败 → 新库不留下半份数据，旧库不动，下次仍可重试。"""
    target = tmp_path / "decision_journal.db"
    monkeypatch.setattr(db, "DB_PATH", target)
    _make_legacy_db(_legacy_path(), decisions=2)

    real_backup = db._backup_legacy

    def fail_backup(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated backup failure")

    monkeypatch.setattr(db, "_backup_legacy", fail_backup)
    db._migrate_legacy_db()

    # 失败后目标文件可能已被 sqlite3.connect 创建，但不应有半份决策数据
    with sqlite3.connect(str(target)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "decisions" in tables:
            assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert _legacy_path().exists()
    assert not (tmp_path / "choice.db.migrated").exists()

    # 恢复后重新启动：本次不再注入失败，迁移应成功
    monkeypatch.setattr(db, "_backup_legacy", real_backup)
    db.init_db()
    assert db.count_decisions() == 2
    assert (tmp_path / "choice.db.migrated").exists()


def test_migration_idempotent_when_marker_rename_fails(tmp_path, monkeypatch):
    """场景j：数据复制成功但改名失败 → 重复调用不重复插入，也不丢数据。"""
    target = tmp_path / "decision_journal.db"
    monkeypatch.setattr(db, "DB_PATH", target)
    _make_legacy_db(_legacy_path(), decisions=2, with_config=True)

    original_rename = Path.rename

    def fail_choice_rename(self, destination):
        if self.name == "choice.db":
            raise OSError("simulated rename failure")
        return original_rename(self, destination)

    monkeypatch.setattr(Path, "rename", fail_choice_rename)
    db._migrate_legacy_db()
    db._migrate_legacy_db()

    assert db.count_decisions() == 2
    assert db.get_config_value("llm_api_key") == "sk-legacy"
    assert _legacy_path().exists()
    assert not (tmp_path / "choice.db.migrated").exists()


def test_migration_accepts_old_schema_without_optional_columns(tmp_path, monkeypatch):
    """场景k：旧库 decisions 缺 brief/executed/regret/dialogue 列 → 仍能迁移核心字段。"""
    target = tmp_path / "decision_journal.db"
    monkeypatch.setattr(db, "DB_PATH", target)
    db.init_db()
    legacy = _legacy_path()
    conn = sqlite3.connect(str(legacy))
    conn.execute(
        """CREATE TABLE decisions (
            id TEXT PRIMARY KEY, question TEXT NOT NULL, mode TEXT NOT NULL,
            result TEXT NOT NULL, created_at TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO decisions VALUES ('old_1', '旧 schema', 'random', '{}', '2024-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    db._migrate_legacy_db()

    assert db.count_decisions() == 1
    assert db.get_decision("old_1")["question"] == "旧 schema"


def test_migration_concurrent_calls_do_not_duplicate(tmp_path, monkeypatch):
    """场景l：两个实例并发启动 → 串行合并，不重复、不覆盖新配置。"""
    import threading

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "decision_journal.db")
    db.init_db()
    db.set_config_value("llm_api_key", "sk-keep-me")
    _make_legacy_db(_legacy_path(), decisions=2, with_config=True, config={"weather_key": "amap-legacy"})

    barrier = threading.Barrier(2)
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            db._migrate_legacy_db()
        except Exception as exc:  # pragma: no cover - 失败时给断言提供线索
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert db.count_decisions() == 2
    assert db.get_config_value("llm_api_key") == "sk-keep-me"
    assert db.get_config_value("weather_key") == "amap-legacy"
