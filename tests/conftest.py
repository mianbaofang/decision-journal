"""pytest 共享 fixtures。

- 把 backend/ 加入 sys.path（与 main.py 启动脚本一致）
- 每个测试自动隔离 DB_PATH 与 CONFIG_FILE_PATH，避免污染真实 decision_journal.db 与 ~/.decision-journal/config.json
"""

import sys
from pathlib import Path

# 把 backend/ 目录加入 sys.path（与 backend/main.py 第 22 行的做法一致）
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402

import config as config_mod  # noqa: E402
import db  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_paths(tmp_path, monkeypatch):
    """每个测试用独立临时目录的 DB/config，并清掉真实用户环境里的 Key。

    真实环境若设置了 DECISION_JOURNAL_LLM_API_KEY，route 测试会意外发起真实
    LLM 请求；这里统一删除，确保测试只走合成数据。
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_decision_journal.db")
    monkeypatch.setattr(config_mod, "CONFIG_FILE_PATH", tmp_path / "config.json")
    for env_name in set(config_mod.ENV_KEY_MAP.values()):
        monkeypatch.delenv(env_name, raising=False)
    db.init_db()
    yield
