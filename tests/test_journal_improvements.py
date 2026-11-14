import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import db
from fastapi.testclient import TestClient
from main import app
from services.decision_score import score


def test_first_migration_does_not_overwrite_another_process(tmp_path, monkeypatch):
    db_path = tmp_path / "new.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    with sqlite3.connect(tmp_path / "choice.db") as conn:
        db._ensure_schema(conn)
        conn.execute("INSERT INTO decisions(id,question,mode,result,created_at) VALUES('old','old','rational','{}','2026-01-01')")
    original = db._backup_legacy
    def interleave(source, destination):
        child = "import sys,pathlib;sys.path.insert(0,sys.argv[1]);import db;db.DB_PATH=pathlib.Path(sys.argv[2]);db.init_db();db.save_decision({'id':'new','question':'new','mode':'rational'});db.set_config_value('preference','keep-me')"
        subprocess.run([sys.executable, "-c", child, str(Path(db.__file__).parent), str(db_path)], check=True, capture_output=True, timeout=30)
        original(source, destination)
    monkeypatch.setattr(db, "_backup_legacy", interleave)
    db.init_db()
    assert {d["id"] for d in db.list_decisions()} == {"old", "new"}
    assert db.get_config_value("preference") == "keep-me"


def test_journal_plan_review_stats_and_export():
    with TestClient(app) as client:
        saved = client.post("/api/decision", json={"question":"Synthetic choice", "mode":"rational", "result":{"source":"host-agent"}}).json()
        path = "/api/decision/" + saved["id"]
        client.patch(path, json={"journal":{"chosenOption":"A", "expectedOutcome":"Learn", "reviewDate":"2026-12-01"}}).raise_for_status()
        stats = client.get("/api/stats").json()
        assert stats["reviewedCount"] == 0 and stats["regretRate"] is None
        assert stats["avgConfidence"] is None and stats["confidenceSampleCount"] == 0
        reviewed = client.patch(path, json={"regret":False, "journal":{"actualOutcome":"Learned", "reviewNote":"Try B next"}}).json()
        assert reviewed["journal"]["chosenOption"] == "A"
        assert reviewed["journal"]["reviewedAt"]
        stats = client.get("/api/stats").json()
        assert stats["reviewedCount"] == 1 and stats["regretRate"] == 0
        md = client.get(path + "/export?format=md").text
        assert "Try B next" in md and "Learned" in md and "2026-12-01" in md
        assert client.get(path + "/export?format=json").json()["journal"] == reviewed["journal"]
        client.post("/api/decision", json={**saved,"question":"Rewritten"}).raise_for_status()
        assert client.get(path).json()["journal"]["reviewNote"] == "Try B next"


def test_heuristics_are_deduplicated_and_do_not_use_today_as_reversibility():
    one = score("Should I quit my job?", {"pros":["More time"],"cons":[]}, None)
    repeated = score("Should I quit my job?", {"pros":["More time", "more time!", " More time "],"cons":[]}, None)
    assert one == repeated
    assert one["risk"] == score("是否辞职", None, None)["risk"]
    assert score("今天是否辞职", None, None)["reversibility"] == score("是否辞职", None, None)["reversibility"]


def test_wrong_named_service_is_rejected(monkeypatch):
    path = Path(__file__).parents[1] / "scripts/decision_journal.py"
    spec = importlib.util.spec_from_file_location("identity_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    class Response:
        status_code = 200
        def json(self): return {"status":"ok", "name":"unrelated local service"}
    monkeypatch.setattr(cli.httpx, "get", lambda *a, **k: Response())
    assert not cli._health_ok("http://127.0.0.1:8123")


def test_review_date_requires_a_real_calendar_date():
    with TestClient(app) as client:
        d = client.post("/api/decision", json={"question":"test", "mode":"rational", "result":{}}).json()
        assert client.patch("/api/decision/" + d["id"], json={"journal":{"reviewDate":"2026-99-99"}}).status_code == 422
    assert score("Should I investigate this issue?", None, None)["risk"] == 42
