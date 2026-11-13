"""别纠结决策辅助 - FastAPI 应用入口。

启动流程：
  1. 初始化 SQLite（decision_journal.db）
  2. 注册 6 个 API 路由
  3. 挂载前端静态资源（frontend/）
  4. 监听 8010 端口

启动方式：
    python main.py
    或 uvicorn main:app --reload --port 8010
"""

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# 确保 backend/ 在 sys.path 中（用于 routes/services 的相对导入）
sys.path.insert(0, str(Path(__file__).parent))

from db import init_db  # noqa: E402
from routes import archive, chat, config_api, decision, modes, stats, tts  # noqa: E402

# 前端静态资源目录（decision-journal/frontend/）
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(
    title="别纠结决策辅助 API",
    description="辅助人做选择，不替代人做决定",
    version="0.10.0",
)


@app.on_event("startup")
def _startup_init_db() -> None:
    """启动时初始化数据库（含旧 choice.db 迁移）。

    放在 startup 而不是模块导入阶段：测试导入本模块时不会触碰真实数据库。
    """
    init_db()

# ─── API 路由 ───────────────────────────────────────────────────
app.include_router(chat.router)
app.include_router(modes.router)
app.include_router(decision.router)
app.include_router(archive.router)
app.include_router(stats.router)
app.include_router(config_api.router)
app.include_router(tts.router)


# ─── 健康检查 ───────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict:
    """健康检查。"""
    return {"name": "别纠结 API", "status": "ok", "version": "0.10.0"}


# ─── 前端静态资源 ──────────────────────────────────────────────
# 仅当 frontend/ 目录存在时挂载（CLI-only 模式下不需要前端）
if FRONTEND_DIR.exists():
    styles_dir = FRONTEND_DIR / "styles"
    scripts_dir = FRONTEND_DIR / "scripts"
    assets_dir = FRONTEND_DIR / "assets"

    if styles_dir.exists():
        app.mount("/styles", StaticFiles(directory=str(styles_dir)), name="styles")
    if scripts_dir.exists():
        app.mount("/scripts", StaticFiles(directory=str(scripts_dir)), name="scripts")
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def serve_index() -> FileResponse:
        """根路径返回前端首页。"""
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str) -> FileResponse:
        """SPA fallback：非 API 路径返回 index.html。"""
        # 不拦截 /api/ 开头的路径：未注册的 API 一律返回 JSON 404
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # 尝试返回静态文件；解析后必须仍位于 frontend/ 内，避免路径穿越
        frontend_root = FRONTEND_DIR.resolve()
        try:
            candidate = (FRONTEND_DIR / full_path).resolve()
        except (OSError, ValueError):
            return FileResponse(str(FRONTEND_DIR / "index.html"))
        if candidate.is_file() and candidate.is_relative_to(frontend_root):
            return FileResponse(str(candidate))
        # fallback 到 index.html
        return FileResponse(str(FRONTEND_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010)
