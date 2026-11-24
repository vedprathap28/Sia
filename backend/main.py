"""
Sia AI — Entry Point
Run from the Sia/ root folder:
    python -m backend.main
"""
from __future__ import annotations
import threading
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.config import settings
from backend.core.logger import get_logger

log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("━" * 48)
    log.info("  Sia AI — Starting")
    log.info("━" * 48)

    settings.validate()

    # ── v4 agent engine ──────────────────────────────────────────────────
    from pathlib import Path
    from backend.database.db import init_db
    init_db()
    Path(settings.WORKSPACE_DIR).mkdir(parents=True, exist_ok=True)
    try:
        from backend.memory.store import memory_store
        n = 0
        root = Path(__file__).resolve().parents[1]
        # data/ved_facts.json, plus the old misplaced copy one folder above the project (pre-v4 path bug)
        for f in {Path(settings.DB_PATH).parent / "ved_facts.json", root.parent / "data" / "ved_facts.json"}:
            n += memory_store.migrate_json_facts(f)
        if n:
            log.info("Imported %d facts from ved_facts.json into the memory table", n)
    except Exception as e:
        log.warning("Fact migration skipped: %s", e)
    from backend.tools.registry import load_all_tools
    load_all_tools()
    from backend.task_manager.manager import task_manager
    await task_manager.startup()
    resumed = await task_manager.recover()
    if resumed:
        log.info("Resumed %d interrupted task(s)", resumed)
    from backend.background_workers.scheduler import scheduler
    scheduler.start()
    log.info("Engine: %s | planner %s/%s | chat %s/%s", settings.SIA_ENGINE, settings.PLANNER_PROVIDER,
             settings.PLANNER_MODEL, settings.CHAT_PROVIDER, settings.CHAT_MODEL)

    # Voice controller starts ONLY when user toggles it from frontend
    log.info("Voice ready — toggle from frontend to activate")

    log.info("✅  Ready at http://localhost:%d", settings.PORT)
    log.info("    Swagger docs: http://localhost:%d/docs", settings.PORT)
    log.info("    Frontend:     http://localhost:5173")

    yield

    # Cleanup
    log.info("Sia shutting down...")
    try:
        from backend.background_workers.scheduler import scheduler
        scheduler.shutdown()
        from backend.task_manager.manager import task_manager
        await task_manager.shutdown()
        from backend.tools.browser.service import browser_service
        if browser_service._ctx is not None:
            await browser_service.run("close")
    except Exception:
        pass
    try:
        from backend.voice.voice_controller import voice_controller
        voice_controller.stop()
    except Exception:
        pass
    try:
        from backend.llm.llm_service import llm_service
        await llm_service.close()
    except Exception:
        pass


app = FastAPI(
    title="Sia AI",
    version="4.0.0",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:4173",
        "http://localhost:3000",
        *settings.CORS_ORIGINS,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.api.routes import router
from backend.api.agent_routes import router as agent_router
app.include_router(router, prefix="/api")
app.include_router(agent_router, prefix="/api")


if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        log_level="warning",
        access_log=False,
    )
