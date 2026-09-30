"""FastAPI entrypoint —— 冒險者公會大廳後端。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("guild")

from . import activity_log, db, dispatch, muse, reception, uploads as uploads_mod
from .routers import (
    activity, adventurers, commissions, evolutions, guilds,
    muse as muse_router, recall as recall_router,
    reception as reception_router, reports,
    training as training_router, uploads as uploads_router,
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
    # 重啟修復:被中斷的「working」派遣(重啟殺掉了記憶體裡的任務,DB 卻還停在
    # working)重設為 blocked,並留一則事件說明 —— 使用者即可重新派遣。
    for cid in db.reset_working_to_blocked():
        db.add_event(cid, "system",
                     "⚠️ 後端重啟,原本執行中的派遣已中斷。已轉為待指示,可按「▶️ 繼續做」重新派遣。")
        log.info("重啟修復:委託 #%s working → blocked", cid)
    # 重啟後若還有排隊中的委託,續跑派遣佇列(晚上掛著醒來收成)
    if db.queued_ids():
        dispatch.ensure_worker()
        log.info("派遣佇列 worker 已續跑(尚有 %d 張排隊)", len(db.queued_ids()))
    # 公會夜巡:啟用時起常駐 loop(自我把關開關/時段/預算)
    if muse.load_config().enabled:
        muse.ensure_loop()
        log.info("公會夜巡 loop 已啟動")


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
app.include_router(uploads_router.router)
app.include_router(muse_router.router)

# 委託附圖:靜態服務 uploads/(不存在先建,避免 mount 失敗)
uploads_mod.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_mod.UPLOAD_DIR)), name="uploads")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
