"""SQLite 持久化层。

替代 HTML 版本的 IndexedDB，提供 decisions 和 config 两张表。
单文件 SQLite，无需服务器，适合本地单机工具。

表结构：
  - decisions: 决策记录（id/question/mode/result/brief/createdAt/executed/regret/dialogueHistory）
  - config: 配置项（key/value），存储 LLM/天气 API Key 和用户偏好
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# SQLite 数据库文件路径（与 backend/ 同级，gitignore 已忽略 *.db）
# 可用环境变量 DECISION_JOURNAL_DB_PATH 覆盖（测试/多实例隔离用）
DB_PATH = Path(os.environ.get("DECISION_JOURNAL_DB_PATH") or (Path(__file__).parent / "decision_journal.db"))


def get_conn() -> sqlite3.Connection:
    """获取 SQLite 连接。每次调用创建新连接，避免线程问题。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """表是否存在（不依赖 sqlite_master 的大小写）。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    """表行数；表不存在时返回 0。"""
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row[0]) if row else 0


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """创建 decisions / config 表与索引（幂等）。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id TEXT PRIMARY KEY,
            question TEXT NOT NULL,
            mode TEXT NOT NULL,
            result TEXT NOT NULL,
            brief TEXT,
            created_at TEXT NOT NULL,
            executed INTEGER DEFAULT 0,
            regret INTEGER DEFAULT 0,
            dialogue_history TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions(created_at);
        CREATE INDEX IF NOT EXISTS idx_decisions_mode ON decisions(mode);

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


# 当前 schema 的 decisions 全部列；旧库缺任何一列时不走整体 backup，
# 而是先建新 schema 再按列合并，避免复制出一个后续写入会失败的老表。
_DECISION_COLUMNS = {
    "id", "question", "mode", "result", "brief", "created_at",
    "executed", "regret", "dialogue_history",
}
_REQUIRED_DECISION_COLUMNS = {"id", "question", "mode", "result", "created_at"}


def _decision_columns(conn: sqlite3.Connection) -> set:
    return {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(decisions)").fetchall()
    }


def _legacy_decision_rows(conn: sqlite3.Connection) -> list:
    """读取旧库 decisions 行；兼容缺少可选列的老库，跳过不可用的行。

    返回顺序与 decisions 表列顺序一致：
    (id, question, mode, result, brief, created_at, executed, regret, dialogue_history)
    """
    columns = _decision_columns(conn)
    if not _REQUIRED_DECISION_COLUMNS.issubset(columns):
        return []

    def col(name: str, default: str) -> str:
        return name if name in columns else default

    query = (
        "SELECT "
        + ", ".join(
            (
                col("id", "NULL"),
                col("question", "NULL"),
                col("mode", "NULL"),
                col("result", "'{}'"),
                col("brief", "NULL"),
                col("created_at", "NULL"),
                col("executed", "0"),
                col("regret", "0"),
                col("dialogue_history", "NULL"),
            )
        )
        + " FROM decisions"
    )
    rows = []
    for row in conn.execute(query).fetchall():
        if not row[0] or row[1] is None or not row[2] or row[3] is None or not row[5]:
            continue
        rows.append(tuple(row))
    return rows


def _backup_legacy(legacy_conn: sqlite3.Connection, new_conn: sqlite3.Connection) -> None:
    """用 SQLite backup API 整体复制（含未 checkpoint 的 WAL 数据）。"""
    legacy_conn.backup(new_conn)
    new_conn.commit()


