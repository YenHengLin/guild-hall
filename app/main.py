"""FastAPI entrypoint —— 冒險者公會大廳後端。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("guild")

from . import activity_log, db, reception
from .routers import (
    activity, adventurers, commissions, evolutions, guilds,
    recall as recall_router, reception as reception_router, reports,
    training as training_router,
)

app = FastAPI(title="冒險者公會大廳", version="0.2.0")

_worker_task: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
    # 啟動時輪替活動 log:主檔超過門檻就把舊資料按月歸檔(存檔是保底,失敗只降級不阻斷)
    try:
        stats = activity_log.rotate()
        if stats["rotated"]:
            log.info("活動 log 已輪替:歸檔 %d 行,主檔留 %d 行",
                     stats["archived"], stats["kept"])
    except Exception as exc:  # noqa: BLE001 輪替絕不可拖垮啟動
        log.warning("活動 log 輪替略過: %s", exc)
    global _worker_task
    _worker_task = asyncio.create_task(reception.worker_loop())
    log.info("櫃台佇列與工人已啟動")


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _worker_task:
        _worker_task.cancel()

# 開發期允許 Vite dev server 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(guilds.router)
app.include_router(adventurers.router)
app.include_router(reports.router)
app.include_router(reception_router.router)
app.include_router(evolutions.router)
app.include_router(commissions.router)
app.include_router(recall_router.router)
app.include_router(activity.router)
app.include_router(training_router.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
