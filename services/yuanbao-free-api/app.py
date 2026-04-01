"""YuanBao API Proxy 主应用"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from src.routers import chat, upload
from src.services.browser import browser_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_login_task: asyncio.Task | None = None


async def _ensure_login_task(*, force: bool = False) -> tuple[asyncio.Task, bool]:
    global _login_task
    if _login_task is not None and not _login_task.done():
        if not force:
            return _login_task, False
        _login_task.cancel()
        await asyncio.gather(_login_task, return_exceptions=True)
        _login_task = None
        await browser_manager.close()
    _login_task = asyncio.create_task(browser_manager.login(force=force))
    return _login_task, True


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期事件处理器"""
    logger.info("[Startup] 正在初始化浏览器...")
    try:
        _, started = await _ensure_login_task(force=False)
        logger.info("[Startup] 浏览器登录任务%s启动", "已" if started else "复用")
    except Exception as e:
        logger.error(f"[Startup] 浏览器初始化失败: {e}")

    yield

    logger.info("[Shutdown] 正在关闭浏览器..")
    try:
        global _login_task
        if _login_task is not None and not _login_task.done():
            _login_task.cancel()
            await asyncio.gather(_login_task, return_exceptions=True)
        _login_task = None
        await browser_manager.close()
        logger.info("[Shutdown] 浏览器已关闭")
    except Exception as e:
        logger.error(f"[Shutdown] 关闭浏览器失败: {e}")


app = FastAPI(title="YuanBao API Proxy", version="1.0.0", lifespan=lifespan)

app.include_router(chat.router)
app.include_router(upload.router)


@app.get("/fsv/status")
async def fsv_status():
    status = dict(browser_manager.status())
    qrcode_path = Path(str(status.get("qrcode_path") or "")).expanduser()
    status["qrcode_exists"] = bool(qrcode_path and qrcode_path.exists())
    status["login_task_running"] = bool(_login_task is not None and not _login_task.done())
    return status


@app.post("/fsv/login")
async def fsv_login():
    task, started = await _ensure_login_task(force=True)
    status = dict(browser_manager.status())
    qrcode_path = Path(str(status.get("qrcode_path") or "")).expanduser()
    status["qrcode_exists"] = bool(qrcode_path and qrcode_path.exists())
    status["task_started"] = started
    status["task_done"] = task.done()
    status["login_task_running"] = not task.done()
    status["success"] = bool(
        status.get("logged_in") or status.get("qrcode_exists") or status.get("login_in_progress")
    )
    if task.done():
        try:
            result = task.result()
        except Exception as exc:
            status["success"] = False
            status["message"] = str(exc)
        else:
            if isinstance(result, dict):
                status.update(result)
    else:
        status.setdefault("message", "login_started")
    return status


@app.post("/fsv/logout")
async def fsv_logout():
    global _login_task
    if _login_task is not None and not _login_task.done():
        _login_task.cancel()
        await asyncio.gather(_login_task, return_exceptions=True)
    _login_task = None
    await browser_manager.logout()
    status = dict(browser_manager.status())
    status["success"] = True
    status["message"] = "logged_out"
    return status


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
