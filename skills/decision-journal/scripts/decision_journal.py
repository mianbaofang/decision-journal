"""别纠结决策辅助 CLI

通过 HTTP 调用后端 API，支持 chat / archive / stats / decision / export / config-api / stop 动作。
辅助人做选择，不替代人做决定。

Skill 版本特有：用户可自配 LLM API Key + 天气 API Key（iOS/MP 版本由
服务器端管理，用户不接触）。配置来源优先级（与后端 config.py 对齐）：
  本次请求参数 > 环境变量 > SQLite > ~/.decision-journal/config.json
"""

import argparse
import datetime
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

# 后端默认地址
DEFAULT_BASE_URL = "http://127.0.0.1:8010"

# 自动启动后端的等待时间与日志位置
BACKEND_START_TIMEOUT = 20.0
# 测试/便携模式可重定向，避免写入真实用户目录
BACKEND_LOG = Path(
    os.environ.get("DECISION_JOURNAL_BACKEND_LOG")
    or (Path.home() / ".decision-journal" / "backend.log")
)

# 六种决策模式（与后端 modes_data 保持一致）
MODES = ["auto", "rational", "random", "nature", "dialogue", "fengshui"]

# 配置文件路径（与后端 config.py 保持一致；测试可用环境变量隔离）
CONFIG_PATH = Path(
    os.environ.get("DECISION_JOURNAL_CONFIG_FILE")
    or (Path.home() / ".decision-journal" / "config.json")
)

# CLI flag -> 请求体字段名（与后端 ChatRequest 字段对齐）
# 键名与 _collect_cli_config 返回的 config 键名一致
# v0.7.0 起 weather_key（高德 Key）为主字段；weather_appsecret 保留兼容
REQ_FIELD_MAP = {
    "llm_api_key": "apiKey",
    "llm_model": "llmModel",
    "llm_base_url": "llmBaseUrl",
    "weather_key": "weatherKey",
    "weather_base_url": "weatherBaseUrl",
    "weather_appsecret": "weatherAppsecret",  # 兼容旧版，后端读取时自动映射到 weather_key
    "weather_city": "weatherCity",
}


def _print_json(data: Any) -> None:
    """美化打印 JSON。"""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _backend_main() -> Optional[Path]:
    """返回随包分发的 backend/main.py 路径（Skill 包内）。"""
    candidate = Path(__file__).resolve().parents[1] / "backend" / "main.py"
    return candidate if candidate.is_file() else None