def _merge_legacy_into_new(legacy_conn: sqlite3.Connection, new_conn: sqlite3.Connection) -> bool:
    """把旧库 decisions/config 合并进新库，不覆盖新库已有配置。

    - BEGIN IMMEDIATE 让并发启动的实例串行化；进入事务后重新检查新库是否已有决策；
    - decisions 用 INSERT OR IGNORE，按主键幂等；
    - config 只补新库缺失的键，绝不覆盖已有 Key/偏好；
    - 单事务提交，失败整体回滚。
    """
    _ensure_schema(new_conn)
    new_conn.execute("BEGIN IMMEDIATE")
    try:
        if _table_count(new_conn, "decisions") > 0:
            new_conn.rollback()
            return False
        for row in _legacy_decision_rows(legacy_conn):
            new_conn.execute(
                """
                INSERT OR IGNORE INTO decisions
                    (id, question, mode, result, brief, created_at, executed, regret, dialogue_history)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        if _table_exists(legacy_conn, "config"):
            for row in legacy_conn.execute("SELECT key, value FROM config").fetchall():
                if row[0] is None or row[1] is None:
                    continue
                new_conn.execute(
                    "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                    (row[0], row[1]),
                )
        new_conn.commit()
        return True
    except Exception:
        new_conn.rollback()
        raise


def _migrate_legacy_db() -> None:
    """v0.10.0 更名迁移：把旧 choice.db 的数据接管到 decision_journal.db。

    安全边界：
    - 旧库不存在、已标记 migrated、不是本项目的库（没有 decisions 表）时不动；
    - 新库已有决策时绝不覆盖、也不打标记，保留旧库供用户手动检查；
    - 新库文件全新且为空时用 backup API 整体复制（连同 WAL）；
    - 新库已存在但为空、或只含配置时，事务内只补 decisions 与缺失的 config 键，
      保留新库已有 Key/偏好与未知表，并让并发启动串行化；
    - 成功后把旧库改名为 choice.db.migrated；改名失败保留旧库，重复调用幂等；
    - 任何失败静默跳过并回滚，不阻塞启动。
    """
    legacy = DB_PATH.parent / "choice.db"  # 跟随 DB_PATH 所在目录（测试隔离时指向临时目录）
    marker = DB_PATH.parent / "choice.db.migrated"
    if not legacy.exists() or marker.exists():
        return

    new_existed = DB_PATH.exists()
    legacy_conn = None
    new_conn = None
    adopted = False
    try:
        legacy_conn = sqlite3.connect(str(legacy))
        # 只接管本项目的旧库；别的同名 SQLite 文件原样保留。
        if not _table_exists(legacy_conn, "decisions"):
            return
        new_conn = sqlite3.connect(str(DB_PATH))
        new_decisions = _table_count(new_conn, "decisions")
        new_config = _table_count(new_conn, "config")

        if new_decisions > 0:
            return  # 新库已有决策：不覆盖、不标记、不动旧库
        legacy_has_full_schema = _DECISION_COLUMNS.issubset(_decision_columns(legacy_conn))
        if not new_existed and new_config == 0 and legacy_has_full_schema:
            # 全新数据库且旧 schema 完整：整体复制最完整，backup API 会带上 WAL。
            _backup_legacy(legacy_conn, new_conn)
            adopted = True
        else:
            # 已存在/仅配置/旧 schema 缺列：新建 schema 后幂等合并，保留新库配置。
            adopted = _merge_legacy_into_new(legacy_conn, new_conn)
    except (sqlite3.Error, OSError):
        adopted = False
    finally:
        for conn in (legacy_conn, new_conn):
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

    if adopted:
        try:
            legacy.rename(marker)
        except OSError:
            # 改名失败不影响已提交的数据；下次启动会按幂等路径重试或跳过。
            pass


def init_db() -> None:
    """初始化数据库表。应用启动时调用一次。"""
    _migrate_legacy_db()
    with get_conn() as conn:
        _ensure_schema(conn)


# ─── decisions 表操作 ───────────────────────────────────────────


def save_decision(dec: dict) -> dict:
    """新增/更新一条决策记录。返回传入的 dec（补齐默认值）。"""
    if not dec.get("id"):
        dec["id"] = f"dec_{datetime.now().strftime('%Y%m%d%H%M%S')}_{_random_suffix()}"
    if not dec.get("createdAt"):
        dec["createdAt"] = datetime.now().isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO decisions
                (id, question, mode, result, brief, created_at, executed, regret, dialogue_history)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dec["id"],
                dec["question"],
                dec["mode"],
                json.dumps(dec.get("result", {}), ensure_ascii=False),
                json.dumps(dec.get("brief"), ensure_ascii=False) if dec.get("brief") else None,
                dec["createdAt"],
                1 if dec.get("executed") else 0,
                1 if dec.get("regret") else 0,
                json.dumps(dec.get("dialogueHistory"), ensure_ascii=False) if dec.get("dialogueHistory") else None,
            ),
        )
    return dec


def get_decision(decision_id: str) -> Optional[dict]:
    """按 id 查询单条决策记录。"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _row_to_decision(row) if row else None


def list_decisions(limit: int = 100, offset: int = 0) -> list[dict]:
    """查询决策历史列表（按 createdAt 降序）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_decision(r) for r in rows]


def update_decision(decision_id: str, patches: dict) -> Optional[dict]:
    """部分更新决策记录（仅 executed/regret/dialogueHistory 可更新）。"""
    allowed = {"executed", "regret", "dialogueHistory"}
    updates = {k: v for k, v in patches.items() if k in allowed}
    if not updates:
        return get_decision(decision_id)

    sets = []
    vals = []
    if "executed" in updates:
        sets.append("executed = ?")
        vals.append(1 if updates["executed"] else 0)
    if "regret" in updates:
        sets.append("regret = ?")
        vals.append(1 if updates["regret"] else 0)
    if "dialogueHistory" in updates:
        sets.append("dialogue_history = ?")
        vals.append(json.dumps(updates["dialogueHistory"], ensure_ascii=False))

    vals.append(decision_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE decisions SET {', '.join(sets)} WHERE id = ?", vals)
    return get_decision(decision_id)


def delete_decision(decision_id: str) -> bool:
    """删除一条决策记录。返回是否删除成功。"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM decisions WHERE id = ?", (decision_id,))
        return cur.rowcount > 0


def count_decisions() -> int:
    """决策总数。"""
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM decisions").fetchone()
    return row["n"]


# ─── config 表操作 ──────────────────────────────────────────────


def get_config_value(key: str, default: Any = None) -> Any:
    """读取单个配置项。"""
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_config_value(key: str, value: Any) -> None:
    """写入单个配置项（upsert）。"""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def get_all_config() -> dict:
    """读取全部配置项，返回 {key: value} 字典。"""
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            out[r["key"]] = r["value"]
    return out


def delete_config_value(key: str) -> None:
    """删除单个配置项。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM config WHERE key = ?", (key,))


# ─── 辅助函数 ───────────────────────────────────────────────────


def _row_to_decision(row: sqlite3.Row) -> dict:
    """把数据库行转换为 Decision dict。"""
    out = {
        "id": row["id"],
        "question": row["question"],
        "mode": row["mode"],
        "result": json.loads(row["result"]) if row["result"] else {},
        "createdAt": row["created_at"],
        "executed": bool(row["executed"]),
        "regret": bool(row["regret"]),
    }
    if row["brief"]:
        out["brief"] = json.loads(row["brief"])
    if row["dialogue_history"]:
        out["dialogueHistory"] = json.loads(row["dialogue_history"])
    return out


def _random_suffix() -> str:
    """6 位随机后缀（base36）。"""
    import random
    import string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
