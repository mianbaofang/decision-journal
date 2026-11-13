"""CLI 安全边界与输出回归测试（不启动真实后端、不触碰真实 DB）。"""

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.decision_journal as cli  # noqa: E402


def _args(**overrides):
    values = {
        "question": "今晚吃什么",
        "mode": "random",
        "save_config": False,
        "demo": False,
        "api_key": None,
        "llm_model": None,
        "llm_base_url": None,
        "weather_key": None,
        "weather_base_url": None,
        "weather_appsecret": None,
        "weather_city": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


# ─── base_url 解析与自动启动边界 ─────────────────────────────


def test_parse_local_target_accepts_loopback_only():
    assert cli._parse_local_target("http://127.0.0.1:8123") == ("http", "127.0.0.1", 8123)
    assert cli._parse_local_target("http://localhost:8010") == ("http", "localhost", 8010)
    assert cli._parse_local_target("http://[::1]:9000") == ("http", "::1", 9000)
    assert cli._parse_local_target("https://example.com") is None
    assert cli._parse_local_target("http://192.168.1.10:8010") is None
    assert cli._parse_local_target("file:///tmp/x") is None


def test_command_line_matcher_requires_full_project_path():
    target = cli._backend_main()
    assert target is not None
    assert cli._command_line_runs_backend(
        f'"C:\\Python\\python.exe" "{target}"', target
    )
    assert not cli._command_line_runs_backend(
        '"C:\\Python\\python.exe" "E:\\Other\\backend\\main.py"', target
    )
    assert not cli._command_line_runs_backend("python backend/main.py", target)
    assert not cli._command_line_runs_backend(f'"{target}.bak"', target)


def test_ensure_backend_remote_does_not_spawn_local_process(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_health_ok", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: calls.append(args))

    assert cli.ensure_backend("https://remote.example.com:8010") is False
    assert calls == []


def test_ensure_backend_local_non_default_port_passes_host_and_port(tmp_path, monkeypatch):
    calls = []
    health = iter([False, True])

    class FakeProc:
        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProc()

    monkeypatch.setattr(cli, "_health_ok", lambda *_args, **_kwargs: next(health))
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "BACKEND_LOG", tmp_path / "backend.log")

    assert cli.ensure_backend("http://127.0.0.1:8123", timeout=1.0) is True
    assert len(calls) == 1
    command = calls[0][0]
    assert command[1] == str(cli._backend_main())
    assert command[2:6] == ["--host", "127.0.0.1", "--port", "8123"]


def test_ensure_backend_disabled_does_not_spawn(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_health_ok", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *args, **kwargs: calls.append(args))

    assert cli.ensure_backend("http://127.0.0.1:8123", enabled=False) is False
    assert calls == []


# ─── stop 的进程定位 ────────────────────────────────────────


def test_windows_finder_uses_port_and_exact_project_path(monkeypatch):
    """Windows：不能只看 backend/main.py 子串；必须核对绝对路径与端口。"""
    from types import SimpleNamespace

    target = cli._backend_main()
    captured = {}
    other_pid = 4321

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(
            stdout=(
                f"{other_pid}\t\"C:\\Python\\python.exe\" \"E:\\Other\\backend\\main.py\"\n"
                f"{other_pid + 1}\t\"C:\\Python\\python.exe\" \"{target}\"\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    pids = cli._find_backend_pids_windows(8123, target)

    assert pids == [other_pid + 1]
    # 空 State 的 Windows 上 -State Listen 会漏掉真实监听者
    assert "-State Listen" not in captured["command"][-1]


def test_cmd_stop_rejects_remote_url(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_find_backend_pids", lambda *args: called.append(args) or [])
    assert cli.cmd_stop_backend("http://remote.example.com:8010") == 2
    assert called == []


def test_cmd_stop_refuses_when_port_owned_by_other_process(monkeypatch):
    monkeypatch.setattr(cli, "_find_backend_pids", lambda *_args: [])
    monkeypatch.setattr(cli, "_health_ok", lambda *_args, **_kwargs: True)
    assert cli.cmd_stop_backend("http://127.0.0.1:8123") == 3


def test_cmd_stop_reports_not_running_when_port_closed(monkeypatch):
    monkeypatch.setattr(cli, "_find_backend_pids", lambda *_args: [])
    monkeypatch.setattr(cli, "_health_ok", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "_port_open", lambda *_args, **_kwargs: False)
    assert cli.cmd_stop_backend("http://127.0.0.1:8123") == 0


def test_cmd_stop_kills_hung_verified_backend(monkeypatch):
    """健康检查失败（悬挂）时，只要端口+绝对路径核对通过仍应停止。"""
    results = iter([[1234], []])
    killed = []
    monkeypatch.setattr(cli, "_find_backend_pids", lambda *_args: next(results))
    monkeypatch.setattr(cli, "_health_ok", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(cli, "_kill_pids", lambda pids: killed.extend(pids))

    assert cli.cmd_stop_backend("http://127.0.0.1:8123") == 0
    assert killed == [1234]


def test_posix_lookup_uses_port_owners_and_exact_path(monkeypatch):
    target = cli._backend_main()
    monkeypatch.setattr(cli, "_listening_pids_posix", lambda port: [111, 222])
    monkeypatch.setattr(
        cli, "_pid_runs_backend_posix", lambda pid, path: pid == 222 and path == target
    )
    assert cli._find_backend_pids_posix(8123, target) == [222]


def test_posix_lookup_returns_empty_when_port_ownership_unknown(monkeypatch):
    monkeypatch.setattr(cli, "_listening_pids_posix", lambda port: None)
    assert cli._find_backend_pids_posix(8123, cli._backend_main()) == []


# ─── 错误脱敏与 CLI 输出 ────────────────────────────────────


def test_redact_secrets_masks_values_and_common_patterns():
    text = '{"apiKey":"sk-live-abcdef","other":"Bearer abcdef123"}'
    redacted = cli._redact_secrets(text, ["sk-live-abcdef"])
    assert "sk-live-abcdef" not in redacted
    assert "abcdef123" not in redacted
    assert "***" in redacted


def test_secret_values_reads_args_and_env(monkeypatch):
    monkeypatch.setenv("DECISION_JOURNAL_LLM_API_KEY", "sk-env-secret")
    values = cli._secret_values(_args(api_key="sk-arg-secret", weather_key="amap-secret"))
    assert "sk-arg-secret" in values
    assert "amap-secret" in values
    assert "sk-env-secret" in values


def test_cmd_chat_demo_flag_and_mock_source_output(capsys):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "mode": "random",
                "reply": "已用 random 模式分析。",
                "result": {"type": "random", "options": ["A", "B"], "_source": "mock"},
                "brief": None,
                "decisionId": "dec_test",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        assert cli.cmd_chat(_args(demo=True), client) == 0

    output = capsys.readouterr().out
    assert captured["demoMode"] is True
    assert '"options"' in output
    assert "结果来源：mock" in output
    assert "dec_test" in output