def _health_ok(base_url: str, timeout: float = 1.0) -> bool:
    try:
        response = httpx.get(base_url.rstrip("/") + "/api/health", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def ensure_backend(base_url: str, enabled: bool = True, timeout: float = BACKEND_START_TIMEOUT) -> bool:
    """确保后端可用：先探测；仅对 loopback HTTP 地址按需启动本地后端。

    远程主机或非 HTTP 的 base_url 只做健康检查，绝不在本机另起一个 127.0.0.1:8010
    进程。启动时把解析到的 host/port 显式传给 backend/main.py，支持非默认端口。
    """
    if _health_ok(base_url):
        return True
    if not enabled:
        print(
            "后端未运行。请先启动 backend/main.py，或去掉 --no-autostart 让 CLI 自动启动。",
            file=sys.stderr,
        )
        return False

    target = _parse_local_target(base_url)
    if target is None:
        print(
            f"base_url 指向远程主机或非 loopback 地址（{base_url}），CLI 不会自动启动本地后端。"
            "请确认远程服务可用，或改用本机 --base-url。",
            file=sys.stderr,
        )
        return False
    scheme, host, port = target
    if scheme != "http":
        print(
            f"自动启动只支持本机 http base_url，当前为 {base_url}；请手动启动后端。",
            file=sys.stderr,
        )
        return False

    main_py = _backend_main()
    if main_py is None:
        print(
            "找不到随包分发的 backend/main.py；请从完整 Skill 目录运行，或手动启动后端。",
            file=sys.stderr,
        )
        return False

    BACKEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(BACKEND_LOG, "a", encoding="utf-8")
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    log.write(f"[{stamp}] starting backend: {main_py} ({host}:{port})\n")
    log.flush()
    bind_host = "127.0.0.1" if host == "localhost" else host
    command = [sys.executable, str(main_py), "--host", bind_host, "--port", str(port)]
    popen_kwargs = {
        "cwd": str(main_py.parent),
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "env": {**os.environ, "PYTHONUNBUFFERED": "1"},
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        log.write(f"failed to start backend: {type(exc).__name__}\n")
        log.flush()
        print(f"后端启动失败，详见 {BACKEND_LOG}", file=sys.stderr)
        return False
    finally:
        log.close()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health_ok(base_url):
            print(f"已自动启动本地后端（{base_url}）。", file=sys.stderr)
            return True
        if proc.poll() is not None:
            break
        time.sleep(0.4)
    print(f"后端启动失败，详见 {BACKEND_LOG}", file=sys.stderr)
    return False


def _parse_local_target(base_url: str) -> Optional[tuple]:
    """解析 loopback base_url，返回 (scheme, host, port)；远程/非法地址返回 None。"""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(base_url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return None
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return None
    return parts.scheme, host, port


def _parse_local_port(base_url: str) -> Optional[int]:
    """解析本机 loopback 端口；保留给 stop/测试使用。"""
    target = _parse_local_target(base_url)
    return target[2] if target else None


def _normalized_cmd(value: str) -> str:
    """统一路径分隔符与大小写，供命令行匹配使用。"""
    return value.replace("\\", "/").lower()


def _command_line_runs_backend(command_line: str, target: Path) -> bool:
    """命令行是否把本项目 backend/main.py 的绝对路径作为独立参数。

    只认完整绝对路径，不做 `main.py` / `backend` 子串匹配，避免误杀同名项目。
    """
    if not command_line or target is None:
        return False
    target_text = _normalized_cmd(str(target))
    pattern = r'(?:^|[\s"\'])' + re.escape(target_text) + r'(?=$|[\s"\'])'
    return re.search(pattern, _normalized_cmd(command_line)) is not None


def _find_backend_pids_windows(port: int, target: Path) -> list:
    """Windows：只返回同时持有该端口、且命令行含本项目绝对路径的 python 进程。"""
    ps_cmd = (
        # 不用 -State Listen：部分 Windows 版本对监听 socket 返回空 State，
        # 过滤后会把真正的后端漏掉；后续再用 python + 绝对路径核对。
        "$conns = Get-NetTCPConnection -LocalPort " + str(port) + " -ErrorAction SilentlyContinue; "
        "$pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique; "
        "$out = @(); "
        "foreach ($p in $pids) { "
        "  $proc = Get-CimInstance Win32_Process -Filter \"ProcessId = $p\" -ErrorAction SilentlyContinue; "
        "  if ($proc -and $proc.CommandLine -and $proc.ProcessName.ToLower().Contains('python')) { "
        "    $out += (\"{0}`t{1}\" -f $p, $proc.CommandLine) "
        "  } "
        "} "
        "$out -join \"`n\""
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=25,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in out.splitlines():
        pid_text, _, command_line = line.partition("\t")
        if pid_text.strip().isdigit() and _command_line_runs_backend(command_line, target):
            pids.append(int(pid_text.strip()))
    return pids


def _listening_pids_posix(port: int) -> Optional[list]:
    """POSIX：找出监听该端口的 PID；无法确认时返回 None（调用方必须拒绝杀进程）。"""
    lsof_cmd = ["lsof", "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"]
    try:
        result = subprocess.run(lsof_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]
        if result.returncode == 1 and not (result.stderr or "").strip():
            return []  # lsof 正常执行：该端口没有监听者
    except (OSError, subprocess.TimeoutExpired):
        pass

    ss_cmd = ["ss", "-ltnpH", f"sport = :{port}"]
    try:
        result = subprocess.run(ss_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return sorted({int(pid) for pid in re.findall(r"pid=(\d+)", result.stdout)})
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _process_is_python_posix(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "python" in (result.stdout or "").strip().lower()


def _process_command_line_posix(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or "").strip()


def _pid_runs_backend_posix(pid: int, target: Path) -> bool:
    """PID 是否为启动本项目 backend/main.py 的 python 进程。

    优先匹配绝对路径；Linux 上若命令行是相对路径，则结合 /proc/<pid>/cwd 解析。
    """
    if not _process_is_python_posix(pid):
        return False
    args = _process_command_line_posix(pid)
    if not args:
        return False
    if _command_line_runs_backend(args, target):
        return True
    try:
        cwd = Path(os.readlink(f"/proc/{pid}/cwd"))
    except (OSError, ValueError):
        return False
    for token in shlex.split(args):
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            if candidate.resolve() == target.resolve():
                return True
        except OSError:
            continue
    return False


def _find_backend_pids_posix(port: int, target: Path) -> list:
    """POSIX：先确认端口监听者，再逐个核对本项目绝对路径，避免误杀其他实例。"""
    owners = _listening_pids_posix(port)
    if not owners:
        return []
    return [pid for pid in owners if _pid_runs_backend_posix(pid, target)]


def _find_backend_pids(port: int, target: Path) -> list:
    if os.name == "nt":
        return _find_backend_pids_windows(port, target)
    return _find_backend_pids_posix(port, target)


def _port_open(base_url: str, timeout: float = 0.4) -> bool:
    """TCP 层判断端口是否仍有进程监听（用于区分“未运行”和“无法确认”）。"""
    target = _parse_local_target(base_url)
    if target is None:
        return False
    _, host, port = target
    if host == "localhost":
        host = "127.0.0.1"
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_pids(pids: list) -> None:
    for pid in pids:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, text=True, timeout=15,
                )
            else:
                os.kill(pid, 15)  # SIGTERM，给后端优雅退出机会
        except (OSError, subprocess.TimeoutExpired):
            pass
    if os.name != "nt":
        time.sleep(1.0)
        for pid in pids:  # 未退出的补 SIGKILL；先确认 PID 仍存活，降低 PID 复用风险
            if _pid_alive(pid):
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass


def cmd_stop_backend(base_url: str) -> int:
    """停止本机 base_url 端口上的后端进程（--action stop）。

    安全边界：
    - 仅接受 loopback 地址；不对远程主机做任何操作；
    - 命中进程必须同时满足"持有该端口"和"命令行确实是本项目的 backend/main.py"，
      避免误杀同端口的其他 Python 服务、或恰好打开同名文件的编辑器；
    - 健康检查只作辅助提示：后端卡死/启动中时同样可以停止。
    """
    port = _parse_local_port(base_url)
    if port is None:
        print(f"仅支持停止本机 loopback 地址的后端，无法处理：{base_url}", file=sys.stderr)
        return 2
    target = _backend_main()
    if target is None:
        print("找不到随包分发的 backend/main.py，无法确认要停止哪个进程；未做操作。", file=sys.stderr)
        return 2

    pids = _find_backend_pids(port, target)
    if not pids:
        if _health_ok(base_url) or _port_open(base_url):
            print(
                f"端口 {port} 有服务在监听，但无法确认是本项目 {target}；未做操作。",
                file=sys.stderr,
            )
            return 3
        print(f"后端未在运行（{base_url}）。")
        return 0

    _kill_pids(pids)
    if not _find_backend_pids(port, target):
        print(f"已停止本地后端（{base_url}）。")
        return 0
    print(f"未能停止后端；请手动关闭监听 {base_url} 的进程。", file=sys.stderr)
    return 3


def _read_config_file() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_config_file(partial: dict) -> dict:
    """合并并写入 ~/.decision-journal/config.json（权限 0600）。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(_read_config_file())
    for k, v in partial.items():
        if v is not None:
            merged[k] = v
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return merged


def _collect_cli_config(args: argparse.Namespace) -> dict:
    """从解析后的 args 收集 CLI 提供的配置（仅非空）。

    v0.7.0 起 weather_key（高德 Key）为主字段；
    --weather-appsecret 兼容旧版，自动并入 weather_key。
    """
    weather_key = getattr(args, "weather_key", None) or getattr(args, "weather_appsecret", None)
    return {
        "llm_api_key": getattr(args, "api_key", None),
        "llm_model": getattr(args, "llm_model", None),
        "llm_base_url": getattr(args, "llm_base_url", None),
        "weather_key": weather_key,
        "weather_base_url": getattr(args, "weather_base_url", None),
        "weather_appsecret": getattr(args, "weather_appsecret", None),
        "weather_city": getattr(args, "weather_city", None),
    }


def _build_request_overrides(cli_config: dict) -> dict:
    """把 CLI 配置映射为 /api/chat 请求体字段。"""
    return {REQ_FIELD_MAP[k]: v for k, v in cli_config.items() if v}


def cmd_chat(args: argparse.Namespace, client: httpx.Client) -> int:
    """调用 /api/chat，返回决策简报。"""
    if not args.question:
        print("错误：chat 动作需要 --question 参数", file=sys.stderr)
        return 2

    # --save-config：先把 CLI 配置持久化到 ~/.decision-journal/config.json
    if getattr(args, "save_config", False):
        cli_cfg = _collect_cli_config(args)
        if any(cli_cfg.values()):
            _save_config_file(cli_cfg)

    payload: dict = {"question": args.question, "mode": args.mode}
    if getattr(args, "demo", False):
        # 仅本次请求启用 mock；不写入用户偏好，也不调用真实 LLM。
        payload["demoMode"] = True
    payload.update(_build_request_overrides(_collect_cli_config(args)))
    resp = client.post("/api/chat", json=payload)
    resp.raise_for_status()
    data = resp.json()

    # nature 模式输出自然意象简报
    nature = data.get("nature")
    brief = data.get("brief")
    result = data.get("result")
    reply = data.get("reply", "")
    if reply:
        print(reply)
        print("-" * 48)

    if nature:
        _print_json(nature)
    elif isinstance(brief, dict) and brief:
        _print_json(brief)
    elif isinstance(result, dict):
        # random / dialogue 等模式没有 Brief，输出完整 ModeResult，避免只打印 {}。
        _print_json(result)
    else:
        _print_json({})

    # 明确标注真实模型或 Demo/mock，不把示例数据说成真实分析。
    source = ""
    if isinstance(brief, dict):
        source = brief.get("source") or ""
    if not source and isinstance(result, dict):
        source = result.get("source") or result.get("_source") or ""
    if source:
        print(f"结果来源：{source}")

    # 若后端已落库，提示 decisionId（便于后续 decision 动作查看/删除）
    decision_id = data.get("decisionId")
    if decision_id:
        print(f"已落库决策记录 id：{decision_id}")
    return 0


def cmd_archive(args: argparse.Namespace, client: httpx.Client) -> int:
    """调用 /api/archive，列出历史决策。

    后端返回 { ok, list, total, page, pageSize }；CLI 只打印 list 字段。
    """
    resp = client.get("/api/archive")
    resp.raise_for_status()
    data = resp.json()
    items = data.get("list", []) if isinstance(data, dict) else data
    _print_json(items)
    return 0


def cmd_stats(args: argparse.Namespace, client: httpx.Client) -> int:
    """调用 /api/stats，返回统计。"""
    resp = client.get("/api/stats")
    resp.raise_for_status()
    _print_json(resp.json())
    return 0


def cmd_decision(args: argparse.Namespace, client: httpx.Client) -> int:
    """调用 /api/decision/:id，查看或删除单条决策记录。"""
    if not args.id:
        print("错误：decision 动作需要 --id 参数", file=sys.stderr)
        return 2

    if args.delete:
        resp = client.delete(f"/api/decision/{args.id}")
        resp.raise_for_status()
        print(f"已删除决策记录 {args.id}")
        return 0

    resp = client.get(f"/api/decision/{args.id}")
    resp.raise_for_status()
    _print_json(resp.json())
    return 0


def cmd_export(args: argparse.Namespace, client: httpx.Client) -> int:
    """导出单条决策简报（Markdown 或 JSON）。"""
    if not args.id:
        print("错误：export 需要 --id", file=sys.stderr)
        return 2
    resp = client.get(f"/api/decision/{args.id}/export", params={"format": args.format})
    resp.raise_for_status()
    if args.format == "json":
        _print_json(resp.json())
    else:
        print(resp.text)
    return 0


def cmd_config_api(args: argparse.Namespace, client: httpx.Client) -> int:
    """通过后端 /api/config 接口管理配置（落库到 SQLite）。

    - 默认 / --list：GET /api/config 查看脱敏配置
    - --delete：DELETE /api/config 清除所有 API Key
    - --save-to-db：POST /api/config 把 CLI 提供的配置写入 SQLite
    """
    # --save-to-db 优先级高于 --delete 与 --list
    if getattr(args, "save_to_db", False):
        cli_cfg = _collect_cli_config(args)
        if not any(cli_cfg.values()):
            print("错误：config-api --save-to-db 需要至少一个配置参数", file=sys.stderr)
            return 2
        # 后端 ConfigUpdate 字段为 snake_case，与 cli_cfg 键名一致
        payload = {k: v for k, v in cli_cfg.items() if v}
        resp = client.post("/api/config", json=payload)
        resp.raise_for_status()
        print("已保存到 SQLite（脱敏配置如下）")
        _print_json(resp.json())
        return 0

    if getattr(args, "delete", False):
        resp = client.delete("/api/config")
        resp.raise_for_status()
        print("已清除 SQLite 中所有 API Key 配置")
        return 0

    # 默认行为：GET /api/config
    resp = client.get("/api/config")
    resp.raise_for_status()
    _print_json(resp.json())
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    """config 子命令：查看或持久化用户自配 API Key。"""
    cli_cfg = _collect_cli_config(args)

    # 仅查看当前生效配置
    if not getattr(args, "save", False):
        current = dict(_read_config_file())
        for k, env in (
            ("llm_api_key", "DECISION_JOURNAL_LLM_API_KEY"),
            ("llm_model", "DECISION_JOURNAL_LLM_MODEL"),
            ("llm_base_url", "DECISION_JOURNAL_LLM_BASE_URL"),
            ("weather_key", "DECISION_JOURNAL_WEATHER_KEY"),
            ("weather_base_url", "DECISION_JOURNAL_WEATHER_BASE_URL"),
            ("weather_appsecret", "DECISION_JOURNAL_WEATHER_APPSECRET"),  # 兼容旧版
            ("weather_city", "DECISION_JOURNAL_WEATHER_CITY"),
        ):
            env_val = os.environ.get(env)
            if env_val:
                current[k] = env_val
            elif cli_cfg.get(k):
                current[k] = cli_cfg[k]
        # 不回显完整 secret，仅显示是否已配置
        masked = dict(current)
        for k in ("llm_api_key", "weather_key", "weather_appsecret"):
            if masked.get(k):
                masked[k] = "***已配置***"
        print(f"配置文件：{CONFIG_PATH}")
        _print_json(masked)
        return 0

    if not any(cli_cfg.values()):
        print("错误：config --save 需要至少一个配置参数", file=sys.stderr)
        return 2

    merged = _save_config_file(cli_cfg)
    print(f"已保存到 {CONFIG_PATH}（权限 0600）")
    masked = {k: ("***已配置***" if v and k in ("llm_api_key", "weather_key", "weather_appsecret") else v)
              for k, v in merged.items()}
    _print_json(masked)
    return 0


def _add_config_flags(parser: argparse.ArgumentParser) -> None:
    """给 parser 添加 LLM / 天气 API Key 配置参数。

    v0.7.0 起天气服务切换到高德开放平台，主参数为 --weather-key；
    --weather-appsecret 保留为兼容别名（自动并入 weather_key）。
    高德 Key 申请路径：https://lbs.amap.com/dev/key/app
    """
    parser.add_argument("--api-key", default=None, help="LLM API Key")
    parser.add_argument("--llm-model", default=None, help="LLM 模型名（如 gpt-4o-mini）")
    parser.add_argument(
        "--llm-base-url", default=None,
        help="LLM base url（OpenAI 兼容，如 https://api.openai.com/v1 或完整 .../chat/completions）",
    )
    parser.add_argument(
        "--weather-key", default=None,
        help="高德开放平台 Key（10 万次/日免费，申请：https://lbs.amap.com/dev/key/app）",
    )
    parser.add_argument(
        "--weather-base-url", default=None,
        help="高德天气接口地址，如 https://restapi.amap.com/v3/weather/weatherInfo",
    )
    parser.add_argument(
        "--weather-appsecret", default=None,
        help="（已弃用，兼容旧版）等价于 --weather-key",
    )
    parser.add_argument("--weather-city", default=None, help="天气查询城市（默认北京）")


def build_parser() -> argparse.ArgumentParser:
    """构造主参数解析器（chat / archive / stats / decision / config-api）。"""
    parser = argparse.ArgumentParser(
        prog="decision_journal",
        description="别纠结决策辅助 CLI - 辅助人做选择，不替代人做决定",
    )
    parser.add_argument("--question", "-q", help="决策问题（chat 动作必填）")
    parser.add_argument(
        "--mode", "-m",
        default="auto",
        choices=MODES,
        help="决策模式，默认 auto",
    )
    parser.add_argument(
        "--action", "-a",
        default="chat",
        choices=["chat", "archive", "stats", "decision", "config-api", "export", "stop"],
        help="执行动作，默认 chat。decision: 查看或删除单条决策（需 --id）；export: 导出决策简报（需 --id）；config-api: 通过后端管理 SQLite 配置；stop: 停止本机自动启动的后端",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"后端 API 地址，默认 {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="不自动启动本地后端；后端未运行时直接报错",
    )
    parser.add_argument(
        "--backend-timeout",
        type=float,
        default=BACKEND_START_TIMEOUT,
        help=f"自动启动后端的等待秒数，默认 {BACKEND_START_TIMEOUT:g}",
    )
    parser.add_argument(
        "--save-config",
        action="store_true",
        help="把本次 CLI 提供的配置持久化到 ~/.decision-journal/config.json",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="仅本次 chat 使用 Demo/mock 示例数据，不调用真实 LLM，也不修改偏好",
    )
    parser.add_argument(
        "--id",
        default=None,
        help="决策记录 id（decision 动作必填）",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="删除：decision 动作删除单条记录；config-api 动作清除 SQLite 中所有 API Key",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="查看脱敏配置（config-api 动作；默认行为，显式传亦可）",
    )
    parser.add_argument(
        "--format",
        default="md",
        choices=["md", "json"],
        help="export 动作的格式，默认 md",
    )
    parser.add_argument(
        "--save-to-db",
        action="store_true",
        help="把 CLI 提供的配置保存到 SQLite（config-api 动作，需配合 --api-key 等参数）",
    )
    _add_config_flags(parser)
    return parser


def build_config_parser() -> argparse.ArgumentParser:
    """构造 config 子命令解析器。"""
    parser = argparse.ArgumentParser(
        prog="decision_journal config",
        description="查看或保存用户自配 API Key（写入 ~/.decision-journal/config.json，权限 0600）",
    )
    parser.add_argument("--save", action="store_true", help="保存到配置文件")
    _add_config_flags(parser)
    return parser


def _secret_values(args: argparse.Namespace) -> list:
    """收集本次 CLI/环境中的敏感值，用于错误输出脱敏。"""
    values = []
    for name in ("api_key", "weather_key", "weather_appsecret"):
        value = getattr(args, name, None)
        if value:
            values.append(str(value))
    for env_name in (
        "DECISION_JOURNAL_LLM_API_KEY",
        "DECISION_JOURNAL_WEATHER_KEY",
        "DECISION_JOURNAL_WEATHER_APPSECRET",
    ):
        value = os.environ.get(env_name)
        if value:
            values.append(value)
    return values


def _redact_secrets(text: str, secrets: Optional[list] = None) -> str:
    """从错误文本中移除已知 secret 与常见 Key 形态，避免转发上游响应时泄露。"""
    redacted = text or ""
    for secret in secrets or []:
        if secret and len(secret) >= 4:
            redacted = redacted.replace(secret, "***")
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{4,}", r"\1***", redacted)
    redacted = re.sub(r"(?i)((?:api[_-]?key|token|secret|authorization)\s*[:=]\s*[\"']?)[^\"'\s,}]+", r"\1***", redacted)
    redacted = re.sub(r"sk-[A-Za-z0-9._\-]{4,}", "sk-***", redacted)
    return redacted


def main() -> int:
    """入口。支持 config 子命令与默认 chat/archive/stats 流程。"""
    argv = sys.argv[1:]
    # config 子命令：python decision_journal.py config --api-key sk-xxx --save
    if argv and argv[0] == "config":
        args = build_config_parser().parse_args(argv[1:])
        return cmd_config(args)

    args = build_parser().parse_args(argv)
    if args.action == "stop":
        return cmd_stop_backend(args.base_url)
    if not ensure_backend(args.base_url, enabled=not args.no_autostart, timeout=args.backend_timeout):
        return 3
    try:
        with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
            if args.action == "chat":
                return cmd_chat(args, client)
            if args.action == "archive":
                return cmd_archive(args, client)
            if args.action == "stats":
                return cmd_stats(args, client)
            if args.action == "decision":
                return cmd_decision(args, client)
            if args.action == "config-api":
                return cmd_config_api(args, client)
            if args.action == "export":
                return cmd_export(args, client)
    except httpx.ConnectError:
        print(
            "无法连接本地后端。请确认 backend/main.py 已启动，或重试让 CLI 自动启动。",
            file=sys.stderr,
        )
        return 3
    except httpx.HTTPStatusError as exc:
        detail = _redact_secrets((exc.response.text or "").strip(), _secret_values(args))
        print(
            f"后端返回 {exc.response.status_code}：{detail[:300] or exc.response.reason_phrase}",
            file=sys.stderr,
        )
        return 2
    except httpx.TimeoutException:
        print(f"后端响应超时；请确认本机负载或后端日志：{BACKEND_LOG}", file=sys.stderr)
        return 3
    return 1


if __name__ == "__main__":
    sys.exit(main())
