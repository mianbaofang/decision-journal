# 本地数据、升级与恢复 / Local data, upgrades and recovery

> 当前源码的未发布改进；线上 v0.10.1 安装包仍使用旧路径。以下命令在项目根目录 / 安装后的 Skill 根目录运行，使用同一个 Python 环境。

## 数据位置

默认 SQLite 数据库现在是 `~/.decision-journal/decision_journal.db`，与安装目录分离。在 Windows 上，`~` 是当前 Windows 用户的主目录。源码、pip 安装与安装后的 Skill 使用同一个用户数据库；替换程序文件不会替换日志。

数据库路径优先级（启动时解析）为：

1. `DECISION_JOURNAL_DB_PATH`：明确指定数据库文件。
2. `DECISION_JOURNAL_DATA_DIR`：指定目录，数据库名仍为 `decision_journal.db`。
3. `~/.decision-journal/decision_journal.db`。

这些变量只控制数据库及默认备份目录；不会自动重定向 `config.json`、后端日志或环境变量。`config.json`、日志的原有覆盖变量保持兼容。不同端口默认仍共享一个用户数据库；需要隔离时必须显式配置不同数据库路径。

```bash
python scripts/decision_journal.py data status
```

`status` 是只读的，显示路径、条数、待迁移旧库与配置文件位置；不输出记录正文或 Key，也不创建空库。`data` 命令仅用 Python 标准库，不启动后端、不调用模型，无需先安装 httpx。它们操作的是本机配置路径，不受 HTTP `--base-url` 控制；远程工作台的数据需在该主机维护。

## 从旧版本升级

**第一次从旧版迁出时，如果安装器会删除旧目录，务必在重装之前用当前源码执行 `data migrate --from 旧库路径`，或先把旧库及 WAL 一致地备份到安装目录之外。已经删除的旧库无法由新版自动找回。**

先停止仍在运行的旧版后端。新版启动时，仅在没有路径覆盖、当前包的 `backend/` 下只有一个旧库、用户库为空时，自动导入 `decision_journal.db` 或 `choice.db`。迁移保留旧文件，完整保留当前支持的决策、复盘与 SQLite 配置，并记录来源，避免重启后把已删除的记录重新导入。

如果检测到多个旧库，新版会要求明确选择；如果用户库已有数据，保留两边，给出待迁移提示。路径覆盖不会偷偷接管包内数据。其他安装位置、显式配置数据目录的用户使用：

```bash
python scripts/decision_journal.py data migrate --from /path/to/old/backend/decision_journal.db
# 目标已有数据/设置时，先检查两边，再明确允许合并：
python scripts/decision_journal.py data migrate --from /path/to/old/backend/decision_journal.db --merge
```

迁移只补新记录、保留目标已有设置。同一 ID 内容不同会拒绝整次导入；不会猜哪一份正确。已有数据的合并会先生成 `before-merge-*.sqlite3` 备份。损坏文件、未知的新版本字段或不完整数据会失败，保留原件。

只自动发现当前安装目录内的旧库；不扫描整台电脑。旧后端如果继续向旧库写入，迁移快照之后的新内容不会自动同步，所以应先停旧服务。旧文件不会自动清理；确认新版记录齐全后再自行归档旧安装。

## SQLite 备份

```bash
python scripts/decision_journal.py data backup
python scripts/decision_journal.py data backup --output /path/to/backups/my-journal.sqlite3
```

默认放在数据库旁的 `backups/`，文件名带时间和随机后缀；已有备份不会被覆盖。使用 SQLite snapshot API，包含尚未 checkpoint 的 WAL 数据，不是简单复制主文件。备份成功前不会发布最终文件名。

备份是整个 SQLite 快照，包括决策、计划/复盘、SQLite 设置与迁移历史；可能含用户在库中保存的 API Key，属于敏感本地文件，未加密。新建文件使用限制性权限（POSIX 0600；Windows 仍取决于父目录 ACL）。不要提交或公开分享。打包与同步工具排除 SQLite、WAL、备份目录和 `.db.migrated` 文件；开发同步若发现目标包目录仍有旧数据，会先拒绝替换，要求先迁出数据。

**不包含**独立的 `~/.decision-journal/config.json`、环境变量、导出文件或日志。需要迁到另一台机器时另行安全保存配置文件；环境变量在新机器重新配置。本功能没有自动备份计划、云同步或自动清理策略。

## 恢复与回退

```bash
# 新建/空目标可以直接恢复：
python scripts/decision_journal.py data restore --from /path/to/backups/my-journal.sqlite3
# 目标已有记录或设置时，必须明确允许替换：
python scripts/decision_journal.py data restore --from /path/to/backups/my-journal.sqlite3 --replace
```

恢复先验证源库；已有内容被替换前，自动生成 `before-restore-*.sqlite3`。恢复在 SQLite 写事务中完成，保留目标中的其他非应用表和迁移标记，不替换正在使用的数据库文件；验证失败会回滚。正常后端写入与恢复通过 SQLite 写锁串行化。为避免误操作，恢复前仍建议停止工作台，恢复后刷新页面。

若需要撤销恢复，对命令返回的 `safety_backup` 路径再次执行 `data restore --from ... --replace`。源备份始终保留，恢复前备份也不会自动删除。恢复只应用本版本支持的决策/SQLite 设置/迁移历史，不执行源库中的任意 SQL 或复制未知业务表。

恢复后的旧 Key/设置可能与当前环境变量或 `config.json` 不同；原有配置优先级仍然生效，不能把“恢复 SQLite”误认为“替换所有配置”。

## English quick reference

The unreleased source stores SQLite at `~/.decision-journal/decision_journal.db`, outside the installation. Path precedence: `DECISION_JOURNAL_DB_PATH` → `DECISION_JOURNAL_DATA_DIR` → user-home default. Explicit path overrides disable automatic adoption. All four `data` commands run offline with the standard library and operate on local storage, not a remote HTTP backend.

- `data status`: read-only path/count/pending-migration inspection; no records or secret values printed.
- `data migrate --from PATH [--merge]`: preserve the source; import missing records and keep existing destination settings; conflicting IDs fail atomically. Existing data is backed up before merging. Stop the old backend first. If an installer removes the old directory, migrate or consistently back up its database outside that directory **before** reinstalling; already deleted legacy data cannot be recovered by automatic adoption.
- `data backup [--output PATH]`: consistent SQLite snapshot including WAL. Refuses an existing destination. Backups may contain credentials, are unencrypted and must stay private.
- `data restore --from PATH [--replace]`: validate first, require `--replace` for a populated destination, save a pre-restore safety backup, apply supported app rows in one transaction and verify before commit. Restore the returned safety backup to undo.

Automatic migration discovers only the running installation's `backend/` database, requires an unambiguous source and empty user destination, and keeps durable provenance to avoid resurrecting deleted records. An explicitly chosen path, a populated destination, corrupt data or multiple sources never silently overwrite user data. New installations under the same home reuse the same default database.

Backups do **not** include `config.json`, environment variables, logs or exports. Restore preserves unrelated destination tables and existing migration history; it does not import unknown business tables. There is no scheduled backup, remote sync, encryption or retention policy. Before release, cross-platform runtime verification is still needed; this change was exercised on Windows.
