"""
server.py  –  Komatsu Watcher Bot  •  Web Dashboard Backend
============================================================

Runs the FastAPI web server + APScheduler in one process.

  python3 server.py
  → http://localhost:8000

The scheduler fires a watcher check every N minutes (config.yaml).
The frontend communicates via the /api/* routes below.
"""

import asyncio
import io
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

# Windows UTF-8 fix
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import yaml
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
from agents.informer import InformerAgent
from agents.watcher import WatcherAgent
from agents.rba_watcher import RBAWatcherAgent
from bot.enquiry import fill_enquiry_form
from db.database import Database

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("komatsu_bot.log", encoding="utf-8"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

app = FastAPI(title="Komatsu Watcher Bot", version="1.0.0", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Shared state  –  Komatsu (tradeearthmovers)
# --------------------------------------------------------------------------

_config: dict = {}
_db: Optional[Database] = None
_scheduler = None
_scheduler_running = False
_last_run: Optional[str] = None
_last_new_count: int = 0
_check_in_progress = False

# --------------------------------------------------------------------------
# Shared state  –  RB Auction
# --------------------------------------------------------------------------

_rba_scheduler = None
_rba_scheduler_running = False
_rba_last_run: Optional[str] = None
_rba_last_new_count: int = 0
_rba_check_in_progress = False


def get_config() -> dict:
    global _config
    if not _config:
        cfg_path = Path("config.yaml")
        if cfg_path.exists():
            with cfg_path.open(encoding="utf-8") as fh:
                _config = yaml.safe_load(fh) or {}
    return _config


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


# --------------------------------------------------------------------------
# Komatsu watch cycle
# --------------------------------------------------------------------------

def _run_check_sync() -> list:
    global _last_run, _last_new_count, _check_in_progress
    _check_in_progress = True
    try:
        config = get_config()
        db = get_db()
        informer = InformerAgent(config)
        watcher = WatcherAgent(config, db, informer)
        findings = watcher.run()
        _last_run = datetime.now().isoformat(timespec="seconds")
        _last_new_count = len(findings)
        return findings
    finally:
        _check_in_progress = False


def start_scheduler():
    global _scheduler, _scheduler_running
    from apscheduler.schedulers.background import BackgroundScheduler

    interval = int(get_config().get("watcher", {}).get("interval_minutes", 60))
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(_run_check_sync, "interval", minutes=interval, id="watch_job")
    _scheduler.start()
    _scheduler_running = True
    logger.info("Komatsu scheduler started – every %d min", interval)
    # Run once immediately
    threading.Thread(target=_run_check_sync, daemon=True).start()


# --------------------------------------------------------------------------
# RBA watch cycle
# --------------------------------------------------------------------------

def _run_rba_check_sync() -> list:
    global _rba_last_run, _rba_last_new_count, _rba_check_in_progress
    _rba_check_in_progress = True
    try:
        config = get_config()
        db = get_db()
        informer = InformerAgent(config)
        watcher = RBAWatcherAgent(config, db, informer)
        findings = watcher.run()
        _rba_last_run = datetime.now().isoformat(timespec="seconds")
        _rba_last_new_count = len(findings)
        return findings
    finally:
        _rba_check_in_progress = False


def start_rba_scheduler():
    global _rba_scheduler, _rba_scheduler_running
    from apscheduler.schedulers.background import BackgroundScheduler

    config = get_config()
    rba_cfg = config.get("rba_watcher", {})
    if not rba_cfg.get("enabled", True):
        logger.info("RBA Watcher disabled in config.yaml – skipping")
        return

    interval = int(rba_cfg.get("interval_minutes", 60))
    _rba_scheduler = BackgroundScheduler(timezone="UTC")
    _rba_scheduler.add_job(
        _run_rba_check_sync, "interval", minutes=interval, id="rba_watch_job"
    )
    _rba_scheduler.start()
    _rba_scheduler_running = True
    logger.info("RBA scheduler started – every %d min", interval)
    # Run once immediately
    threading.Thread(target=_run_rba_check_sync, daemon=True).start()


# --------------------------------------------------------------------------
# Pydantic models
# --------------------------------------------------------------------------

class EnquiryRequest(BaseModel):
    listing_id: str
    phone: str
    email: str
    message: str = ""
    auto_submit: bool = False


class TargetRequest(BaseModel):
    model: str
    type: str = ""
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    price_min: Optional[int] = None
    price_max: Optional[int] = None


# --------------------------------------------------------------------------
# API routes  –  Komatsu
# --------------------------------------------------------------------------

@app.get("/api/status")
def api_status():
    config = get_config()
    db = get_db()
    seen = db.get_all_seen()
    history = db.get_recent_runs(10)
    targets = config.get("targets", [])
    interval = int(config.get("watcher", {}).get("interval_minutes", 60))

    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    new_24h = sum(1 for s in seen if s.get("first_seen", "") >= cutoff)

    next_run = None
    if _scheduler:
        job = _scheduler.get_job("watch_job")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()

    return {
        "scheduler_running": _scheduler_running,
        "check_in_progress": _check_in_progress,
        "last_run": _last_run,
        "last_new_count": _last_new_count,
        "next_run": next_run,
        "interval_minutes": interval,
        "targets": targets,
        "total_listings": len(seen),
        "new_24h": new_24h,
        "total_checks": len(history),
        "recent_runs": history[:5],
    }


@app.get("/api/listings")
def api_listings(limit: int = 200):
    db = get_db()
    seen = db.get_all_seen()
    return {"listings": seen[:limit], "total": len(seen)}


@app.get("/api/history")
def api_history(limit: int = 20):
    db = get_db()
    return {"runs": db.get_recent_runs(limit)}


@app.post("/api/check")
def api_check(background_tasks: BackgroundTasks):
    if _check_in_progress:
        return {"message": "Check already in progress", "started": False}
    background_tasks.add_task(_run_check_sync)
    return {"message": "Check started", "started": True}


@app.post("/api/enquiry")
async def api_enquiry(req: EnquiryRequest):
    db = get_db()
    record = db.get_listing(req.listing_id)
    if not record:
        raise HTTPException(status_code=404, detail="Listing not found in database")

    config = get_config()
    name = (config.get("enquiry") or {}).get("company_name", "YANTRA LIVE")
    listing = record["data"]

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: asyncio.run(
                fill_enquiry_form(
                    detail_url=listing["detail_url"],
                    name=name,
                    phone=req.phone,
                    email=req.email,
                    message=req.message,
                    listing_info=listing,
                    headless=False,
                    auto_submit=req.auto_submit,
                )
            ),
        )
        return {"success": result, "listing": listing["title"]}
    except Exception as exc:
        logger.exception("Enquiry failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/config")
def api_config():
    return get_config()


# --------------------------------------------------------------------------
# API routes  –  RB Auction
# --------------------------------------------------------------------------

@app.get("/api/rba/status")
def api_rba_status():
    config  = get_config()
    db      = get_db()
    seen    = db.get_all_seen_rba()
    history = db.get_rba_recent_runs(10)
    rba_cfg = config.get("rba_watcher", {})

    from datetime import timedelta
    cutoff  = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    new_24h = sum(1 for s in seen if s.get("first_seen", "") >= cutoff)

    next_run = None
    if _rba_scheduler:
        job = _rba_scheduler.get_job("rba_watch_job")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()

    return {
        "enabled":           rba_cfg.get("enabled", True),
        "scheduler_running": _rba_scheduler_running,
        "check_in_progress": _rba_check_in_progress,
        "last_run":          _rba_last_run,
        "last_new_count":    _rba_last_new_count,
        "next_run":          next_run,
        "interval_minutes":  int(rba_cfg.get("interval_minutes", 60)),
        "total_lots":        len(seen),
        "new_24h":           new_24h,
        "total_checks":      len(history),
        "recent_runs":       history[:5],
    }


@app.get("/api/rba/listings")
def api_rba_listings(limit: int = 200):
    db   = get_db()
    seen = db.get_all_seen_rba()
    return {"listings": seen[:limit], "total": len(seen)}


@app.get("/api/rba/history")
def api_rba_history(limit: int = 20):
    db = get_db()
    return {"runs": db.get_rba_recent_runs(limit)}


@app.post("/api/rba/check")
def api_rba_check(background_tasks: BackgroundTasks):
    if _rba_check_in_progress:
        return {"message": "RBA check already in progress", "started": False}
    background_tasks.add_task(_run_rba_check_sync)
    return {"message": "RBA check started", "started": True}


@app.post("/api/rba/toggle")
def api_rba_toggle():
    """Enable or disable the RBA watcher in config.yaml."""
    cfg = get_config()
    rba_cfg = cfg.get("rba_watcher", {})
    rba_cfg["enabled"] = not rba_cfg.get("enabled", True)
    cfg["rba_watcher"] = rba_cfg
    save_config(cfg)
    return {"enabled": rba_cfg["enabled"]}


# --------------------------------------------------------------------------
# Target management
# --------------------------------------------------------------------------

def save_config(cfg: dict):
    """Write config back to disk and update the in-memory cache."""
    global _config
    cfg_path = Path("config.yaml")
    with cfg_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    _config = cfg


@app.post("/api/targets")
def api_add_target(req: TargetRequest):
    cfg = get_config()
    targets = list(cfg.get("targets", []))
    model_name = req.model.strip().upper()
    if not model_name:
        raise HTTPException(status_code=400, detail="Model name is required")
    if any(t.get("model", "").upper() == model_name for t in targets):
        raise HTTPException(status_code=400, detail=f"Target '{model_name}' already exists")
    new_target: dict = {"model": model_name}
    if req.type:        new_target["type"] = req.type
    if req.year_min:    new_target["year_min"] = req.year_min
    if req.year_max:    new_target["year_max"] = req.year_max
    if req.price_min:   new_target["price_min"] = req.price_min
    if req.price_max:   new_target["price_max"] = req.price_max
    targets.append(new_target)
    cfg["targets"] = targets
    save_config(cfg)
    logger.info("Target added: %s", new_target)

    # Seed existing listings from BOTH sources in background
    def _seed():
        try:
            db = get_db()
            informer = InformerAgent(get_config())

            # Seed Komatsu listings
            komatsu_watcher = WatcherAgent(get_config(), db, informer)
            count_k = komatsu_watcher.seed_target(new_target)
            logger.info("Komatsu seed for %s – %d listing(s)", model_name, count_k)

            # Seed RBA lots
            rba_watcher = RBAWatcherAgent(get_config(), db, informer)
            count_r = rba_watcher.seed_target(new_target)
            logger.info("RBA seed for %s – %d lot(s)", model_name, count_r)

        except Exception as exc:
            logger.exception("Background seed failed for %s: %s", model_name, exc)

    threading.Thread(target=_seed, daemon=True).start()
    return {"success": True, "targets": targets, "added": new_target}


@app.delete("/api/targets/{index}")
def api_delete_target(index: int):
    cfg = get_config()
    targets = list(cfg.get("targets", []))
    if index < 0 or index >= len(targets):
        raise HTTPException(status_code=404, detail="Target index out of range")
    removed = targets.pop(index)
    cfg["targets"] = targets
    save_config(cfg)

    db = get_db()
    deleted_k = db.delete_by_model(removed["model"])
    deleted_r = db.delete_rba_by_model(removed["model"])
    logger.info(
        "Target removed: %s  |  Komatsu: %d  |  RBA: %d listings purged",
        removed, deleted_k, deleted_r,
    )
    return {
        "success": True, "targets": targets, "removed": removed,
        "listings_deleted": deleted_k, "rba_lots_deleted": deleted_r,
    }


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------

_frontend = Path(__file__).parent / "frontend"
if _frontend.exists():
    app.mount("/static", StaticFiles(directory=str(_frontend)), name="static")


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
def root():
    index = _frontend / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Komatsu Bot API running. Frontend not found at frontend/index.html"}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    # Start both schedulers in background before uvicorn takes over
    threading.Thread(target=start_scheduler, daemon=True).start()
    threading.Thread(target=start_rba_scheduler, daemon=True).start()

    print("\n" + "=" * 60)
    print("  🤖  KOMATSU + RBA WATCHER BOT  –  Web Dashboard")
    print("  Open: http://localhost:8000")
    print("  API docs: http://localhost:8000/api/docs")
    print("=" * 60 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False, log_level="warning")
